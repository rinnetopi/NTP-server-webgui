# NTP Server Web GUI (Flask)

Web UI and API for Raspberry Pi NTP/network administration using Flask and `nmcli`.

Current release version: **2.0.0**

For full clean-install setup on Raspberry Pi OS Trixie (users, chrony, service, proxy, DNS, and LAN-only access), see:

- [`docs/IMPLEMENTATION-GUIDE-RPI-TRIXIE.md`](docs/IMPLEMENTATION-GUIDE-RPI-TRIXIE.md)

## Bootstrap repository behavior

The bootstrap script treats `/opt/ntp-server-webgui-src` as a disposable/cacheable mirror. If local changes are present in the source repository, they will be automatically reset to match the remote origin state during bootstrap updates.

## Features

- Live system telemetry endpoint (`/api/system/status`) with temperature, load, uptime, and chrony state.
- WAN (`eth0`) configuration endpoint for DHCP or static mode.
- LAN (`eth1`) isolated subnet configuration endpoint.
- Wi-Fi scan and connect endpoints (`/api/network/wifi/scan`, `/api/network/wifi`).
- Wi-Fi DHCP/static handling with SSID connection + connection profile update.
- Service controls for chrony restart/reload and system reboot.

## Web UI Assets

The web UI is designed to work completely offline. All assets are vendored locally to ensure no external dependencies:

- Tailwind CSS: Local copy included in `static/assets/tailwind.min.js`
- Font Awesome: Local copy included in `static/assets/fontawesome.css`  
- Google Fonts: Local fallbacks for Inter font family

## API routes

| Route | Method | Purpose |
|---|---|---|
| `/api/system/status` | `GET` | Return telemetry and chrony status |
| `/api/system/ntp-logs` | `GET` | Get recent NTP logs |
| `/api/system/ntp-sources` | `GET` | Get current NTP sources information |
| `/api/system/service-status` | `GET` | Get status of all system services |
| `/api/system/resources` | `GET` | Get detailed system resource usage |
| `/api/network/wan` | `POST` | Set WAN DHCP or static config |
| `/api/network/lan` | `POST` | Set LAN static config |
| `/api/network/wifi/scan` | `GET` | Scan nearby Wi-Fi networks |
| `/api/network/wifi` | `POST` | Connect to Wi-Fi |
| `/api/service/ntp` | `POST` | Restart or reload chrony |
| `/api/service/reboot` | `POST` | Reboot the device |

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Open `http://127.0.0.1:5000`.

## Sandbox testbench (safe, no system changes)

Enable sandbox mode so API calls do not execute real `nmcli`, `systemctl`, or `reboot` commands:

```bash
export SANDBOX_MODE=true
python3 app.py
```

Then run the API/UI testbench:

```bash
python3 -m unittest discover -s tests -p "test_*.py"
```

Sandbox mode returns deterministic mock command responses for Wi-Fi scans, chrony tracking, and network/service operations, so you can validate frontend/backend integration safely on non-Pi machines.

## Raspberry Pi Trixie deployment (your profile)

This repo now defaults to:

- WAN: `eth0`
- Wi-Fi: `wlan0`
- LAN: `eth1`
- NTP service: `chrony`
- Flask bind: `127.0.0.1:5000` (for reverse proxy)

During bootstrap, installer now asks which Ethernet adapter should be WAN vs LAN (swap between `eth0` and `eth1`).

### One-command full bootstrap (clean Pi)

```bash
sudo REPO_URL=https://github.com/rinnetopi/NTP-server-webgui.git \
  REPO_BRANCH=main \
  WIFI_INTERFACE=wlan0 \
  LAN_IP=10.12.0.1 DOMAIN_NAME=ct.ntpserver.org \
  bash scripts/bootstrap_rpi_trixie.sh
```

This script will:

- install missing system dependencies
- clone/update repo into `/opt/ntp-server-webgui-src`
- create `NTP` user + sudo rules
- deploy app/service to `/opt/ntp-server-webgui`
- configure nginx + dnsmasq for `ct.ntpserver.org`
- enforce nftables isolation (NTP-only forwarding between interfaces)

If you want to skip the prompt and force mapping, pass explicit values:

```bash
sudo WAN_INTERFACE=eth1 LAN_INTERFACE=eth0 WIFI_INTERFACE=wlan0 \
  LAN_IP=10.12.0.1 DOMAIN_NAME=ct.ntpserver.org \
  bash scripts/bootstrap_rpi_trixie.sh
```

### 1. Create service user and privileged command rights

```bash
sudo bash scripts/setup_ntp_user.sh
```

This creates user `NTP` (no local password login) and grants passwordless sudo only for:

- `nmcli`
- `systemctl restart chrony`
- `systemctl reload chrony`
- `reboot`

### 2. Deploy app and systemd service

```bash
sudo bash scripts/setup_ntp_service.sh
```

This installs into `/opt/ntp-server-webgui`, creates a venv, installs dependencies, and starts `ntp-webgui.service`.

### 3. Enable proxy + local DNS for `ct.ntpserver.org` on closed LAN (`eth1`)

```bash
sudo LAN_INTERFACE=eth1 LAN_IP=10.12.0.1 DOMAIN_NAME=ct.ntpserver.org bash scripts/setup_proxy_dns.sh
```

What this does:

- Ensures `${LAN_INTERFACE}` has `${LAN_IP}/16` before validating nginx config.
- Nginx listens only on `10.12.0.1:80` and proxies to `127.0.0.1:5000`.
- dnsmasq resolves `ct.ntpserver.org` to `10.12.0.1` on `${LAN_INTERFACE}`.

Critical requirement for clients on the closed network:

- Their DNS server must be set to `10.12.0.1` (the Pi on `eth1`), otherwise `ct.ntpserver.org` will not resolve.

### 4. Enforce network isolation firewall (NTP-only forwarding)

```bash
sudo WAN_INTERFACE=eth0 WIFI_INTERFACE=wlan0 LAN_INTERFACE=eth1 bash scripts/setup_firewall_isolation.sh
```

This blocks forwarding between WAN/Wi-Fi and LAN by default and permits only UDP/123 (NTP) across interfaces.

## Run as a systemd service

1. Install app files:

   ```bash
   sudo cp -r . /opt/ntp-server-webgui
   ```

2. Install Python dependencies:

   ```bash
   cd /opt/ntp-server-webgui
   sudo python3 -m pip install -r requirements.txt
   ```

3. Install and enable service:

   ```bash
   sudo cp deploy/ntp-webgui.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now ntp-webgui
   ```

4. Inspect service logs:

   ```bash
   sudo systemctl status ntp-webgui
   journalctl -u ntp-webgui -f
   ```

## Security and permissions notes

- This app calls privileged commands (`nmcli`, `systemctl`, `reboot`) via `sudo`.
- The service unit includes Linux capabilities for network configuration and reboot control.
- Use least privilege and restrict deployment to trusted administrators.

## Deployment configuration values

- `WAN_CONNECTION_NAME` (default: `eth0`)
- `LAN_CONNECTION_NAME` (default: `eth1`)
- `WIFI_CONNECTION_NAME` (default: `wlan0`)
- Runtime mapping file: `/opt/ntp-server-webgui/runtime.env` (created by setup scripts and editable from UI)
- `NTP_SERVICE_NAMES` (default: `chrony`)
- Presence of `chronyc`, `nmcli`, and `systemctl` on target OS
- `sudo` permissions for the app service user (network changes, chrony control, reboot)