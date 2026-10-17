#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec bash "$0" "$@"
fi

set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/setup_ntp_user.sh"
  exit 1
fi

SERVICE_USER="ntp"
ADMIN_USER="ntp-admin"
APP_DIR="${APP_DIR:-/opt/ntp-server-webgui}"
SUDOERS_FILE="/etc/sudoers.d/ntp-webgui"

# Create service user with nologin shell
if id "${SERVICE_USER}" >/dev/null 2>&1; then
  echo "User ${SERVICE_USER} already exists."
else
  useradd --badname --create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
  echo "Created service user ${SERVICE_USER}."
fi

# Create admin user with bash shell and password
if id "${ADMIN_USER}" >/dev/null 2>&1; then
  echo "User ${ADMIN_USER} already exists."
else
  useradd --badname --create-home --shell /bin/bash "${ADMIN_USER}"
  echo "Created admin user ${ADMIN_USER}."
fi

# No local password for service user.
passwd -d "${SERVICE_USER}" >/dev/null 2>&1 || true
passwd -l "${SERVICE_USER}" >/dev/null 2>&1 || true

# Admin user has a password and can login
passwd -u "${ADMIN_USER}" >/dev/null 2>&1 || true

cat > "${SUDOERS_FILE}" <<'EOF'
ntp ALL=(root) NOPASSWD: /usr/bin/nmcli
ntp-admin ALL=(root) NOPASSWD: /usr/bin/systemctl restart chrony
ntp-admin ALL=(root) NOPASSWD: /usr/bin/systemctl reload chrony
ntp-admin ALL=(root) NOPASSWD: /usr/sbin/reboot
ntp-admin ALL=(root) NOPASSWD: /usr/bin/reboot
EOF

chmod 440 "${SUDOERS_FILE}"
visudo -cf "${SUDOERS_FILE}"

if [[ -d "${APP_DIR}" ]]; then
  chown -R "${SERVICE_USER}:${SERVICE_USER}" "${APP_DIR}"
fi

echo "NTP users and sudo rights configured."
