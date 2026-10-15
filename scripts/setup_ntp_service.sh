#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/setup_ntp_service.sh"
  exit 1
fi

USER_NAME="NTP"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="${APP_DIR:-/opt/ntp-server-webgui}"
WAN_INTERFACE="${WAN_INTERFACE:-}"
LAN_INTERFACE="${LAN_INTERFACE:-}"
WIFI_INTERFACE="${WIFI_INTERFACE:-wlan0}"
RUNTIME_ENV_FILE="${APP_DIR}/runtime.env"

if ! id "${USER_NAME}" >/dev/null 2>&1; then
  echo "User ${USER_NAME} does not exist. Run scripts/setup_ntp_user.sh first."
  exit 1
fi

mkdir -p "${APP_DIR}"
rsync -a --delete \
  --exclude ".git" \
  --exclude "__pycache__" \
  --exclude ".venv" \
  "${SRC_DIR}/" "${APP_DIR}/"

python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install --upgrade pip
"${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

install -m 644 "${APP_DIR}/deploy/ntp-webgui.service" /etc/systemd/system/ntp-webgui.service

cat > "${RUNTIME_ENV_FILE}" <<EOF
WAN_CONNECTION_NAME=${WAN_INTERFACE}
LAN_CONNECTION_NAME=${LAN_INTERFACE}
WIFI_CONNECTION_NAME=${WIFI_INTERFACE}
EOF

chown -R "${USER_NAME}:${USER_NAME}" "${APP_DIR}"

systemctl daemon-reload
systemctl enable --now ntp-webgui
systemctl status ntp-webgui --no-pager

echo "NTP web GUI service deployed and started."
