# Full implementation guide (Raspberry Pi OS Trixie, clean install)

This guide sets up the complete NTP server + web management stack on Raspberry Pi OS Trixie.

It configures:

- Chrony NTP service (`chrony`), serving the LAN subnet
- Flask web UI/API (`ntp-webgui`) on port 5000, behind a login
- Dedicated service user (`ntp`) with scoped sudo rights
- Static, closed LAN interface (no gateway, no DNS)
- Inter-interface firewall isolation (`nftables`)

---

## 1. Target topology

- **WAN**: `eth0` (internet/upstream NTP, usually DHCP)
- **Wi-Fi**: `wlan0` (optional upstream)
- **LAN**: `eth1` (isolated subnet for NTP clients), default `10.12.0.1/16`
- **Web UI**: `http://<pi-address>:5000` from both WAN and LAN, e.g. `http://10.12.0.1:5000`
- **Login**: default `admin` / `admin` (change it in the UI)

---

## 2. Install

### Option A: from a Windows PC (recommended)

From the project folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy.ps1
```

`deploy.ps1` copies the project to the Pi with `scp`, runs `deploy/remote_install.sh` over `ssh` (which checks the interfaces and runs the bootstrap), and writes everything to `deploy.log`. Parameters: `-PiHost`, `-User`, `-Wan`, `-Lan`, `-LanIp`, `-LanPrefix`.

### Option B: on the Pi

Copy or clone the project to the Pi, then from its root:

```bash
sudo WAN_INTERFACE=eth0 LAN_INTERFACE=eth1 LAN_IP=10.12.0.1 LAN_PREFIX=16 \
  bash scripts/bootstrap_rpi_trixie.sh
```

By default the bootstrap installs from the copy it is run from. With `USE_LOCAL_SOURCE=0` it clones `REPO_URL` into `/opt/ntp-server-webgui-src` instead, which is treated as a disposable mirror (local changes are reset to the remote state).

The bootstrap runs these steps, which can also be run on their own:

| Step | Script | What it does |
|---|---|---|
| Packages | (bootstrap) | Installs git, rsync, python3-venv, chrony, network-manager, nftables, ... |
| Chrony | (bootstrap) | Enables chrony; LAN `allow` rule + `local stratum 10` |
| Users and sudo | `scripts/setup_ntp_user.sh` | Creates `ntp` (service, no login) and `ntp-admin`; sudoers in `/etc/sudoers.d/ntp-webgui`; `ntp` joins `systemd-journal` |
| App and service | `scripts/setup_ntp_service.sh` | Syncs the app to `/opt/ntp-server-webgui`, creates the venv, installs and restarts `ntp-webgui.service` |
| LAN | `scripts/setup_lan.sh` | Sets `LAN_IP/LAN_PREFIX` on the LAN port, removes duplicate profiles, installs `ntp-webgui-lan-refresh`, removes the nginx/dnsmasq setup of earlier versions |
| Firewall | `scripts/setup_firewall_isolation.sh` | nftables: forwarding dropped except UDP/123 between LAN and WAN/Wi-Fi |

---

## 3. Service user and sudo policy

`ntp` may run, without a password, only:

- `/usr/bin/nmcli`
- `/usr/bin/systemctl restart chrony`, `/usr/bin/systemctl reload chrony`
- `/usr/sbin/reboot`, `/usr/bin/reboot`
- `/usr/local/sbin/ntp-webgui-lan-refresh` (root-owned; validates its arguments and only rewrites chrony's LAN `allow` rule)

Check:

```bash
sudo visudo -cf /etc/sudoers.d/ntp-webgui
sudo -u ntp sudo -n /usr/bin/nmcli -t -f DEVICE,STATE device
```

---

## 4. Login and credentials

- Every page and `/api/` route requires a session; unauthenticated API calls get HTTP 401.
- Credentials: `/var/lib/ntp-webgui/credentials.json` (systemd `StateDirectory`, mode 600, owner `ntp`). The password is stored as a salted scrypt hash. The file survives re-deploys.
- Session signing key: `/var/lib/ntp-webgui/secret_key` (generated on first start).
- Change the password with **Change Password** in the UI (minimum 6 characters).
- Reset to `admin` / `admin`:

  ```bash
  sudo rm /var/lib/ntp-webgui/credentials.json && sudo systemctl restart ntp-webgui
  ```

The UI is served over plain HTTP, so the password crosses the network unencrypted. Keep access to trusted networks.

---

## 5. LAN client requirements

- Static address in the LAN subnet (for example `10.12.0.50/16`); the Pi does not hand out addresses on the LAN.
- NTP server: `10.12.0.1` (the Pi's LAN address).
- Web UI: `http://10.12.0.1:5000`.

