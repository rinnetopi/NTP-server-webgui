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


def env_flag(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


SANDBOX_MODE = env_flag("SANDBOX_MODE", default=False)
WAN_CONNECTION_NAME = os.getenv("WAN_CONNECTION_NAME", "eth0")
LAN_CONNECTION_NAME = os.getenv("LAN_CONNECTION_NAME", "eth1")
WIFI_CONNECTION_NAME = os.getenv("WIFI_CONNECTION_NAME", "wlan0")
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

    return jsonify({"success": True, "message": f"WAN eth0 configured to {mode}."})


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

    networks = []
    if success:
        for line in output.split("\n"):
            parts = line.split(":")
            if len(parts) >= 3 and parts[0].strip():
                networks.append(
                    {
                        "ssid": parts[0].strip(),
                        "signal": parts[1].strip(),
                        "security": parts[2].strip(),
                    }
                )
    else:
        networks = [
            {"ssid": "Home_Lab_5G", "signal": "98", "security": "WPA2"},
            {"ssid": "Pi_Setup_AP", "signal": "75", "security": "WPA2"},
        ]

    return jsonify({"success": True, "networks": networks})


@app.route("/api/network/lan", methods=["POST"])
def configure_lan():
    data = request.get_json(silent=True) or {}
    ip_address = str(data.get("ip", "10.0.0.1")).strip()
    subnet = str(data.get("subnet", "255.255.255.0")).strip()

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

    return jsonify({"success": True, "message": f"LAN eth1 configured to static {ip_address}."})


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


if __name__ == "__main__":
    app_host = os.getenv("APP_HOST", "0.0.0.0")
    app_port = int(os.getenv("APP_PORT", "5000"))
    app.run(host=app_host, port=app_port, debug=False)
