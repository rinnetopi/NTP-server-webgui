# Full implementation guide (Raspberry Pi OS Trixie, clean install)

This guide builds a complete NTP + web management stack on a fresh Raspberry Pi OS Trixie install for this project.

It configures:

- Chrony NTP service (`chrony`)
- Flask web backend (`ntp-webgui`)
- Dedicated service user (`NTP`) with scoped sudo rights
- Reverse proxy (`nginx`)
- Local DNS for `ct.ntpserver.org` on LAN (`dnsmasq`)
- Inter-interface firewall isolation (`nftables`)
- Closed LAN access path via `eth1` only

---

## 1. Target topology

- **WAN**: `eth0` (internet/upstream)
- **Wi-Fi**: `wlan0` (optional upstream)
- **LAN**: `eth1` (isolated local subnet for clients)
- **Web UI ingress**: `http://ct.ntpserver.org` on the **eth1 network only**

Example LAN:

- Pi `eth1`: `10.12.0.1/16`
- DNS for clients: `10.12.0.1`
- UI URL for clients: `http://ct.ntpserver.org`

---

## 2. Bootstrap repository behavior

The bootstrap script treats `/opt/ntp-server-webgui-src` as a disposable/cacheable mirror. If local changes are present in the source repository, they will be automatically reset to match the remote origin state during bootstrap updates.

---

## 3. Clean OS preparation

Run on the Pi as a sudo-capable user:

```bash
sudo apt-get update
sudo apt-get -y full-upgrade
sudo reboot
```

After reboot:

```bash
sudo apt-get update
sudo apt-get install -y \
  git rsync curl ca-certificates \
  python3 python3-venv python3-pip \
  chrony network-manager \
  nginx dnsmasq
```

Enable/ensure required services:

```bash
sudo systemctl enable --now chrony
sudo systemctl enable --now NetworkManager
```

---

## 3. Get project files onto the Pi

Choose one:

### Option A: clone directly

```bash
cd /tmp
git clone <YOUR_REPO_URL> ntp-server-webgui
cd ntp-server-webgui
```

### Option B: copy from your dev machine

Copy the repository to the Pi, then `cd` into it.

### Fast path: all-in-one bootstrap script

If you want automated end-to-end setup on a clean Pi, run:

```bash
sudo REPO_URL=https://github.com/rinnetopi/NTP-server-webgui.git \
  REPO_BRANCH=main \
  WIFI_INTERFACE=wlan0 \
  LAN_IP=10.12.0.1 DOMAIN_NAME=ct.ntpserver.org \
  bash scripts/bootstrap_rpi_trixie.sh
```

This executes dependency installation, repo sync, user/sudo setup, service deployment, proxy+DNS, and firewall isolation in one flow.
The bootstrap script asks whether WAN/LAN should be `eth0/eth1` or `eth1/eth0`. To skip the prompt, pass explicit `WAN_INTERFACE` and `LAN_INTERFACE`.

---

## 4. Configure Chrony for LAN serving

Create a LAN server profile for chrony:

```bash
sudo tee /etc/chrony/conf.d/ntp-webgui-lan.conf >/dev/null <<'EOF'
# Allow LAN clients from eth1 subnet
allow 10.12.0.0/16

# Keep serving time even if upstream is temporarily unavailable
local stratum 10
EOF
```

Restart and verify:

```bash
sudo systemctl restart chrony
chronyc tracking
chronyc sources -v
```

If your LAN subnet differs, change `allow` accordingly.

---

## 5. Create dedicated service user and sudo policy

From repo root:

```bash
sudo bash scripts/setup_ntp_user.sh
```

What it does:

- Creates `NTP` user (non-login shell).
- Removes/locks local password.
- Adds scoped passwordless sudo permissions for exactly:
  - `/usr/bin/nmcli`
  - `/usr/bin/systemctl restart chrony`
  - `/usr/bin/systemctl reload chrony`
  - `/usr/sbin/reboot` and `/usr/bin/reboot`

---

## 6. Deploy app and systemd service

From repo root:

```bash
sudo bash scripts/setup_ntp_service.sh
```

What it does:

- Installs app to `/opt/ntp-server-webgui`
- Creates virtual environment
- Installs Python requirements
- Installs and starts `ntp-webgui.service`

Check service:

```bash
sudo systemctl status ntp-webgui --no-pager
journalctl -u ntp-webgui -f
```

---

## 7. Configure proxy + DNS for `ct.ntpserver.org`

From repo root:

```bash
sudo LAN_INTERFACE=eth1 LAN_IP=10.12.0.1 DOMAIN_NAME=ct.ntpserver.org \
  bash scripts/setup_proxy_dns.sh
```

What it does:

- Ensures `${LAN_INTERFACE}` has `${LAN_IP}/16` before validating nginx config.
- Nginx listens on `10.12.0.1:80` and proxies to `127.0.0.1:5000`
- dnsmasq maps `ct.ntpserver.org -> 10.12.0.1` on `${LAN_INTERFACE}`

Validate:

```bash
sudo nginx -t
sudo systemctl status nginx --no-pager
sudo systemctl status dnsmasq --no-pager
```

