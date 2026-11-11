# NTP Server Web GUI (Flask)

Web UI and API for Raspberry Pi NTP/network administration using Flask and `nmcli`.

Current release version: **3.0.0**

For full clean-install setup on Raspberry Pi OS Trixie (users, chrony, service, LAN address and firewall), see:

- [`docs/IMPLEMENTATION-GUIDE-RPI-TRIXIE.md`](docs/IMPLEMENTATION-GUIDE-RPI-TRIXIE.md)

## Bootstrap repository behavior

By default the bootstrap script installs from the copy of the repository it is run from (for example a folder copied to the Pi with `scp`, or by `deploy.ps1`). Set `USE_LOCAL_SOURCE=0` to clone from `REPO_URL` instead; in that mode `/opt/ntp-server-webgui-src` is treated as a disposable mirror and local changes in it are reset to the remote state.

## Deploy from a Windows PC

`deploy.ps1` copies this folder to the Pi with `scp`, runs the bootstrap as root over `ssh`, and writes everything to `deploy.log`:

```powershell
.\deploy.ps1                       # defaults: ntp-admin@192.168.1.239
.\deploy.ps1 -PiHost 192.168.1.50  # another Pi
```

## Login

The UI and every `/api/` route require a login. The default account is **admin / admin**; change it with **Change Password** in the UI.
Credentials are stored on the Pi in `/var/lib/ntp-webgui/credentials.json` (mode 600, owned by the service user, password stored as a salted scrypt hash, never in plain text). The file survives re-deploys. To reset to admin/admin, delete it and restart the service:

```bash
sudo rm /var/lib/ntp-webgui/credentials.json && sudo systemctl restart ntp-webgui
```

## Features

- Live system telemetry endpoint (`/api/system/status`) with temperature, load, uptime, and chrony state.
- WAN (`eth0`) configuration endpoint for DHCP or static mode (static supports gateway and DNS).
- LAN (`eth1`) isolated subnet configuration endpoint.
- Wi-Fi scan and connect endpoints (`/api/network/wifi/scan`, `/api/network/wifi`).
- Wi-Fi DHCP/static handling with SSID connection + connection profile update.
- Service controls for chrony restart/reload and system reboot.
- Login with a changeable password.

## Web UI Assets

The web UI works completely offline: styles and icons (inline SVG) live in `templates/index.html`, and the Inter font is served locally from `static/assets/site.css` and `static/assets/fonts/`.

## API routes

| Route | Method | Purpose |
|---|---|---|
| `/login`, `/logout` | `GET`/`POST`, `POST` | Sign in / sign out (all other routes need a session) |
| `/api/auth/password` | `POST` | Change password (`current_password`, `new_password`, `confirm_password`) |
| `/api/system/status` | `GET` | Return telemetry and chrony status |
| `/api/system/ntp-logs` | `GET` | Get recent NTP logs |
| `/api/system/ntp-sources` | `GET` | Get current NTP sources information |
| `/api/system/service-status` | `GET` | Get status of all system services |
| `/api/system/resources` | `GET` | Get detailed system resource usage |
| `/api/network/interfaces` | `GET`/`POST` | Read or swap the WAN/LAN interface roles |
| `/api/network/current` | `GET` | Current IPv4 address and DHCP/static mode per role |
| `/api/network/wan` | `POST` | Set WAN DHCP or static config (`ip`, `subnet`, optional `gateway`, `dns`) |
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
export STATE_DIRECTORY="$PWD/.state" RUNTIME_ENV_PATH="$PWD/.state/runtime.env"
python3 app.py
```

Open `http://127.0.0.1:5000` and sign in with `admin` / `admin`. Login data is kept in `.state/` (git-ignored).

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
- Web UI: Flask on `0.0.0.0:5000`, reachable from both WAN and LAN (`http://<pi-address>:5000`)

During bootstrap, installer now asks which Ethernet adapter should be WAN vs LAN (swap between `eth0` and `eth1`).

### One-command full bootstrap (clean Pi)

