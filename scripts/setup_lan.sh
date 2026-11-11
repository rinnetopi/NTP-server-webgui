#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/setup_lan.sh"
  exit 1
fi

LAN_INTERFACE="${LAN_INTERFACE:-}"
LAN_IP="${LAN_IP:-10.12.0.1}"
LAN_PREFIX="${LAN_PREFIX:-16}"

if [[ -z "${LAN_INTERFACE}" ]]; then
  WAN_INTERFACE="${WAN_INTERFACE:-$(ip route show default 2>/dev/null | awk '/default/ {print $5; exit}')}"
  if [[ -z "${WAN_INTERFACE}" ]]; then
    LAN_INTERFACE="eth1"
  else
    for iface in $(ls /sys/class/net 2>/dev/null | sort); do
      if [[ "${iface}" == "lo" || "${iface}" == "${WAN_INTERFACE}" ]]; then
        continue
      fi
      if [[ "${iface}" == eth* || "${iface}" == en* ]]; then
        LAN_INTERFACE="${iface}"
        break
      fi
    done
    if [[ -z "${LAN_INTERFACE}" ]]; then
      LAN_INTERFACE="eth1"
    fi
  fi
fi

profiles_for_iface() {
  # UUIDs of all profiles bound to an interface (names may be duplicated, UUIDs are not)
  local iface="$1" uuid
  for uuid in $(nmcli -g UUID connection show 2>/dev/null); do
    if [[ "$(nmcli -g connection.interface-name connection show "${uuid}" 2>/dev/null)" == "${iface}" ]]; then
      echo "${uuid}"
    fi
  done
}

ensure_lan_profile() {
  local iface="$1"
  local uuid

  if ! command -v nmcli >/dev/null 2>&1; then
    echo "ERROR: nmcli is required to configure LAN interface ${iface}." >&2
    exit 1
  fi

  if ! ip link show "${iface}" >/dev/null 2>&1; then
    echo "ERROR: interface ${iface} does not exist." >&2
    exit 1
  fi

  uuid="$(nmcli -g GENERAL.CON-UUID device show "${iface}" 2>/dev/null | head -n1 || true)"
  if [[ -z "${uuid}" ]]; then
    uuid="$(profiles_for_iface "${iface}" | head -n1)"
  fi
  if [[ -z "${uuid}" ]]; then
    echo "Creating NetworkManager profile for ${iface}" >&2
    nmcli con add type ethernet ifname "${iface}" con-name "${iface}" >/dev/null
    uuid="$(profiles_for_iface "${iface}" | head -n1)"
  fi
  if [[ -z "${uuid}" ]]; then
    echo "ERROR: Unable to create a NetworkManager profile for ${iface}." >&2
    exit 1
  fi

  # Remove duplicate profiles for the LAN port; otherwise NetworkManager may pick
  # a stale (e.g. DHCP) one after a reboot and the LAN address disappears.
  local other
  for other in $(profiles_for_iface "${iface}"); do
    if [[ "${other}" != "${uuid}" ]]; then
      echo "Removing duplicate profile ${other} ($(nmcli -g connection.id connection show "${other}")) for ${iface}" >&2
      nmcli connection delete "${other}" >/dev/null
    fi
  done

  echo "${uuid}"
}

LAN_CONNECTION_NAME="$(ensure_lan_profile "${LAN_INTERFACE}")"

nmcli con modify "${LAN_CONNECTION_NAME}" \
  connection.autoconnect yes \
  ipv4.addresses "${LAN_IP}/${LAN_PREFIX}" \
  ipv4.method manual \
  ipv4.gateway "" \
  ipv4.dns "" \
  ipv4.never-default yes
if ! nmcli con up "${LAN_CONNECTION_NAME}"; then
  echo "WARN: could not activate ${LAN_CONNECTION_NAME} now (cable unplugged?). It will come up when the link is connected."
fi

# Root-owned helper that keeps chrony's 'allow' rule on the LAN subnet.
# The web UI calls it (via sudo) after a LAN IP change.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install -o root -g root -m 755 "${SCRIPT_DIR}/ntp-webgui-lan-refresh" /usr/local/sbin/ntp-webgui-lan-refresh
/usr/local/sbin/ntp-webgui-lan-refresh "${LAN_IP}" "${LAN_PREFIX}"

# Remove the nginx proxy and dnsmasq DNS used by earlier versions of this app.
# The UI is served directly by Flask on port 5000.
for svc in nginx dnsmasq; do
  if systemctl list-unit-files "${svc}.service" >/dev/null 2>&1; then
    systemctl disable --now "${svc}" >/dev/null 2>&1 || true
  fi
done
rm -f /etc/nginx/sites-enabled/ntpserver /etc/nginx/sites-available/ntpserver \
      /etc/dnsmasq.d/ntpserver.conf /etc/sysctl.d/90-ntp-webgui.conf /etc/ntp-webgui/lan.conf
rmdir /etc/ntp-webgui 2>/dev/null || true
if [[ "${KEEP_NGINX_DNSMASQ:-0}" != "1" ]]; then
  pkgs=()
  for pkg in nginx nginx-common nginx-core dnsmasq; do
    if dpkg -s "${pkg}" >/dev/null 2>&1; then
      pkgs+=("${pkg}")
    fi
  done
  if (( ${#pkgs[@]} > 0 )); then
    echo "Removing packages no longer used: ${pkgs[*]}"
    DEBIAN_FRONTEND=noninteractive apt-get purge -y "${pkgs[@]}"
  fi
fi

echo "LAN ${LAN_INTERFACE} configured: ${LAN_IP}/${LAN_PREFIX} (no gateway, no DNS)."
echo "Web UI on the LAN: http://${LAN_IP}:5000"