---

## 8. Apply firewall isolation (allow only NTP between interfaces)

From repo root:

```bash
sudo WAN_INTERFACE=eth0 WIFI_INTERFACE=wlan0 LAN_INTERFACE=eth1 \
  bash scripts/setup_firewall_isolation.sh
```

What this does:

- Installs/enables `nftables`.
- Sets `FORWARD` default policy to `drop`.
- Allows only UDP/123 forwarding between:
  - `eth1` <-> `eth0`
  - `eth1` <-> `wlan0`

Result:

- Internet traffic from WAN/Wi-Fi is blocked from flooding into the closed `eth1` network.
- Only NTP traffic is forwarded between those networks.

Validate:

```bash
sudo nft list ruleset
```

---

## 9. LAN client requirements (critical)

Because there is no external DNS in this closed network, each LAN client must use:

- **DNS server**: `10.12.0.1` (Pi `eth1` address)

Then clients can open:

- `http://ct.ntpserver.org`

If clients do not use Pi DNS, hostname resolution will fail.

---

## 10. Optional: provide DHCP on `eth1` from Pi

If you want clients to auto-receive IP + DNS, add a dedicated dnsmasq DHCP config:

```bash
sudo tee /etc/dnsmasq.d/ntpserver-dhcp.conf >/dev/null <<'EOF'
interface=eth1
dhcp-range=10.12.0.50,10.12.0.200,255.255.0.0,24h
dhcp-option=option:router,10.12.0.1
dhcp-option=option:dns-server,10.12.0.1
EOF
```

Then:

```bash
sudo systemctl restart dnsmasq
```

Use this only if you want the Pi to manage addressing on the LAN.

---

## 11. Production hardening checklist

1. Confirm service runs as `NTP`:
   ```bash
   systemctl show ntp-webgui -p User
   ```
2. Confirm Flask is not directly internet-exposed:
   ```bash
   sudo ss -ltnp | grep 5000
   ```
   It should bind to `127.0.0.1:5000`.
3. Confirm nginx listens only on LAN IP:
   ```bash
   sudo ss -ltnp | grep ':80'
   ```
4. Confirm sudoers file is valid and scoped:
   ```bash
   sudo visudo -cf /etc/sudoers.d/ntp-webgui
   sudo cat /etc/sudoers.d/ntp-webgui
   ```
5. Confirm chrony service health:
   ```bash
   systemctl status chrony --no-pager
   chronyc tracking
   ```
6. Confirm forwarding policy and NTP-only exception:
   ```bash
   sudo nft list ruleset | sed -n '/chain forward/,/}/p'
   ```

---

## 12. End-to-end verification flow

1. On Pi:
   - `curl -I http://10.12.0.1`
   - `curl -I http://127.0.0.1:5000`
2. From LAN client on eth1 network:
   - `nslookup ct.ntpserver.org 10.12.0.1`
   - open `http://ct.ntpserver.org`
3. In UI:
   - verify status updates
   - test WAN DHCP/static switch
   - test Wi-Fi scan/connect (default DHCP, static optional)
   - test LAN static update
   - test Chrony restart action

---

## 13. Troubleshooting

### `ct.ntpserver.org` not resolving

- Client DNS not set to `10.12.0.1`
- dnsmasq not running
- `dnsmasq.d` config typo

Checks:

```bash
sudo systemctl status dnsmasq --no-pager
sudo journalctl -u dnsmasq -n 100 --no-pager
```

### Web UI unreachable

- nginx not listening on LAN IP
- `eth1` IP differs from expected

Checks:

```bash
ip a show eth1
sudo nginx -t
sudo systemctl status nginx --no-pager
```

### Network actions fail in UI

- Missing/incorrect NetworkManager connection names
- sudoers restrictions not applied

Checks:

```bash
nmcli con show
sudo -u NTP sudo -n /usr/bin/nmcli con show
sudo -u NTP sudo -n /usr/bin/systemctl restart chrony
```

### Unexpected traffic crossing between interfaces

- nftables not active
- custom rules overwritten by another firewall manager

Checks:

```bash
sudo systemctl status nftables --no-pager
sudo nft list ruleset
```

Re-apply:

```bash
sudo WAN_INTERFACE=eth0 WIFI_INTERFACE=wlan0 LAN_INTERFACE=eth1 \
  bash scripts/setup_firewall_isolation.sh
```

### Chrony actions fail

- service name mismatch (`chrony` vs `chronyd`)
- chrony not installed/running

Checks:

```bash
systemctl status chrony --no-pager
```

If needed, set `NTP_SERVICE_NAMES` in `/etc/systemd/system/ntp-webgui.service`.

---

## 14. Upgrade workflow

After pulling updated repo content:

```bash
cd <repo-root>
sudo bash scripts/setup_ntp_service.sh
sudo systemctl restart ntp-webgui
```

Then verify service and UI accessibility again.

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

To run the application in development mode, execute:

```bash
python3 app.py
```

The web UI will be accessible at [http://localhost:5000](http://localhost:5000) and all API endpoints are available under `/api/`.
