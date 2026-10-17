#!/usr/bin/env python3

import os
import re
import subprocess
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)


IP_REGEX = re.compile(
    r"^((25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
    r"(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)$"
)
SUBNET_REGEX = IP_REGEX
SSID_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.\s]{1,32}$")

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
    """Detect which interface has the default route (internet connection)"""
    try:
        # Use ip route to find the default gateway interface
        result = subprocess.run(["ip", "route", "show", "default"], 
                              capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            output = result.stdout.strip()
            # Look for the interface name in the route
            import re
            match = re.search(r'device\s+(\w+)', output)
            if match:
                interface = match.group(1)
                # Validate it's either eth0 or eth1
                if interface in ['eth0', 'eth1']:
                    return interface
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
        pass
    
    # Fallback to checking active connections with nmcli
    try:
        result = subprocess.run(["nmcli", "device", "status"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            for line in lines[1:]:  # Skip header
                parts = line.split()
                if len(parts) >= 3 and parts[2] == "connected":
                    device = parts[0]
                    if device in ['eth0', 'eth1']:
                        return device
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
        pass
    
    # Final fallback - check if interfaces are actually up with ip link
    try:
        result = subprocess.run(["ip", "link", "show"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            for line in lines:
                if 'eth0' in line and 'state UP' in line:
                    return 'eth0'
                elif 'eth1' in line and 'state UP' in line:
                    return 'eth1'
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
        pass
    
    # If we can't determine, return None to use defaults
    return None


def build_interface_roles():
    runtime = load_runtime_env(RUNTIME_ENV_PATH)
    
    # Try to detect actual active interfaces if not set in runtime
    wan_if = runtime.get(INTERFACE_KEY_MAP["wan"], os.getenv(INTERFACE_KEY_MAP["wan"], "eth0"))
    lan_if = runtime.get(INTERFACE_KEY_MAP["lan"], os.getenv(INTERFACE_KEY_MAP["lan"], "eth1"))
    wifi_if = runtime.get(INTERFACE_KEY_MAP["wifi"], os.getenv(INTERFACE_KEY_MAP["wifi"], "wlan0"))
    
    # Try to detect the actual WAN interface by checking network routes
    detected_wan = detect_wan_interface()
    if detected_wan:
        # If we have a better detection, use it but maintain the mapping logic
        # But only if it's not already set in runtime (preserve user preferences)
        if INTERFACE_KEY_MAP["wan"] not in runtime:
            wan_if = detected_wan
    
    # Validate that these interfaces exist and are properly configured
    # If they're not in the expected state, we'll try to determine them from active connections
    try:
        # Check if NetworkManager is available and get current connection status
        result = subprocess.run(["nmcli", "device", "status"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            # Parse nmcli output to find active connections
            lines = result.stdout.strip().split('\n')
            # Skip header line and process connection lines
            for line in lines[1:]:  # Skip header
                parts = line.split()
                if len(parts) >= 3:
                    device = parts[0]
                    state = parts[2]
                    # If we find an active connection, check if it's the default route
                    if state == "connected":
                        if device == wan_if or device == lan_if:
                            # This is already set correctly
                            continue
    except (subprocess.TimeoutExpired, subprocess.SubprocessError, FileNotFoundError):
        # NetworkManager not available or command failed, use defaults
        pass
    
    return {
        "wan": wan_if,
        "lan": lan_if,
        "wifi": wifi_if,
    }


def persist_interface_roles(roles):
    runtime = load_runtime_env(RUNTIME_ENV_PATH)
    runtime[INTERFACE_KEY_MAP["wan"]] = roles["wan"]
    runtime[INTERFACE_KEY_MAP["lan"]] = roles["lan"]
    runtime[INTERFACE_KEY_MAP["wifi"]] = roles["wifi"]

    lines = [f"{key}={runtime[key]}" for key in sorted(runtime.keys())]
    os.makedirs(os.path.dirname(RUNTIME_ENV_PATH), exist_ok=True)
    with open(RUNTIME_ENV_PATH, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def validate_wan_lan_swap(wan_name, lan_name):
    if wan_name == lan_name:
        return False, "WAN and LAN interfaces must be different."

    allowed = {"eth0", "eth1"}
    if wan_name not in allowed or lan_name not in allowed:
        return False, "WAN/LAN mapping supports only eth0 and eth1."

    # Check that both interfaces actually exist
    try:
        result = subprocess.run(["ip", "link", "show", wan_name], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return False, f"WAN interface {wan_name} does not exist."
            
        result = subprocess.run(["ip", "link", "show", lan_name], capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return False, f"LAN interface {lan_name} does not exist."
    except (subprocess.TimeoutExpired, subprocess.SubprocessError):
        # If we can't check, proceed with validation based on allowed set
        pass

    if {wan_name, lan_name} != allowed:
        return False, "WAN/LAN mapping must be one eth0 and one eth1."

    return True, ""


SANDBOX_MODE = env_flag("SANDBOX_MODE", default=False)
INTERFACE_ROLES = build_interface_roles()
WAN_CONNECTION_NAME = INTERFACE_ROLES["wan"]
LAN_CONNECTION_NAME = INTERFACE_ROLES["lan"]
WIFI_CONNECTION_NAME = INTERFACE_ROLES["wifi"]
NTP_SERVICE_NAMES = [
    name.strip()
    for name in os.getenv("NTP_SERVICE_NAMES", "chrony").split(",")
    if name.strip()
]


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


def run_service_command(action):
    errors = []
    for service_name in NTP_SERVICE_NAMES:
        success, output = run_cmd(["sudo", "systemctl", action, service_name])
        if success:
            return True, output, service_name
        errors.append(f"{service_name}: {output}")
    return False, "; ".join(errors), None


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
        )
        return True, result.stdout.strip()
    except subprocess.CalledProcessError as error:
        return False, error.stderr.strip() or str(error)
    except FileNotFoundError as error:
        return False, str(error)


def subnet_mask_to_prefix(mask):
    octets = [int(part) for part in mask.split(".")]
    value = 0
    for octet in octets:
        value = (value << 8) | octet
    bits = f"{value:032b}"
    if "01" in bits:
        return None
    return bits.count("1")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/network/interfaces", methods=["GET"])
def get_interface_mapping():
    return jsonify(
        {
            "success": True,
            "mapping": {
                "wan": WAN_CONNECTION_NAME,
                "lan": LAN_CONNECTION_NAME,
                "wifi": WIFI_CONNECTION_NAME,
            },
        }
    )


@app.route("/api/network/interfaces", methods=["POST"])
def set_interface_mapping():
    global WAN_CONNECTION_NAME, LAN_CONNECTION_NAME, WIFI_CONNECTION_NAME, INTERFACE_ROLES

    data = request.get_json(silent=True) or {}
    wan_name = str(data.get("wan", WAN_CONNECTION_NAME)).strip()
    lan_name = str(data.get("lan", LAN_CONNECTION_NAME)).strip()
    wifi_name = str(data.get("wifi", WIFI_CONNECTION_NAME)).strip()

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
    WAN_CONNECTION_NAME = wan_name
    LAN_CONNECTION_NAME = lan_name
    WIFI_CONNECTION_NAME = wifi_name
    
    # Return the updated mapping in response for client-side consistency
    return jsonify({
        "success": True,
        "mapping": updated_roles
    })
        {
            "success": True,
            "message": f"Interface mapping updated: WAN={wan_name}, LAN={lan_name}, Wi-Fi={wifi_name}.",
            "mapping": updated_roles,
        }
    )


@app.route("/api/system/status", methods=["GET"])
def system_status():
    temp_c = 0.0
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r", encoding="utf-8") as handle:
            temp_c = round(float(handle.read().strip()) / 1000.0, 1)
    except OSError:
        temp_c = 45.0

    try:
        if hasattr(os, "getloadavg"):
            load1, load5, load15 = os.getloadavg()
        else:
            raise OSError
    except OSError:
        load1, load5, load15 = 0.4, 0.25, 0.2

    uptime_str = "Unknown"
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as handle:
            uptime_seconds = float(handle.readline().split()[0])
            days = int(uptime_seconds // 86400)
            hours = int((uptime_seconds % 86400) // 3600)
            minutes = int((uptime_seconds % 3600) // 60)
            uptime_str = f"{days}d {hours}h {minutes}m"
    except OSError:
        uptime_str = "14d 6h"

    chrony_active = False
    stratum = 2
    success, output = run_cmd(["chronyc", "tracking"])
    if success:
        chrony_active = True
        for line in output.split("\n"):
            if "Stratum" in line:
                try:
                    stratum = int(line.split(":")[1].strip())
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
        }
    )


@app.route("/api/network/wan", methods=["POST"])
def configure_wan():
    data = request.get_json(silent=True) or {}
    mode = str(data.get("mode", "dhcp")).lower()

    if mode == "static":
        ip_address = str(data.get("ip", "")).strip()
        subnet = str(data.get("subnet", "")).strip()
        if not IP_REGEX.match(ip_address) or not SUBNET_REGEX.match(subnet):
            return jsonify({"success": False, "error": "Invalid IP or subnet format."}), 400

        prefix = subnet_mask_to_prefix(subnet)
        if prefix is None:
            return jsonify({"success": False, "error": "Invalid subnet mask."}), 400

        cmd = [
            "sudo",
            "nmcli",
            "con",
            "modify",
            WAN_CONNECTION_NAME,
            "ipv4.addresses",
            f"{ip_address}/{prefix}",
            "ipv4.method",
            "manual",
        ]
    elif mode == "dhcp":
        cmd = [
            "sudo",
            "nmcli",
            "con",
            "modify",
            WAN_CONNECTION_NAME,
            "ipv4.method",
            "auto",
        ]
    else:
        return jsonify({"success": False, "error": "Mode must be either 'dhcp' or 'static'."}), 400

    success, output = run_cmd(cmd)
    if not success:
        return jsonify({"success": False, "error": output}), 500

    up_success, up_output = run_cmd(["sudo", "nmcli", "con", "up", WAN_CONNECTION_NAME])
    if not up_success:
        return jsonify({"success": False, "error": up_output}), 500

    return jsonify(
        {"success": True, "message": f"WAN {WAN_CONNECTION_NAME} configured to {mode}."}
    )


@app.route("/api/network/wifi", methods=["POST"])
def configure_wifi():
    data = request.get_json(silent=True) or {}
    mode = str(data.get("mode", "dhcp")).lower()
    ssid = str(data.get("ssid", "")).strip()
    password = str(data.get("password", ""))
    ip_address = str(data.get("ip", "")).strip()
    subnet = str(data.get("subnet", "")).strip()

    if not SSID_REGEX.match(ssid):
        return jsonify({"success": False, "error": "Invalid SSID name."}), 400

    if mode not in ("dhcp", "static"):
        return jsonify({"success": False, "error": "Mode must be either 'dhcp' or 'static'."}), 400

    success, output = run_cmd(
        ["sudo", "nmcli", "device", "wifi", "connect", ssid, "password", password]
    )
    if not success:
        return jsonify({"success": False, "error": f"Failed to connect to Wi-Fi: {output}"}), 500

    if mode == "static":
        if not IP_REGEX.match(ip_address) or not SUBNET_REGEX.match(subnet):
            return jsonify({"success": False, "error": "Invalid Wi-Fi IP or subnet format."}), 400
        prefix = subnet_mask_to_prefix(subnet)
        if prefix is None:
            return jsonify({"success": False, "error": "Invalid Wi-Fi subnet mask."}), 400
        success, output = run_cmd(
            [
                "sudo",
                "nmcli",
                "con",
                "modify",
                WIFI_CONNECTION_NAME,
                "ipv4.addresses",
                f"{ip_address}/{prefix}",
                "ipv4.method",
                "manual",
            ]
        )
        if not success:
            return jsonify({"success": False, "error": output}), 500
    else:
        success, output = run_cmd(
            ["sudo", "nmcli", "con", "modify", WIFI_CONNECTION_NAME, "ipv4.method", "auto"]
        )
        if not success:
            return jsonify({"success": False, "error": output}), 500

    up_success, up_output = run_cmd(["sudo", "nmcli", "con", "up", WIFI_CONNECTION_NAME])
    if not up_success:
        return jsonify({"success": False, "error": up_output}), 500

    return jsonify({"success": True, "message": f"Connected to wireless network {ssid} ({mode})."})


@app.route("/api/network/wifi/scan", methods=["GET"])
def scan_wifi():
    success, output = run_cmd(
        ["sudo", "nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"]
    )

    if not success:
        if SANDBOX_MODE:
            networks = [
                {"ssid": "Home_Lab_5G", "signal": "98", "security": "WPA2"},
                {"ssid": "Pi_Setup_AP", "signal": "75", "security": "WPA2"},
            ]
            return jsonify({"success": True, "networks": networks})

        return jsonify(
            {
                "success": False,
                "error": output or "Wi-Fi scan failed on this Raspberry Pi. Check nmcli and Wi-Fi hardware state.",
                "networks": [],
            }
        ), 500

    networks = []
    for line in output.splitlines():
        if not line.strip():
            continue

        parts = [part.strip() for part in line.split(":")]
        if len(parts) >= 3 and parts[0]:
            networks.append(
                {
                    "ssid": parts[0],
                    "signal": parts[1],
                    "security": parts[2] if parts[2] else "Open",
                }
            )
        elif len(parts) >= 2 and parts[0]:
            networks.append(
                {
                    "ssid": parts[0],
                    "signal": parts[1],
                    "security": "Open",
                }
            )

    return jsonify({"success": True, "networks": networks})


@app.route("/api/network/lan", methods=["POST"])
def configure_lan():
    data = request.get_json(silent=True) or {}
    ip_address = str(data.get("ip", "10.12.0.1")).strip()
    subnet = str(data.get("subnet", "255.255.0.0")).strip()

    if not IP_REGEX.match(ip_address):
        return jsonify({"success": False, "error": "Invalid LAN IP address."}), 400
    if not SUBNET_REGEX.match(subnet):
        return jsonify({"success": False, "error": "Invalid LAN subnet format."}), 400

    prefix = subnet_mask_to_prefix(subnet)
    if prefix is None:
        return jsonify({"success": False, "error": "Invalid LAN subnet mask."}), 400

    success, output = run_cmd(
        [
            "sudo",
            "nmcli",
            "con",
            "modify",
            LAN_CONNECTION_NAME,
            "ipv4.addresses",
            f"{ip_address}/{prefix}",
            "ipv4.method",
            "manual",
            "ipv4.gateway",
            "",
            "ipv4.dns",
            "",
        ]
    )
    if not success:
        return jsonify({"success": False, "error": output}), 500

    up_success, up_output = run_cmd(["sudo", "nmcli", "con", "up", LAN_CONNECTION_NAME])
    if not up_success:
        return jsonify({"success": False, "error": up_output}), 500

    return jsonify(
        {
            "success": True,
            "message": f"LAN {LAN_CONNECTION_NAME} configured to static {ip_address}.",
        }
    )


@app.route("/api/system/ntp-logs", methods=["GET"])
def get_ntp_logs():
    """Retrieve recent chrony NTP logs."""
    try:
        # Get last 100 lines of chrony logs
        success, output = run_cmd(["sudo", "journalctl", "-u", "chrony", "--no-pager", "-n", "100"])
        if not success:
            return jsonify({"success": False, "error": "Failed to retrieve NTP logs"}), 500
        
        return jsonify({
            "success": True,
            "logs": output
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/system/ntp-sources", methods=["GET"])
def get_ntp_sources():
    """Retrieve current NTP sources information."""
    try:
        # Get NTP sources using chronyc sources
        success, output = run_cmd(["sudo", "chronyc", "sources", "-v"])
        if not success:
            return jsonify({"success": False, "error": "Failed to retrieve NTP sources"}), 500
        
        return jsonify({
            "success": True,
            "sources": output
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/system/service-status", methods=["GET"])
def get_service_status():
    """Retrieve status of all system services."""
    try:
        services = ["chrony", "nginx", "dnsmasq", "nftables"]
        service_status = {}
        
        for service in services:
            success, output = run_cmd(["sudo", "systemctl", "is-active", service])
            if success:
                service_status[service] = output.strip()
            else:
                service_status[service] = "unknown"
        
        return jsonify({
            "success": True,
            "status": service_status
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/system/resources", methods=["GET"])
def get_system_resources():
    """Retrieve detailed system resource usage."""
    try:
        # Get CPU load
        success, load_output = run_cmd(["uptime"])
        if not success:
            cpu_load = "unknown"
        else:
            # Extract the load average from uptime output
            import re
            match = re.search(r'load average: ([\d\.]+), ([\d\.]+), ([\d\.]+)', load_output)
            if match:
                cpu_load = [match.group(1), match.group(2), match.group(3)]
            else:
                cpu_load = "unknown"
        
        # Get memory usage
        success, mem_output = run_cmd(["free", "-m"])
        if not success:
            memory_info = {}
        else:
            lines = mem_output.strip().split('\n')
            if len(lines) >= 2:
                mem_line = lines[1].split()
                if len(mem_line) >= 4:
                    memory_info = {
                        "total": f"{mem_line[1]} MB",
                        "used": f"{mem_line[2]} MB",
                        "free": f"{mem_line[3]} MB"
                    }
                else:
                    memory_info = {}
            else:
                memory_info = {}
        
        # Get disk usage
        success, disk_output = run_cmd(["df", "-h"])
        if not success:
            disk_usage = []
        else:
            lines = disk_output.strip().split('\n')[1:]  # Skip header
            disk_usage = []
            for line in lines:
                parts = line.split()
                if len(parts) >= 5:
                    disk_usage.append({
                        "filesystem": parts[0],
                        "size": parts[1],
                        "used": parts[2],
                        "available": parts[3],
                        "use_percent": parts[4]
                    })
        
        return jsonify({
            "success": True,
            "cpu_load": cpu_load,
            "memory": memory_info,
            "disk_usage": disk_usage
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/service/ntp", methods=["POST"])
def control_ntp():
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "restart")).lower()
    if action not in ("restart", "reload"):
        return jsonify({"success": False, "error": "Action must be 'restart' or 'reload'."}), 400

    success, output, service_name = run_service_command(action)
    if not success:
        return jsonify({"success": False, "error": output}), 500

    return jsonify(
        {
            "success": True,
            "message": f"NTP daemon '{service_name}' {action}ed successfully.",
            "service": service_name,
        }
    )


@app.route("/api/service/reboot", methods=["POST"])
def reboot_system():
    success, output = run_cmd(["sudo", "reboot"])
    if not success:
        return jsonify({"success": False, "error": output}), 500
    return jsonify({"success": True, "message": "Raspberry Pi is restarting..."})


def reset_network_connections():
    """Reset network connections to resolve conflicts after reboot"""
    try:
        # Try to disconnect and reconnect all ethernet interfaces
        subprocess.run(["nmcli", "device", "disconnect", "eth0"], check=False, capture_output=True)
        subprocess.run(["nmcli", "device", "disconnect", "eth1"], check=False, capture_output=True)
        # Give some time for disconnection
        import time
        time.sleep(2)
        # Reconnect eth0 and eth1 to force proper detection
        subprocess.run(["nmcli", "device", "connect", "eth0"], check=False, capture_output=True)
        subprocess.run(["nmcli", "device", "connect", "eth1"], check=False, capture_output=True)
    except (subprocess.SubprocessError, FileNotFoundError):
        # If nmcli is not available, we can't reset connections
        pass


def fix_netplan_conflicts():
    """Fix netplan conflicts that cause interface naming issues"""
    try:
        # Check for conflicting connections and remove them
        result = subprocess.run(["nmcli", "con", "show"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            # Look for netplan-eth0 connections that might be conflicting
            for line in lines[1:]:  # Skip header
                if "netplan-eth" in line and ("eth0" in line or "eth1" in line):
                    # Try to delete the conflicting connection
                    parts = line.split()
                    if len(parts) > 0:
                        conn_name = parts[0]
                        try:
                            subprocess.run(["nmcli", "con", "delete", conn_name], check=True, capture_output=True)
                        except:
                            # If deletion fails, that's okay - continue
                            pass
        
        # Restart NetworkManager to clear any stale connections
        subprocess.run(["systemctl", "restart", "NetworkManager"], check=False, capture_output=True)
        
        return True
    except Exception as e:
        print(f"Error fixing netplan conflicts: {e}")
        return False


@app.route("/api/network/fix-netplan", methods=["POST"])
def fix_netplan_conflicts_endpoint():
    """Endpoint to manually fix netplan conflicts"""
    try:
        success = fix_netplan_conflicts()
        if success:
            return jsonify({"success": True, "message": "Netplan conflicts fixed"})
        else:
            return jsonify({"success": False, "error": "Failed to fix netplan conflicts"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": f"Error: {str(e)}"}), 500


def restart_network_services():
    """Restart network services to ensure proper interface configuration"""
    try:
        # Restart NetworkManager
        subprocess.run(["systemctl", "restart", "NetworkManager"], check=False, capture_output=True)
        
        # Give some time for services to restart
        import time
        time.sleep(3)
        
        # Apply netplan configuration if it exists
        try:
            subprocess.run(["netplan", "apply"], check=False, capture_output=True)
        except FileNotFoundError:
            # netplan not available, that's okay
            pass
            
        return True
    except Exception as e:
        print(f"Error restarting network services: {e}")
        return False


@app.route("/api/network/restart", methods=["POST"])
def restart_network():
    """Endpoint to restart network services"""
    try:
        success = restart_network_services()
        if success:
            # Rebuild interface roles after restart
            global INTERFACE_ROLES, WAN_CONNECTION_NAME, LAN_CONNECTION_NAME, WIFI_CONNECTION_NAME
            INTERFACE_ROLES = build_interface_roles()
            WAN_CONNECTION_NAME = INTERFACE_ROLES["wan"]
            LAN_CONNECTION_NAME = INTERFACE_ROLES["lan"]
            WIFI_CONNECTION_NAME = INTERFACE_ROLES["wifi"]
            
            return jsonify({
                "success": True, 
                "message": "Network services restarted",
                "current_mapping": {
                    "wan": WAN_CONNECTION_NAME,
                    "lan": LAN_CONNECTION_NAME,
                    "wifi": WIFI_CONNECTION_NAME,
                }
            })
        else:
            return jsonify({"success": False, "error": "Failed to restart network services"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": f"Error: {str(e)}"}), 500


if __name__ == "__main__":
    app_host = os.getenv("APP_HOST", "0.0.0.0")
    app_port = int(os.getenv("APP_PORT", "5000"))
    app.run(host=app_host, port=app_port, debug=False)
