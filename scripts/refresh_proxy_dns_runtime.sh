#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/refresh_proxy_dns_runtime.sh"
  exit 1
fi

LAN_INTERFACE="${LAN_INTERFACE:-eth1}"
LAN_IP="${LAN_IP:-10.12.0.1}"
DOMAIN_NAME="${DOMAIN_NAME:-ct.ntpserver.org}"

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

cat > /etc/dnsmasq.d/ntpserver.conf <<EOF
interface=${LAN_INTERFACE}
bind-interfaces
domain-needed
bogus-priv
local=/ct/
address=/${DOMAIN_NAME}/${LAN_IP}
EOF

echo "Runtime proxy + local DNS config updated."