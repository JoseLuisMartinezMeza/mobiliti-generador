#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/mobiliti-worker/app}"
ENV_DIR="${ENV_DIR:-/etc/mobiliti-worker}"
GIT_REPO="${GIT_REPO:-https://github.com/REMOVED_PASSWORD/mobiliti-generador.git}"
GIT_REF="${GIT_REF:-master}"
SWAP_SIZE="${SWAP_SIZE:-8G}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/hetzner/bootstrap.sh" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y \
  ca-certificates \
  curl \
  fail2ban \
  git \
  gnupg \
  htop \
  jq \
  ncdu \
  ufw \
  unattended-upgrades

if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  . /etc/os-release
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

systemctl enable --now docker
systemctl enable --now fail2ban
systemctl enable --now unattended-upgrades || true

if ! swapon --show --noheadings | grep -q .; then
  fallocate -l "${SWAP_SIZE}" /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo "/swapfile none swap sw 0 0" >> /etc/fstab
fi

ufw allow OpenSSH
ufw --force enable

install -d -m 0755 "$(dirname "${APP_DIR}")"
if [[ -d "${APP_DIR}/.git" ]]; then
  git -C "${APP_DIR}" fetch origin "${GIT_REF}"
  git -C "${APP_DIR}" reset --hard "origin/${GIT_REF}"
else
  rm -rf "${APP_DIR}"
  git clone --branch "${GIT_REF}" --single-branch "${GIT_REPO}" "${APP_DIR}"
fi

install -d -m 0700 "${ENV_DIR}"
if [[ ! -f "${ENV_DIR}/worker.env" ]]; then
  install -m 0600 "${APP_DIR}/deploy/hetzner/worker.env.example" "${ENV_DIR}/worker.env"
  echo "Created ${ENV_DIR}/worker.env. Fill SUPABASE_ANON_KEY and MOBILITI_REST_SECRET before deploy."
fi

chmod +x "${APP_DIR}/deploy/hetzner/deploy.sh"
ln -sf "${APP_DIR}/deploy/hetzner/deploy.sh" /usr/local/bin/mobiliti-worker-deploy

echo "Bootstrap complete."
echo "Next:"
echo "  1. Edit ${ENV_DIR}/worker.env"
echo "  2. Run: mobiliti-worker-deploy"
