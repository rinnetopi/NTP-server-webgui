#!/usr/bin/env python3
"""Flask web UI/API for managing a Raspberry Pi NTP server's network settings."""

import json
import os
import re
import secrets
import subprocess
import tempfile
import time

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)


IP_REGEX = re.compile(
    r"^((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
    r"(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
)
SSID_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.\s]{1,32}$")
ETHERNET_INTERFACES = ("eth0", "eth1")
CMD_TIMEOUT_SECONDS = 30
LAN_REFRESH_HELPER = "/usr/local/sbin/ntp-webgui-lan-refresh"

RUNTIME_ENV_PATH = os.getenv("RUNTIME_ENV_PATH", "/opt/ntp-server-webgui/runtime.env")
INTERFACE_KEY_MAP = {
    "wan": "WAN_CONNECTION_NAME",
    "lan": "LAN_CONNECTION_NAME",
    "wifi": "WIFI_CONNECTION_NAME",
}


def env_flag(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


SANDBOX_MODE = env_flag("SANDBOX_MODE", default=False)

# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
# Credentials live outside the app directory so re-deploys keep them
# (systemd StateDirectory=ntp-webgui -> /var/lib/ntp-webgui, owned by the service user).
STATE_DIR = os.getenv("STATE_DIRECTORY", "/var/lib/ntp-webgui").split(":")[0]
CREDENTIALS_PATH = os.getenv("CREDENTIALS_PATH", os.path.join(STATE_DIR, "credentials.json"))
SECRET_KEY_PATH = os.getenv("SECRET_KEY_PATH", os.path.join(STATE_DIR, "secret_key"))
DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"
MIN_PASSWORD_LENGTH = 6


def write_private_file(path, content):
    """Atomically write a file readable only by the service user."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def load_secret_key():
    try:
        with open(SECRET_KEY_PATH, "r", encoding="utf-8") as handle:
            key = handle.read().strip()
            if key:
                return key
    except OSError:
        pass
    key = secrets.token_hex(32)
    try:
        write_private_file(SECRET_KEY_PATH, key + "\n")
    except OSError:
        pass  # sessions then only last until the service restarts
    return key


def load_credentials():
    """Return {'username', 'password_hash'}; creates admin/admin on first use."""
    try:
        with open(CREDENTIALS_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("username") and data.get("password_hash"):
            return data
    except (OSError, ValueError):
        pass
    data = {
        "username": DEFAULT_USERNAME,
        "password_hash": generate_password_hash(DEFAULT_PASSWORD),
    }
    try:
        save_credentials(data)
    except OSError as error:
        # e.g. running on a dev machine without /var/lib/ntp-webgui: keep defaults in memory
        app.logger.warning("Could not save credentials to %s: %s", CREDENTIALS_PATH, error)
    return data


def save_credentials(data):
    write_private_file(CREDENTIALS_PATH, json.dumps(data, indent=2) + "\n")


app.config.update(
    SECRET_KEY=load_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Strict",
)

NTP_SERVICE_NAMES = [
    name.strip() for name in os.getenv("NTP_SERVICE_NAMES", "chrony").split(",") if name.strip()
]


# --------------------------------------------------------------------------- #
# Command execution
# --------------------------------------------------------------------------- #
def mock_wifi_networks():
    return [
        {"ssid": "Home_Lab_5G", "signal": "98", "security": "WPA2"},
        {"ssid": "Pi_Setup_AP", "signal": "75", "security": "WPA2"},
    ]


def run_cmd_sandbox(cmd_list):
    if cmd_list[:2] == ["chronyc", "tracking"]:
        return True, "Reference ID    : 127.127.1.1\nStratum         : 2"

    if cmd_list[:6] == ["sudo", "nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device"]:
        output = "\n".join(
            f"{network['ssid']}:{network['signal']}:{network['security']}"
            for network in mock_wifi_networks()
        )
        return True, output

    return True, "sandbox-ok"


def run_cmd(cmd_list):
    """Run a system command and return (success, stdout_or_stderr)."""
    if SANDBOX_MODE:
        return run_cmd_sandbox(cmd_list)

    try:
        result = subprocess.run(
            cmd_list,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
            timeout=CMD_TIMEOUT_SECONDS,
        )
        return True, result.stdout.strip()
    except subprocess.CalledProcessError as error:
        return False, (error.stderr or "").strip() or (error.stdout or "").strip() or str(error)
    except subprocess.TimeoutExpired:
        return False, f"Command timed out: {' '.join(cmd_list)}"
    except FileNotFoundError as error:
        return False, str(error)


# --------------------------------------------------------------------------- #
# Interface role mapping (which device is WAN / LAN / Wi-Fi)
# --------------------------------------------------------------------------- #
def load_runtime_env(path):
    runtime = {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                runtime[key.strip()] = value.strip()
    except OSError:
        return {}
    return runtime


def detect_wan_interface():
    """Return the Ethernet device that carries the default route, if any."""
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"], capture_output=True, text=True, timeout=10
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None

    match = re.search(r"\bdev\s+(\S+)", result.stdout)
    if match and match.group(1) in ETHERNET_INTERFACES:
        return match.group(1)
    return None


def build_interface_roles():
    """Interface roles from runtime.env, then environment, then defaults/detection."""
    runtime = load_runtime_env(RUNTIME_ENV_PATH)

    def pick(role, default):
        key = INTERFACE_KEY_MAP[role]
        return runtime.get(key) or os.getenv(key) or default

    wan_if = pick("wan", "eth0")
    if not runtime.get(INTERFACE_KEY_MAP["wan"]) and not SANDBOX_MODE:
        wan_if = detect_wan_interface() or wan_if

    return {"wan": wan_if, "lan": pick("lan", "eth1"), "wifi": pick("wifi", "wlan0")}


def persist_interface_roles(roles):
    runtime = load_runtime_env(RUNTIME_ENV_PATH)
    for role, key in INTERFACE_KEY_MAP.items():
        runtime[key] = roles[role]

    lines = [f"{key}={runtime[key]}" for key in sorted(runtime)]
    os.makedirs(os.path.dirname(RUNTIME_ENV_PATH), exist_ok=True)
    with open(RUNTIME_ENV_PATH, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def validate_wan_lan_swap(wan_name, lan_name):
    if wan_name == lan_name:
        return False, "WAN and LAN interfaces must be different."

    if {wan_name, lan_name} != set(ETHERNET_INTERFACES):
        return False, "WAN/LAN mapping must be one eth0 and one eth1."

    if not SANDBOX_MODE:
        for role, name in (("WAN", wan_name), ("LAN", lan_name)):
            try:
                result = subprocess.run(
                    ["ip", "link", "show", name], capture_output=True, text=True, timeout=5
                )
            except (subprocess.SubprocessError, FileNotFoundError):
                continue
            if result.returncode != 0:
                return False, f"{role} interface {name} does not exist."

    return True, ""


INTERFACE_ROLES = build_interface_roles()


def role_device(role):
    return INTERFACE_ROLES[role]


def resolve_connection(device):
    """Map a device (eth0, wlan0...) to the UUID of its NetworkManager profile.

    nmcli 'con modify/up' needs a profile, not a device name. Profile names are not
    unique (e.g. two profiles both called 'eth1'), so the UUID is used: first the
    profile active on the device, then any profile bound to it. Falls back to the
    device name, which matches profiles created by the install scripts.
    """
    if SANDBOX_MODE:
        return device

    success, output = run_cmd(["nmcli", "-g", "GENERAL.CON-UUID", "device", "show", device])
    if success and output.strip():
        return output.strip().splitlines()[0]

    success, output = run_cmd(["nmcli", "-g", "UUID", "connection", "show"])
    if success:
        for uuid in output.split():
            ok, iface = run_cmd(["nmcli", "-g", "connection.interface-name", "connection", "show", uuid])
            if ok and iface.strip() == device:
                return uuid

    return device


def subnet_mask_to_prefix(mask):
    value = 0
    for octet in (int(part) for part in mask.split(".")):
        value = (value << 8) | octet
    bits = f"{value:032b}"
    if "01" in bits:
        return None
    return bits.count("1")


def get_interface_ipv4(interface_name):
    if SANDBOX_MODE:
        sandbox_values = {
            role_device("wan"): "192.168.1.150",
            role_device("lan"): "10.12.0.1",
            role_device("wifi"): "192.168.1.180",
        }
        return sandbox_values.get(interface_name, "")

    success, output = run_cmd(["ip", "-4", "-o", "addr", "show", "dev", interface_name])
    if not success:
        return ""

    match = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)/(\d+)", output)
    return match.group(1) if match else ""


def get_ipv4_method(interface_name):
    """Return 'static', 'dhcp' or '' for the profile active on a device."""
    if SANDBOX_MODE:
        return "static" if interface_name == role_device("lan") else "dhcp"

    connection = resolve_connection(interface_name)
    success, output = run_cmd(["nmcli", "-g", "ipv4.method", "connection", "show", connection])
    if not success:
        return ""
    method = output.strip()
    return {"manual": "static", "auto": "dhcp"}.get(method, method)


def parse_static_ipv4(data, label):
    """Validate ip/subnet (+ optional gateway/dns) from a request payload."""
    ip_address = str(data.get("ip") or "").strip()
    subnet = str(data.get("subnet") or "").strip()
    gateway = str(data.get("gateway") or "").strip()
    dns = str(data.get("dns") or "").strip()

    if not ip_address:
        return None, f"{label} IP address is required."
    if not subnet:
        return None, f"{label} subnet mask is required."
    if not IP_REGEX.match(ip_address):
        return None, f"Invalid {label} IP address."
    if not IP_REGEX.match(subnet):
        return None, f"Invalid {label} subnet format."

    prefix = subnet_mask_to_prefix(subnet)
    if prefix is None:
        return None, f"Invalid {label} subnet mask."
    if gateway and not IP_REGEX.match(gateway):
        return None, f"Invalid {label} gateway address."

    dns_servers = [server for server in re.split(r"[,\s]+", dns) if server]
    if any(not IP_REGEX.match(server) for server in dns_servers):
        return None, f"Invalid {label} DNS server address."

    return {
        "address": f"{ip_address}/{prefix}",
        "ip": ip_address,
        "gateway": gateway,
        "dns": " ".join(dns_servers),
    }, ""


def apply_ipv4(device, static_config=None, clear_routing=False):
    """Apply DHCP (static_config=None) or static IPv4 settings and bring the profile up."""
    connection = resolve_connection(device)
    cmd = ["sudo", "nmcli", "con", "modify", connection]
    if static_config is None:
        cmd += ["ipv4.method", "auto", "ipv4.addresses", "", "ipv4.gateway", "", "ipv4.dns", ""]
    else:
        gateway = "" if clear_routing else static_config["gateway"]
        dns = "" if clear_routing else static_config["dns"]
        cmd += [
            "ipv4.addresses", static_config["address"],
            "ipv4.method", "manual",
            "ipv4.gateway", gateway,
            "ipv4.dns", dns,
        ]

    success, output = run_cmd(cmd)
    if not success:
        return False, output

    return run_cmd(["sudo", "nmcli", "con", "up", connection])


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.before_request
def require_login():
    """Every page and API call needs a logged-in session, except the login page itself."""
    if request.endpoint in ("login", "logout", "static") or session.get("user"):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"success": False, "error": "Login required."}), 401
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        credentials = load_credentials()
        if username == credentials["username"] and check_password_hash(
            credentials["password_hash"], password
        ):
            session.clear()
            session["user"] = username
            return redirect(url_for("index"))
        time.sleep(1)  # slow down password guessing
        error = "Invalid username or password."
    return render_template("login.html", error=error), (401 if error else 200)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/api/auth/password", methods=["POST"])
def change_password():
    data = request.get_json(silent=True) or {}
    current = str(data.get("current_password", ""))
    new = str(data.get("new_password", ""))
    confirm = str(data.get("confirm_password", ""))

    credentials = load_credentials()
    if not check_password_hash(credentials["password_hash"], current):
        time.sleep(1)
        return jsonify({"success": False, "error": "Current password is incorrect."}), 400
    if len(new) < MIN_PASSWORD_LENGTH:
        return jsonify(
            {"success": False, "error": f"New password must be at least {MIN_PASSWORD_LENGTH} characters."}
        ), 400
    if new != confirm:
        return jsonify({"success": False, "error": "New passwords do not match."}), 400

    credentials["password_hash"] = generate_password_hash(new)
    try:
        save_credentials(credentials)
    except OSError as error:
        return jsonify({"success": False, "error": f"Could not save password: {error}"}), 500
    return jsonify({"success": True, "message": "Password changed."})


@app.route("/")
def index():
    return render_template("index.html", username=session.get("user", ""))


@app.route("/api/network/interfaces", methods=["GET"])
def get_interface_mapping():
    return jsonify({"success": True, "mapping": dict(INTERFACE_ROLES)})


@app.route("/api/network/interfaces", methods=["POST"])
def set_interface_mapping():
    global INTERFACE_ROLES

    data = request.get_json(silent=True) or {}
    wan_name = str(data.get("wan", role_device("wan"))).strip()
    lan_name = str(data.get("lan", role_device("lan"))).strip()
    wifi_name = str(data.get("wifi", role_device("wifi"))).strip()

    is_valid, validation_error = validate_wan_lan_swap(wan_name, lan_name)
    if not is_valid:
        return jsonify({"success": False, "error": validation_error}), 400
    if not wifi_name:
        return jsonify({"success": False, "error": "Wi-Fi interface cannot be empty."}), 400

    updated_roles = {"wan": wan_name, "lan": lan_name, "wifi": wifi_name}
    try:
        persist_interface_roles(updated_roles)
    except OSError as error:
        return jsonify({"success": False, "error": f"Failed to persist mapping: {error}"}), 500

    INTERFACE_ROLES = updated_roles
    return jsonify(
        {
            "success": True,
            "message": f"Interface mapping updated: WAN={wan_name}, LAN={lan_name}, Wi-Fi={wifi_name}.",
            "mapping": updated_roles,
        }
    )


@app.route("/api/network/current", methods=["GET"])
def get_current_network_values():
    return jsonify(
        {
            "success": True,
            "mapping": dict(INTERFACE_ROLES),
            "addresses": {role: get_interface_ipv4(dev) for role, dev in INTERFACE_ROLES.items()},
            "methods": {role: get_ipv4_method(dev) for role, dev in INTERFACE_ROLES.items()},
        }
    )


@app.route("/api/system/status", methods=["GET"])
def system_status():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r", encoding="utf-8") as handle:
            temp_c = round(float(handle.read().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        temp_c = None

    try:
        load1, load5, load15 = os.getloadavg()
    except (OSError, AttributeError):
        load1 = load5 = load15 = 0.0

    uptime_str = "Unknown"
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as handle:
            uptime_seconds = float(handle.readline().split()[0])
        days = int(uptime_seconds // 86400)
        hours = int((uptime_seconds % 86400) // 3600)
        minutes = int((uptime_seconds % 3600) // 60)
        uptime_str = f"{days}d {hours}h {minutes}m"
    except (OSError, ValueError, IndexError):
        pass

    model = ""
    try:
        with open("/proc/device-tree/model", "r", encoding="utf-8") as handle:
            model = handle.read().strip("\x00\n ")
    except OSError:
        pass

    chrony_active = False
    stratum = None
    success, output = run_cmd(["chronyc", "tracking"])
    if success:
        chrony_active = True
        for line in output.splitlines():
            if line.startswith("Stratum"):
                try:
                    stratum = int(line.split(":", 1)[1].strip())
                except (IndexError, ValueError):
                    pass

    return jsonify(
        {
            "success": True,
            "temperature": temp_c,
            "load": [round(load1, 2), round(load5, 2), round(load15, 2)],
            "uptime": uptime_str,
            "chrony_active": chrony_active,
            "stratum": stratum,
            "model": model,
        }
    )


@app.route("/api/network/wan", methods=["POST"])
def configure_wan():
    data = request.get_json(silent=True) or {}
    mode = str(data.get("mode", "dhcp")).lower()
    device = role_device("wan")

    if mode == "static":
        config, error = parse_static_ipv4(data, "WAN")
        if config is None:
            return jsonify({"success": False, "error": error}), 400
        success, output = apply_ipv4(device, config)
    elif mode == "dhcp":
        success, output = apply_ipv4(device, None)
    else:
        return jsonify({"success": False, "error": "Mode must be either 'dhcp' or 'static'."}), 400

    if not success:
        return jsonify({"success": False, "error": output}), 500
    return jsonify({"success": True, "message": f"WAN {device} configured to {mode}."})


@app.route("/api/network/wifi", methods=["POST"])
def configure_wifi():
    data = request.get_json(silent=True) or {}
    mode = str(data.get("mode", "dhcp")).lower()
    ssid = str(data.get("ssid", "")).strip()
    password = str(data.get("password", ""))
    device = role_device("wifi")

    if not SSID_REGEX.match(ssid):
        return jsonify({"success": False, "error": "Invalid SSID name."}), 400
    if mode not in ("dhcp", "static"):
        return jsonify({"success": False, "error": "Mode must be either 'dhcp' or 'static'."}), 400

    # Validate static settings before touching the radio.
    config = None
    if mode == "static":
        config, error = parse_static_ipv4(data, "Wi-Fi")
        if config is None:
            return jsonify({"success": False, "error": error}), 400

    connect_cmd = ["sudo", "nmcli", "device", "wifi", "connect", ssid, "ifname", device]
    if password:
        connect_cmd += ["password", password]
    success, output = run_cmd(connect_cmd)
    if not success:
        return jsonify({"success": False, "error": f"Failed to connect to Wi-Fi: {output}"}), 500

    success, output = apply_ipv4(device, config)
    if not success:
        return jsonify({"success": False, "error": output}), 500

    return jsonify({"success": True, "message": f"Connected to wireless network {ssid} ({mode})."})


@app.route("/api/network/wifi/scan", methods=["GET"])
def scan_wifi():
    success, output = run_cmd(
        ["sudo", "nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "yes"]
    )
    if not success:
        return jsonify(
            {
                "success": False,
                "error": output or "Wi-Fi scan failed on this Raspberry Pi. Check nmcli and Wi-Fi hardware state.",
                "networks": [],
            }
        ), 500

    networks = {}
    for line in output.splitlines():
        # nmcli -t escapes ':' inside fields as '\:'
        parts = [part.replace("\\:", ":").strip() for part in re.split(r"(?<!\\):", line)]
        if len(parts) < 2 or not parts[0]:
            continue
        ssid, signal = parts[0], parts[1]
        security = parts[2] if len(parts) > 2 and parts[2] else "Open"
        # Keep the strongest entry per SSID.
        previous = networks.get(ssid)
        if previous is None or int(signal or 0) > int(previous["signal"] or 0):
            networks[ssid] = {"ssid": ssid, "signal": signal, "security": security}

    ordered = sorted(networks.values(), key=lambda n: int(n["signal"] or 0), reverse=True)
    return jsonify({"success": True, "networks": ordered})


@app.route("/api/network/lan", methods=["POST"])
def configure_lan():
    data = request.get_json(silent=True) or {}

    config, error = parse_static_ipv4(data, "LAN")
    if config is None:
        return jsonify({"success": False, "error": error}), 400

    device = role_device("lan")
    if device == role_device("wan"):
        return jsonify(
            {"success": False, "error": "LAN and WAN are the same interface; refusing to overwrite WAN settings."}
        ), 400

    # LAN is a closed network: never a gateway or DNS on it.
    success, output = apply_ipv4(device, config, clear_routing=True)
    if not success:
        return jsonify({"success": False, "error": output}), 500

    message = f"LAN {device} configured to static {config['ip']}."
    # Let LAN clients on the new subnet use the NTP server (chrony 'allow' rule).
    if SANDBOX_MODE or os.path.exists(LAN_REFRESH_HELPER):
        prefix = config["address"].split("/")[1]
        ok, helper_output = run_cmd(["sudo", LAN_REFRESH_HELPER, config["ip"], prefix])
        if not ok:
            message += f" Warning: updating the chrony allow rule failed: {helper_output}"

    return jsonify({"success": True, "message": message})


@app.route("/api/system/ntp-logs", methods=["GET"])
def get_ntp_logs():
    """Recent chrony log lines (service user is in the systemd-journal group)."""
    success, output = run_cmd(["journalctl", "-u", "chrony", "--no-pager", "-n", "100"])
    if not success:
        return jsonify({"success": False, "error": f"Failed to retrieve NTP logs: {output}"}), 500
    return jsonify({"success": True, "logs": output})


@app.route("/api/system/ntp-sources", methods=["GET"])
def get_ntp_sources():
    success, output = run_cmd(["chronyc", "sources", "-v"])
    if not success:
        return jsonify({"success": False, "error": f"Failed to retrieve NTP sources: {output}"}), 500
    return jsonify({"success": True, "sources": output})


@app.route("/api/system/service-status", methods=["GET"])
def get_service_status():
    status = {}
    for service in ("ntp-webgui", "chrony", "nftables", "NetworkManager"):
        # 'is-active' exits non-zero for inactive units but still prints the state.
        if SANDBOX_MODE:
            status[service] = "active"
            continue
        try:
            result = subprocess.run(
                ["systemctl", "is-active", service], capture_output=True, text=True, timeout=10
            )
            status[service] = result.stdout.strip() or "unknown"
        except (subprocess.SubprocessError, FileNotFoundError):
            status[service] = "unknown"
    return jsonify({"success": True, "status": status})


@app.route("/api/system/resources", methods=["GET"])
def get_system_resources():
    try:
        cpu_load = [f"{value:.2f}" for value in os.getloadavg()]
    except (OSError, AttributeError):
        cpu_load = "unknown"

    memory_info = {}
    success, mem_output = run_cmd(["free", "-m"])
    if success:
        lines = mem_output.splitlines()
        if len(lines) >= 2:
            mem_line = lines[1].split()
            if len(mem_line) >= 4:
                memory_info = {
                    "total": f"{mem_line[1]} MB",
                    "used": f"{mem_line[2]} MB",
                    "free": f"{mem_line[3]} MB",
                }

    disk_usage = []
    success, disk_output = run_cmd(["df", "-h"])
    if success:
        for line in disk_output.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 5:
                disk_usage.append(
                    {
                        "filesystem": parts[0],
                        "size": parts[1],
                        "used": parts[2],
                        "available": parts[3],
                        "use_percent": parts[4],
                    }
                )

    return jsonify(
        {"success": True, "cpu_load": cpu_load, "memory": memory_info, "disk_usage": disk_usage}
    )


@app.route("/api/service/ntp", methods=["POST"])
def control_ntp():
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "restart")).lower()
    if action not in ("restart", "reload"):
        return jsonify({"success": False, "error": "Action must be 'restart' or 'reload'."}), 400

    errors = []
    for service_name in NTP_SERVICE_NAMES:
        success, output = run_cmd(["sudo", "systemctl", action, service_name])
        if success:
            return jsonify(
                {
                    "success": True,
                    "message": f"NTP daemon '{service_name}' {action}ed successfully.",
                    "service": service_name,
                }
            )
        errors.append(f"{service_name}: {output}")
    return jsonify({"success": False, "error": "; ".join(errors)}), 500


@app.route("/api/service/reboot", methods=["POST"])
def reboot_system():
    success, output = run_cmd(["sudo", "reboot"])
    if not success:
        return jsonify({"success": False, "error": output}), 500
    return jsonify({"success": True, "message": "Raspberry Pi is restarting..."})


if __name__ == "__main__":
    load_credentials()  # create the credentials file (admin/admin) on first start
    app.run(
        host=os.getenv("APP_HOST", "0.0.0.0"),
        port=int(os.getenv("APP_PORT", "5000")),
        debug=False,
    )
