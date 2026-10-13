#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/bootstrap_rpi_trixie.sh"
  exit 1
fi

REPO_URL="${REPO_URL:-https://github.com/rinnetopi/NTP-server-webgui.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
SRC_DIR="${SRC_DIR:-/opt/ntp-server-webgui-src}"
APP_DIR="${APP_DIR:-/opt/ntp-server-webgui}"

WAN_INTERFACE="${WAN_INTERFACE:-eth0}"
WIFI_INTERFACE="${WIFI_INTERFACE:-wlan0}"
LAN_INTERFACE="${LAN_INTERFACE:-eth1}"
LAN_IP="${LAN_IP:-10.0.0.1}"
DOMAIN_NAME="${DOMAIN_NAME:-ntpserver.ct}"
SERVICE_USER="${SERVICE_USER:-NTP}"

PACKAGES=(
  git
  rsync
  curl
  ca-certificates
  sudo
  python3
  python3-venv
  python3-pip
  chrony
  network-manager
  nginx
  dnsmasq
  nftables
)

log() {
  echo "[bootstrap] $*"
}

get_interface_ip() {
  local iface="$1"
  ip -4 -o addr show dev "${iface}" 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n1
}

install_missing_packages() {
  local missing=()
  for pkg in "${PACKAGES[@]}"; do
    if ! dpkg -s "${pkg}" >/dev/null 2>&1; then
      missing+=("${pkg}")
    fi
  done

  if (( ${#missing[@]} > 0 )); then
    log "Installing missing packages: ${missing[*]}"
    apt-get update
    apt-get install -y "${missing[@]}"
  else
    log "All required packages are already installed."
  fi
}

clone_or_update_repo() {
  if [[ -d "${SRC_DIR}/.git" ]]; then
    log "Updating existing repository in ${SRC_DIR}"
    git -C "${SRC_DIR}" fetch --all --prune
    git -C "${SRC_DIR}" checkout "${REPO_BRANCH}"
    git -C "${SRC_DIR}" pull --ff-only origin "${REPO_BRANCH}"
  else
    log "Cloning ${REPO_URL} (branch ${REPO_BRANCH}) into ${SRC_DIR}"
    rm -rf "${SRC_DIR}"
    git clone --branch "${REPO_BRANCH}" --depth 1 "${REPO_URL}" "${SRC_DIR}"
  fi
}

main() {
  log "Starting full Raspberry Pi Trixie bootstrap"
  install_missing_packages

  log "Ensuring core services are enabled"
  systemctl enable --now chrony
  systemctl enable --now NetworkManager

  clone_or_update_repo

  if [[ ! -d "${SRC_DIR}/scripts" ]]; then
    echo "ERROR: scripts directory missing in ${SRC_DIR}"
    exit 1
  fi

  chmod +x "${SRC_DIR}/scripts/"*.sh

  log "Configuring service user and sudo rules"
  bash "${SRC_DIR}/scripts/setup_ntp_user.sh"

  log "Deploying app and systemd service to ${APP_DIR}"
  APP_DIR="${APP_DIR}" bash "${SRC_DIR}/scripts/setup_ntp_service.sh"

  log "Configuring reverse proxy + local DNS (${DOMAIN_NAME} -> ${LAN_IP})"
  LAN_INTERFACE="${LAN_INTERFACE}" LAN_IP="${LAN_IP}" DOMAIN_NAME="${DOMAIN_NAME}" \
    bash "${SRC_DIR}/scripts/setup_proxy_dns.sh"

  log "Applying inter-interface isolation firewall (NTP-only forwarding)"
  WAN_INTERFACE="${WAN_INTERFACE}" WIFI_INTERFACE="${WIFI_INTERFACE}" LAN_INTERFACE="${LAN_INTERFACE}" \
    bash "${SRC_DIR}/scripts/setup_firewall_isolation.sh"

  log "Bootstrap complete."

  local detected_lan_ip
  detected_lan_ip="$(get_interface_ip "${LAN_INTERFACE}")"
  if [[ -z "${detected_lan_ip}" ]]; then
    detected_lan_ip="not-set"
  fi

  echo
  echo "=== Install summary ==="
  echo "Repo source : ${SRC_DIR}"
  echo "App deploy  : ${APP_DIR}"
  echo "Domain      : ${DOMAIN_NAME}"
  echo "LAN IP (configured target) : ${LAN_IP}"
  echo "LAN IP (${LAN_INTERFACE}, detected) : ${detected_lan_ip}"
  echo "Interfaces  : WAN=${WAN_INTERFACE}, Wi-Fi=${WIFI_INTERFACE}, LAN=${LAN_INTERFACE}"
  echo "Service user: ${SERVICE_USER}"
  echo "Password    : no password"
  echo
  echo "Quick checks:"
  echo "  systemctl status ntp-webgui --no-pager"
  echo "  systemctl status chrony --no-pager"
  echo "  systemctl status nginx --no-pager"
  echo "  systemctl status dnsmasq --no-pager"
  echo "  systemctl status nftables --no-pager"
  echo "  nft list ruleset"
  echo
  echo "How to proceed:"
  echo "  1) On a client in the ${LAN_INTERFACE} network, set DNS server to ${LAN_IP}."
  echo "  2) Open: http://${DOMAIN_NAME}"
  echo "  3) If hostname does not resolve, test: nslookup ${DOMAIN_NAME} ${LAN_IP}"
  echo "  4) If needed, verify LAN IP on Pi: ip -4 addr show ${LAN_INTERFACE}"
}

main "$@"
