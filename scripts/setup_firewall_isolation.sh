#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/setup_firewall_isolation.sh"
  exit 1
fi

WAN_INTERFACE="${WAN_INTERFACE:-}"
WIFI_INTERFACE="${WIFI_INTERFACE:-wlan0}"
LAN_INTERFACE="${LAN_INTERFACE:-}"
NTP_PORT="${NTP_PORT:-123}"

apt-get update
apt-get install -y nftables

cat > /etc/nftables.conf <<EOF
#!/usr/sbin/nft -f
flush ruleset

table inet ntp_isolation {
  chain input {
    type filter hook input priority 0;
    policy accept;
  }

  chain forward {
    type filter hook forward priority 0;
    policy drop;

    ct state established,related accept

    # Allow only NTP forwarding between LAN and WAN/Wi-Fi.
    iifname "${LAN_INTERFACE}" oifname "${WAN_INTERFACE}" udp dport ${NTP_PORT} accept
    iifname "${WAN_INTERFACE}" oifname "${LAN_INTERFACE}" udp sport ${NTP_PORT} accept

    iifname "${LAN_INTERFACE}" oifname "${WIFI_INTERFACE}" udp dport ${NTP_PORT} accept
    iifname "${WIFI_INTERFACE}" oifname "${LAN_INTERFACE}" udp sport ${NTP_PORT} accept
  }

  chain output {
    type filter hook output priority 0;
    policy accept;
  }
}
EOF

nft -f /etc/nftables.conf
systemctl enable --now nftables
systemctl restart nftables

echo "Firewall isolation applied."
echo "Forwarding is DROP by default; only UDP/${NTP_PORT} is allowed between:"
echo "  - ${LAN_INTERFACE} <-> ${WAN_INTERFACE}"
echo "  - ${LAN_INTERFACE} <-> ${WIFI_INTERFACE}"
