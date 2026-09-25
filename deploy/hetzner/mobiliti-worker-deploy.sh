#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/mobiliti-worker/app}"
GIT_REF="${GIT_REF:-master}"

git -C "${APP_DIR}" fetch origin "${GIT_REF}"
git -C "${APP_DIR}" show FETCH_HEAD:deploy/hetzner/deploy.sh |
  APP_DIR="${APP_DIR}" GIT_REF="${GIT_REF}" bash -s -- "$@"
