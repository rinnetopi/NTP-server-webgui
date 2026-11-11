#!/usr/bin/env bash
# Runs on the Pi (called by deploy.ps1): checks the interfaces, runs the
# bootstrap installer from this copy, then prints post-install checks.
# Usage: bash deploy/remote_install.sh <WAN_IF> <LAN_IF> <LAN_IP> [LAN_PREFIX]
set -euo pipefail

WAN_IF="${1:-eth0}"
LAN_IF="${2:-eth1}"
LAN_IP="${3:-10.12.0.1}"
LAN_PREFIX="${4:-16}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

section() { printf '\n===== %s =====\n' "$*"; }

# Guard against Windows line endings sneaking in.
find "${REPO_DIR}" -type f \( -name '*.sh' -o -name '*.service' -o -name '*.py' -o -name 'ntp-webgui-lan-refresh' \) -exec sed -i 's/\r$//' {} +

section "Pi before install"
grep PRETTY_NAME /etc/os-release || true
tr -d '\0' < /proc/device-tree/model 2>/dev/null && echo
ip -br link
ip -br -4 addr
ip route show default || true
nmcli -t -f DEVICE,TYPE,STATE,CONNECTION device 2>/dev/null || echo "(nmcli not available yet)"

for iface in "${WAN_IF}" "${LAN_IF}"; do
  if ! ip link show "${iface}" >/dev/null 2>&1; then
    echo "ERROR: interface ${iface} not found on the Pi. Re-run deploy.ps1 with -Wan/-Lan set to the names listed above."
    exit 1
  fi
done
if [[ "${WAN_IF}" == "${LAN_IF}" ]]; then
  echo "ERROR: WAN and LAN must be different interfaces."
  exit 1
fi

# Ask for the sudo password once, on its own line, before the long install output.
sudo -v -p $'\nEnter the Pi password for sudo (%u) and press Enter:\n'

section "Running bootstrap (WAN=${WAN_IF}, LAN=${LAN_IF}, LAN_IP=${LAN_IP}/${LAN_PREFIX})"
sudo WAN_INTERFACE="${WAN_IF}" LAN_INTERFACE="${LAN_IF}" LAN_IP="${LAN_IP}" LAN_PREFIX="${LAN_PREFIX}" \
  bash "${REPO_DIR}/scripts/bootstrap_rpi_trixie.sh"

section "Post-install checks"
sleep 3
for svc in ntp-webgui chrony NetworkManager nftables nginx dnsmasq; do
  printf '%-16s %s\n' "${svc}" "$(systemctl is-active "${svc}" 2>/dev/null || true)"
done
echo
ip -br -4 addr
echo
code() { curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$@" || echo fail; }
printf 'login page  http://%s:5000/login   -> %s (expect 200)\n' "${LAN_IP}" "$(code "http://${LAN_IP}:5000/login")"
printf 'no session  /api/network/current       -> %s (expect 401)\n' "$(code http://127.0.0.1:5000/api/network/current)"
printf 'nginx/dnsmasq packages still installed: %s\n' "$(pkgs="$(dpkg -l nginx dnsmasq 2>/dev/null | awk '/^ii/ {printf "%s ", $2}')"; echo "${pkgs:-none}")"
echo "credentials file: $(sudo ls -l /var/lib/ntp-webgui/credentials.json 2>/dev/null || echo 'not created yet ')"
echo
echo "Service user sudo check (should list devices):"
sudo -u ntp sudo -n /usr/bin/nmcli -t -f DEVICE,STATE device 2>&1 | head -n 5 || true
echo
echo "runtime.env:"; sudo cat /opt/ntp-server-webgui/runtime.env || true
section "ntp-webgui log (last 30 lines)"
sudo journalctl -u ntp-webgui -n 30 --no-pager || true
section "Done"
