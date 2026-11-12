# Shared by deploy.sh, backup.sh, and restore.sh, which source it after setting
# RELEASE_DIR to their own directory. Not run on its own.
# shellcheck shell=bash

# The directory and project name are overridable only so tests can use scratch ones.
readonly APP_DIR="${DEPLOY_APP_DIR:-/opt/gpu-reserve}"
readonly ENV_FILE="$APP_DIR/.env"
readonly PROJECT="${DEPLOY_PROJECT:-gpu-reserve}"
SCRIPT_NAME="$(basename "$0" .sh)"
readonly SCRIPT_NAME

log() { printf '%s %s: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$SCRIPT_NAME" "$*"; }
die() { log "error: $*"; exit 1; }

compose() {
  docker compose --project-name "$PROJECT" --project-directory "$RELEASE_DIR" \
    --file "$RELEASE_DIR/compose.yml" --env-file "$ENV_FILE" "$@"
}

# Deploys, backups, and restores never overlap. A script that already holds the lock
# runs the others with GPU_RESERVE_LOCK_HELD=1 (a deploy takes its own backup).
take_lock() {
  local wait_seconds="$1"
  [ "${GPU_RESERVE_LOCK_HELD:-}" = 1 ] && return 0
  exec 9>"$APP_DIR/operations.lock"
  flock --wait "$wait_seconds" 9 || die "another deploy, backup, or restore is running"
  export GPU_RESERVE_LOCK_HELD=1
}

# One value from the env file (written by deploy.sh as KEY='value').
env_value() {
  sed -n "s/^$1='\(.*\)'\$/\1/p" "$ENV_FILE"
}
