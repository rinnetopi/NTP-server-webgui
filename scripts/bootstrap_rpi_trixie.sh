#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/bootstrap_rpi_trixie.sh"
  exit 1
fi

REPO_URL="${REPO_URL:-https://github.com/rinnetopi/NTP-server-webgui.git}"
REPO_BRANCH="${REPO_BRANCH:-main}"
SRC_DIR="${SRC_DIR:-/opt/ntp-server-webgui-src}"
APP_DIR="${APP_DIR:-/opt/ntp-server-webgui}"

WAN_INTERFACE_SET="${WAN_INTERFACE:-}"
LAN_INTERFACE_SET="${LAN_INTERFACE:-}"
WAN_INTERFACE="${WAN_INTERFACE:-eth0}"
WIFI_INTERFACE="${WIFI_INTERFACE:-wlan0}"
LAN_INTERFACE="${LAN_INTERFACE:-eth1}"
LAN_IP="${LAN_IP:-10.12.0.1}"
DOMAIN_NAME="${DOMAIN_NAME:-ct.ntpserver.org}"
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

validate_wan_lan_mapping() {
  if [[ "${WAN_INTERFACE}" == "${LAN_INTERFACE}" ]]; then
    echo "ERROR: WAN_INTERFACE and LAN_INTERFACE cannot be the same."
    exit 1
  fi

  if [[ "${WAN_INTERFACE}" != "eth0" && "${WAN_INTERFACE}" != "eth1" ]]; then
    echo "ERROR: WAN_INTERFACE must be eth0 or eth1."
    exit 1
  fi

  if [[ "${LAN_INTERFACE}" != "eth0" && "${LAN_INTERFACE}" != "eth1" ]]; then
    echo "ERROR: LAN_INTERFACE must be eth0 or eth1."
    exit 1
  fi
}

prompt_wan_lan_mapping() {
  if [[ -n "${WAN_INTERFACE_SET:-}" || -n "${LAN_INTERFACE_SET:-}" ]]; then
    return
  fi

  if [[ ! -t 0 ]]; then
    return
  fi

  # Detect available Ethernet interfaces
  local eth_interfaces=()
  while IFS= read -r line; do
    if [[ "$line" =~ ^[0-9]+:[[:space:]]*([a-zA-Z0-9]+): ]]; then
      iface="${BASH_REMATCH[1]}"
      if [[ "$iface" == eth* ]]; then
        eth_interfaces+=("$iface")
      fi
    fi
  done < <(ip -o link show)

  # Sort interfaces to ensure consistent ordering
  IFS=$'\n' eth_interfaces=($(sort <<<"${eth_interfaces[*]}"))
  unset IFS

  if [[ ${#eth_interfaces[@]} -eq 0 ]]; then
    echo "ERROR: No Ethernet interfaces found on this system."
    exit 1
  fi

  if [[ ${#eth_interfaces[@]} -eq 1 ]]; then
    echo
    echo "Detected single Ethernet interface: ${eth_interfaces[0]}"
    echo "Setting as both WAN and LAN (default configuration)."
    WAN_INTERFACE="${eth_interfaces[0]}"
    LAN_INTERFACE="${eth_interfaces[0]}"
  elif [[ ${#eth_interfaces[@]} -ge 2 ]]; then
    # Use the first two interfaces for WAN/LAN selection
    local eth0="${eth_interfaces[0]}"
    local eth1="${eth_interfaces[1]}"
    
    echo
    echo "Detected Ethernet interfaces:"
    echo "  1) ${eth0} (WAN)"
    echo "  2) ${eth1} (LAN)"
    read -r -p "Choose WAN interface [1/2] (default 1): " wan_choice
    wan_choice="${wan_choice:-1}"
    
    case "${wan_choice}" in
      1)
        WAN_INTERFACE="${eth0}"
        LAN_INTERFACE="${eth1}"
        ;;
      2)
        WAN_INTERFACE="${eth1}"
        LAN_INTERFACE="${eth0}"
        ;;
      *)
        echo "ERROR: Invalid choice '${wan_choice}'. Expected 1 or 2."
        exit 1
        ;;
    esac
    
    echo "Selected interfaces:"
    echo "  WAN: ${WAN_INTERFACE}"
    echo "  LAN: ${LAN_INTERFACE}"
  fi
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
    # Safely update repo: reset to origin state to handle local changes gracefully
    if ! git -C "${SRC_DIR}" pull --ff-only origin "${REPO_BRANCH}"; then
      log "Warning: Failed to fast-forward pull. Resetting to origin/${REPO_BRANCH}..."
      git -C "${SRC_DIR}" reset --hard "origin/${REPO_BRANCH}"
    fi
    # Clean untracked files and directories (safe since this is a cacheable mirror)
    git -C "${SRC_DIR}" clean -fdx
  else
    log "Cloning ${REPO_URL} (branch ${REPO_BRANCH}) into ${SRC_DIR}"
    rm -rf "${SRC_DIR}"
    git clone --branch "${REPO_BRANCH}" --depth 1 "${REPO_URL}" "${SRC_DIR}"
  fi
}

main() {
  log "Starting full Raspberry Pi Trixie bootstrap"
  prompt_wan_lan_mapping
  validate_wan_lan_mapping
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
  WAN_INTERFACE="${WAN_INTERFACE}" LAN_INTERFACE="${LAN_INTERFACE}" WIFI_INTERFACE="${WIFI_INTERFACE}" APP_DIR="${APP_DIR}" \
    bash "${SRC_DIR}/scripts/setup_ntp_service.sh"

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
