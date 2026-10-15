#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/setup_ntp_user.sh"
  exit 1
fi

USER_NAME="ntp-admin"
APP_DIR="${APP_DIR:-/opt/ntp-server-webgui}"
SUDOERS_FILE="/etc/sudoers.d/ntp-webgui"

if id "${USER_NAME}" >/dev/null 2>&1; then
  echo "User ${USER_NAME} already exists."
else
  useradd --badname --create-home --shell /usr/sbin/nologin "${USER_NAME}"
  echo "Created user ${USER_NAME}."
fi

# No local password for this service user.
passwd -d "${USER_NAME}" >/dev/null 2>&1 || true
passwd -l "${USER_NAME}" >/dev/null 2>&1 || true

cat > "${SUDOERS_FILE}" <<'EOF'
ntp-admin ALL=(root) NOPASSWD: /usr/bin/nmcli
ntp-admin ALL=(root) NOPASSWD: /usr/bin/systemctl restart chrony
ntp-admin ALL=(root) NOPASSWD: /usr/bin/systemctl reload chrony
ntp-admin ALL=(root) NOPASSWD: /usr/sbin/reboot
ntp-admin ALL=(root) NOPASSWD: /usr/bin/reboot
EOF

chmod 440 "${SUDOERS_FILE}"
visudo -cf "${SUDOERS_FILE}"

if [[ -d "${APP_DIR}" ]]; then
  chown -R "${USER_NAME}:${USER_NAME}" "${APP_DIR}"
fi

echo "NTP user and sudo rights configured."
