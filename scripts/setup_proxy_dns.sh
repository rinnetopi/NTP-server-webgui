#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/setup_proxy_dns.sh"
  exit 1
fi

LAN_INTERFACE="${LAN_INTERFACE:-}"
LAN_IP="${LAN_IP:-10.12.0.1}"
DOMAIN_NAME="${DOMAIN_NAME:-ct.ntpserver.org}"
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

apt-get update
apt-get install -y nginx dnsmasq

ensure_lan_profile() {
  local iface="$1"
  local profile

  if ! command -v nmcli >/dev/null 2>&1; then
    echo "ERROR: nmcli is required to configure LAN interface ${iface}."
    exit 1
  fi

  if ! ip link show "${iface}" >/dev/null 2>&1; then
    echo "ERROR: interface ${iface} does not exist."
    exit 1
  fi

  profile="$(nmcli -g GENERAL.CONNECTION device show "${iface}" 2>/dev/null | head -n1 || true)"
  if [[ -z "${profile}" ]]; then
    echo "Creating NetworkManager profile for ${iface}"
    nmcli con add type ethernet ifname "${iface}" con-name "${iface}" >/dev/null 2>&1 || true
    profile="$(nmcli -g GENERAL.CONNECTION device show "${iface}" 2>/dev/null | head -n1 || true)"
  fi

  if [[ -z "${profile}" ]]; then
    echo "ERROR: Unable to create a NetworkManager profile for ${iface}."
    exit 1
  fi

  echo "${profile}"
}

LAN_CONNECTION_NAME="$(ensure_lan_profile "${LAN_INTERFACE}")"

nmcli con modify "${LAN_CONNECTION_NAME}" \
  ipv4.addresses "${LAN_IP}/${LAN_PREFIX}" \
  ipv4.method manual \
  ipv4.gateway "" \
  ipv4.dns ""
nmcli con up "${LAN_CONNECTION_NAME}"

cat > /etc/nginx/sites-available/ntpserver <<EOF
server {
    listen ${LAN_IP}:80;
    server_name ${DOMAIN_NAME};

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/ntpserver /etc/nginx/sites-enabled/ntpserver
nginx -t
systemctl enable --now nginx
systemctl restart nginx

cat > /etc/dnsmasq.d/ntpserver.conf <<EOF
interface=${LAN_INTERFACE}
bind-interfaces
domain-needed
bogus-priv
local=/ct/
address=/${DOMAIN_NAME}/${LAN_IP}
EOF

systemctl enable --now dnsmasq
systemctl restart dnsmasq

echo "Proxy + local DNS configured."
echo "Clients on ${LAN_INTERFACE} must use DNS server ${LAN_IP} to resolve ${DOMAIN_NAME}."
