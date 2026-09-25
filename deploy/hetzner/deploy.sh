#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/mobiliti-worker/app}"
RELEASES_DIR="${RELEASES_DIR:-/opt/mobiliti-worker/releases}"
ENV_FILE="${ENV_FILE:-/etc/mobiliti-worker/worker.env}"
GIT_REF="${GIT_REF:-master}"
ACTIVE_CONTAINER="${ACTIVE_CONTAINER:-mobiliti-worker}"
GRAPH_HOST_DIR="/etc/mobiliti-worker/graph"
GRAPH_HOST_CERT="${GRAPH_HOST_DIR}/client-cert.pem"

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
TARGET_COMMIT="$(git -C "${APP_DIR}" rev-parse FETCH_HEAD)"
RELEASE_DIR="${RELEASES_DIR}/${TARGET_COMMIT}"
COMPOSE_FILE="${RELEASE_DIR}/deploy/hetzner/docker-compose.yml"
NETWORK_OVERRIDE_FILE="${RELEASE_DIR}/deploy/hetzner/docker-compose.existing-network.yml"

if [[ -L "${GRAPH_HOST_DIR}" ]] ||
  [[ -e "${GRAPH_HOST_DIR}" && ! -d "${GRAPH_HOST_DIR}" ]]; then
  echo "Invalid catalog credential directory at ${GRAPH_HOST_DIR}." >&2
  exit 1
fi
install -d -o root -g 10001 -m 0750 "${GRAPH_HOST_DIR}"

git -C "${APP_DIR}" show "${TARGET_COMMIT}:deploy/hetzner/preflight.py" |
  python3 - \
    --env-file "${ENV_FILE}" \
    --host-directory "${GRAPH_HOST_DIR}" \
    --certificate "${GRAPH_HOST_CERT}"

install -d -m 0755 "${RELEASES_DIR}"
if [[ ! -d "${RELEASE_DIR}/.git" && ! -f "${RELEASE_DIR}/.git" ]]; then
  git -C "${APP_DIR}" worktree add --detach "${RELEASE_DIR}" "${TARGET_COMMIT}"
fi

COMPOSE_ARGS=(-f "${COMPOSE_FILE}")
if docker container inspect "${ACTIVE_CONTAINER}" >/dev/null 2>&1; then
  ACTIVE_NETWORKS=()
  while IFS= read -r network_name; do
    if [[ -n "${network_name}" ]]; then
      ACTIVE_NETWORKS+=("${network_name}")
    fi
  done < <(
    docker inspect --format '{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}' \
      "${ACTIVE_CONTAINER}"
  )
  if [[ "${#ACTIVE_NETWORKS[@]}" -ne 1 ]] ||
    [[ ! "${ACTIVE_NETWORKS[0]}" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]]; then
    echo "Active worker must have exactly one valid Docker network." >&2
    exit 1
  fi
  if [[ ! -f "${NETWORK_OVERRIDE_FILE}" ]]; then
    echo "Missing existing-network Compose override at ${NETWORK_OVERRIDE_FILE}." >&2
    exit 1
  fi
  export WORKER_NETWORK_NAME="${ACTIVE_NETWORKS[0]}"
  COMPOSE_ARGS+=(-f "${NETWORK_OVERRIDE_FILE}")
fi

WORKER_IMAGE_TAG="${TARGET_COMMIT}" docker compose "${COMPOSE_ARGS[@]}" build
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_PREFIX:-mobiliti-worker}-${TARGET_COMMIT:0:12}"
TARGET_IMAGE="mobiliti-worker:${TARGET_COMMIT}"
BACKUP_CONTAINER=""

restore_previous_worker() {
  if docker container inspect "${ACTIVE_CONTAINER}" >/dev/null 2>&1; then
    failed_container="${ACTIVE_CONTAINER}-failed-${TARGET_COMMIT:0:12}-$(date -u +%Y%m%d%H%M%S)"
    docker stop "${ACTIVE_CONTAINER}" >/dev/null 2>&1 || true
    docker rename "${ACTIVE_CONTAINER}" "${failed_container}" >/dev/null 2>&1 || true
  fi

  if [[ -n "${BACKUP_CONTAINER}" ]] &&
    docker container inspect "${BACKUP_CONTAINER}" >/dev/null 2>&1; then
    docker rename "${BACKUP_CONTAINER}" "${ACTIVE_CONTAINER}"
    docker start "${ACTIVE_CONTAINER}"
  fi
}

if docker container inspect "${ACTIVE_CONTAINER}" >/dev/null 2>&1; then
  current_image="$(docker inspect --format '{{.Config.Image}}' "${ACTIVE_CONTAINER}")"
  if [[ "${current_image}" != "${TARGET_IMAGE}" ]]; then
    current_id="$(docker inspect --format '{{.Id}}' "${ACTIVE_CONTAINER}")"
    BACKUP_CONTAINER="${ACTIVE_CONTAINER}-backup-$(date -u +%Y%m%d%H%M%S)-${current_id:0:12}"
    docker rename "${ACTIVE_CONTAINER}" "${BACKUP_CONTAINER}"
    docker stop "${BACKUP_CONTAINER}"
  fi
fi

if ! WORKER_IMAGE_TAG="${TARGET_COMMIT}" docker compose \
  --project-name "${COMPOSE_PROJECT_NAME}" \
  "${COMPOSE_ARGS[@]}" up -d; then
  restore_previous_worker
  exit 1
fi

healthy=0
for _ in {1..12}; do
  if curl --fail --silent --show-error http://127.0.0.1:10000/health |
    python3 -c 'import json,sys; data=json.load(sys.stdin); raise SystemExit(0 if data.get("ok") and data.get("isolated_jobs") and data.get("catalog_asset_ready") else 1)'; then
    healthy=1
    break
  fi
  sleep 5
done

if [[ "${healthy}" -ne 1 ]]; then
  restore_previous_worker
  exit 1
fi

WORKER_IMAGE_TAG="${TARGET_COMMIT}" docker compose \
  --project-name "${COMPOSE_PROJECT_NAME}" \
  "${COMPOSE_ARGS[@]}" ps
curl --fail --silent --show-error http://127.0.0.1:10000/health
echo
printf '%s\n' "${TARGET_COMMIT}" >"${RELEASES_DIR}/CURRENT"