```bash
sudo REPO_URL=https://github.com/rinnetopi/NTP-server-webgui.git \
  REPO_BRANCH=main \
  WIFI_INTERFACE=wlan0 \
  LAN_IP=10.12.0.1 \
  bash scripts/bootstrap_rpi_trixie.sh
```

This script will:

- install missing system dependencies
- use the local copy (or clone/update into `/opt/ntp-server-webgui-src` with `USE_LOCAL_SOURCE=0`)
- create `ntp` user + sudo rules
- deploy app/service to `/opt/ntp-server-webgui`
- set the LAN interface to `LAN_IP/LAN_PREFIX` (default `10.12.0.1/16`) and point chrony's `allow` rule at that subnet
- enforce nftables isolation (NTP-only forwarding between interfaces)

If you want to skip the prompt and force mapping, pass explicit values:

```bash
sudo WAN_INTERFACE=eth1 LAN_INTERFACE=eth0 WIFI_INTERFACE=wlan0 \
  LAN_IP=10.12.0.1 \
  bash scripts/bootstrap_rpi_trixie.sh
```

### 1. Create service user and privileged command rights

```bash
sudo bash scripts/setup_ntp_user.sh
```

This creates user `ntp` (no local password login) and grants passwordless sudo only for:

- `nmcli`
- `systemctl restart chrony`
- `systemctl reload chrony`
- `reboot`
- `/usr/local/sbin/ntp-webgui-lan-refresh` (updates chrony's LAN `allow` rule after a LAN IP change)

and adds it to the `systemd-journal` group so the UI can read chrony logs.

### 2. Deploy app and systemd service

```bash
sudo bash scripts/setup_ntp_service.sh
```

This installs into `/opt/ntp-server-webgui`, creates a venv, installs dependencies, and starts `ntp-webgui.service`.

### 3. Configure the LAN interface

```bash
sudo LAN_INTERFACE=eth1 LAN_IP=10.12.0.1 LAN_PREFIX=16 bash scripts/setup_lan.sh
```

What this does:

- Sets `${LAN_INTERFACE}` to `${LAN_IP}/${LAN_PREFIX}` with no gateway and no DNS (closed network) and removes duplicate NetworkManager profiles for that port.
- Installs `/usr/local/sbin/ntp-webgui-lan-refresh` (root-owned) and runs it so chrony serves the LAN subnet. The UI runs it via sudo after a LAN IP change.
- Removes the nginx/dnsmasq setup used by earlier versions (set `KEEP_NGINX_DNSMASQ=1` to keep the packages installed).

LAN clients open the UI at `http://10.12.0.1:5000` and use `10.12.0.1` as their NTP server.

### 4. Enforce network isolation firewall (NTP-only forwarding)

```bash
sudo WAN_INTERFACE=eth0 WIFI_INTERFACE=wlan0 LAN_INTERFACE=eth1 bash scripts/setup_firewall_isolation.sh
```

This blocks forwarding between WAN/Wi-Fi and LAN by default and permits only UDP/123 (NTP) across interfaces.

## Run as a systemd service

`scripts/setup_ntp_service.sh` installs the app into `/opt/ntp-server-webgui` with its own virtualenv and (re)starts `ntp-webgui.service`. Inspect it with:

   ```bash
   sudo systemctl status ntp-webgui
   journalctl -u ntp-webgui -f
   ```

## Security and permissions notes

- This app calls privileged commands (`nmcli`, `systemctl`, `reboot`) via `sudo`.
- The service unit includes Linux capabilities for network configuration and reboot control.
- Use least privilege and restrict deployment to trusted administrators.

## Deployment configuration values

- `WAN_CONNECTION_NAME` (default: `eth0`), `LAN_CONNECTION_NAME` (default: `eth1`), `WIFI_CONNECTION_NAME` (default: `wlan0`): despite the names these are **device** names; the app looks up the NetworkManager profile active on each device.
- Runtime mapping file: `/opt/ntp-server-webgui/runtime.env` (created by setup scripts and editable from UI)
- `NTP_SERVICE_NAMES` (default: `chrony`)
- Presence of `chronyc`, `nmcli`, and `systemctl` on target OS
- `sudo` permissions for the app service user (network changes, chrony control, reboot)