When the LAN address is changed in the UI, the page moves to the new address automatically (you sign in again there, since the session belongs to the old address), and chrony's `allow` rule follows the new subnet.

---

## 6. Verification

On the Pi:

```bash
systemctl status ntp-webgui chrony nftables --no-pager
ip -4 addr show eth1
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/login              # 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/api/network/current # 401 without login
chronyc tracking
cat /etc/chrony/conf.d/ntp-webgui-lan.conf
sudo nft list ruleset | sed -n '/chain forward/,/}/p'
```

From a LAN client: open `http://10.12.0.1:5000`, sign in, and check that the status panel updates.

---

## 7. Troubleshooting

### Web UI unreachable

```bash
systemctl status ntp-webgui --no-pager
journalctl -u ntp-webgui -n 100 --no-pager
ss -ltnp | grep 5000
```

The UI is also reachable on the WAN address (`http://<wan-ip>:5000`), which helps if the LAN address was changed to something unexpected.

### Network actions fail in the UI

```bash
nmcli -t -f NAME,UUID,DEVICE connection show
sudo -u ntp sudo -n /usr/bin/nmcli con show
```

The app edits the NetworkManager profile that is active on each device (by UUID), so duplicate profile names are not a problem.

### LAN clients cannot sync time

```bash
cat /etc/chrony/conf.d/ntp-webgui-lan.conf   # allow rule must match the LAN subnet
sudo /usr/local/sbin/ntp-webgui-lan-refresh 10.12.0.1 16
chronyc clients
```

### Chrony actions fail

Check the service name (`chrony` vs `chronyd`) and set `NTP_SERVICE_NAMES` in the service unit if needed.

---

## 8. Upgrade workflow

Run `deploy.ps1` again (or the bootstrap on the Pi). It re-syncs the app, restarts the service and keeps the credentials.

## API routes

| Route | Method | Purpose |
|---|---|---|
| `/login`, `/logout` | `GET`/`POST`, `POST` | Sign in / sign out |
| `/api/auth/password` | `POST` | Change password |
| `/api/system/status` | `GET` | Telemetry and chrony status |
| `/api/system/ntp-logs` | `GET` | Recent chrony log lines |
| `/api/system/ntp-sources` | `GET` | `chronyc sources -v` |
| `/api/system/service-status` | `GET` | State of ntp-webgui, chrony, nftables, NetworkManager |
| `/api/system/resources` | `GET` | Load, memory and disk usage |
| `/api/network/interfaces` | `GET`/`POST` | Read or swap the WAN/LAN interface roles |
| `/api/network/current` | `GET` | Current IPv4 address and DHCP/static mode per role |
| `/api/network/wan` | `POST` | WAN DHCP or static (`ip`, `subnet`, optional `gateway`, `dns`) |
| `/api/network/lan` | `POST` | LAN static address |
| `/api/network/wifi/scan` | `GET` | Scan Wi-Fi networks |
| `/api/network/wifi` | `POST` | Connect to Wi-Fi (DHCP or static) |
| `/api/service/ntp` | `POST` | Restart or reload chrony |
| `/api/service/reboot` | `POST` | Reboot the Pi |
