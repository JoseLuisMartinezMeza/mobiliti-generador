#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/mobiliti-worker/app}"
ENV_FILE="${ENV_FILE:-/etc/mobiliti-worker/worker.env}"
GIT_REF="${GIT_REF:-master}"
COMPOSE_FILE="${APP_DIR}/deploy/hetzner/docker-compose.yml"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo mobiliti-worker-deploy" >&2
  exit 1
fi

if [[ ! -d "${APP_DIR}/.git" ]]; then
  echo "Missing git checkout at ${APP_DIR}. Run bootstrap.sh first." >&2
  exit 1
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Missing ${ENV_FILE}. Copy worker.env.example and fill secrets." >&2
  exit 1
fi

missing=()
for key in SUPABASE_URL SUPABASE_ANON_KEY MOBILITI_REST_SECRET QUOTE_STORAGE_BUCKET; do
  if ! grep -Eq "^${key}=.+" "${ENV_FILE}"; then
    missing+=("${key}")
  fi
done
if [[ "${#missing[@]}" -gt 0 ]]; then
  echo "Missing required env values in ${ENV_FILE}: ${missing[*]}" >&2
  exit 1
fi

git -C "${APP_DIR}" fetch origin "${GIT_REF}"
git -C "${APP_DIR}" reset --hard FETCH_HEAD

docker compose -f "${COMPOSE_FILE}" build
docker compose -f "${COMPOSE_FILE}" up -d

sleep 5
docker compose -f "${COMPOSE_FILE}" ps
curl --fail --silent --show-error http://127.0.0.1:10000/health
echo
