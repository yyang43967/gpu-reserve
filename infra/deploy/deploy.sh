#!/usr/bin/env bash
# Deploys one image tag on the instance. Runs as root through SSM Run Command, from
# the release directory that release.sh extracted out of that same image, so the
# Compose file and Caddyfile always match the code they run.
#
#   deploy.sh <git-sha> <ecr-repository-uri>
#
# Rolling back is deploying an earlier tag: the same steps, with that tag's own
# configuration. Migrations are not reversed, so a rollback across a migration needs
# the older code to work with the newer schema.
#
# Never prints parameter values: the env file is written by a filter that reads them
# from the AWS CLI's output and writes them straight to a mode 0600 file.
set -euo pipefail
umask 077

# Overridable only so tests can run the script against a scratch directory.
readonly APP_DIR="${DEPLOY_APP_DIR:-/opt/gpu-reserve}"
readonly ENV_FILE="$APP_DIR/.env"
readonly PROJECT=gpu-reserve
readonly SSM_PREFIX="${SSM_PREFIX:-/gpu-reserve/prod}"
readonly RELEASES_KEPT=5

log() { printf '%s deploy: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }
die() { log "error: $*"; exit 1; }

[ "$#" -eq 2 ] || die "usage: deploy.sh <git-sha> <ecr-repository-uri>"
readonly TAG="$1" REPOSITORY="$2"
[[ "$TAG" =~ ^[0-9a-f]{40}$ ]] || die "the tag must be a full git commit SHA"
[[ "$REPOSITORY" =~ ^[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[a-z0-9/_-]+$ ]] \
  || die "unexpected repository URI"
RELEASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly RELEASE_DIR

compose() {
  docker compose --project-name "$PROJECT" --project-directory "$RELEASE_DIR" \
    --file "$RELEASE_DIR/compose.yml" --env-file "$ENV_FILE" "$@"
}

on_error() {
  log "failed; containers now:"
  compose ps --all || true
  # Application logs are JSON lines that never contain secrets or request bodies.
  compose logs --no-color --tail 40 api worker migrate 2>/dev/null || true
}

# One deploy at a time.
exec 9>"$APP_DIR/deploy.lock"
flock --nonblock 9 || die "another deploy is running"
trap on_error ERR

write_env_file() {
  # Every parameter under the prefix becomes KEY=value. Values are single-quoted for
  # Compose, so none may contain a single quote or a newline.
  aws ssm get-parameters-by-path --path "$SSM_PREFIX" --recursive --with-decryption \
    --output json \
    | python3 -c '
import json, re, sys
params = json.load(sys.stdin)["Parameters"]
out = []
for p in sorted(params, key=lambda p: p["Name"]):
    key = p["Name"].rsplit("/", 1)[-1]
    value = p["Value"]
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
        sys.exit(f"parameter name {key!r} is not an environment variable name")
    if "\x27" in value or "\n" in value:
        sys.exit(f"parameter {key} contains a quote or a newline")
    out.append(f"{key}=\x27{value}\x27")
print("\n".join(out))
' >"$ENV_FILE.new"
  printf "API_IMAGE='%s:%s'\n" "$REPOSITORY" "$TAG" >>"$ENV_FILE.new"
  chmod 600 "$ENV_FILE.new"
  mv "$ENV_FILE.new" "$ENV_FILE"
  log "env file written ($(wc -l <"$ENV_FILE") entries)"
}

log "deploying $TAG"
write_env_file

log "pulling images"
compose --profile migrate pull --quiet

log "starting postgres and redis"
compose up --detach --wait --wait-timeout 120 postgres redis

# Before the new api and worker start: a failed migration leaves the old ones running.
log "running migrations"
compose --profile migrate run --rm migrate

log "starting the stack"
compose up --detach --wait --wait-timeout 240 --remove-orphans

running="$(docker inspect --format '{{.Config.Image}}' "$(compose ps --quiet api)")"
[ "$running" = "$REPOSITORY:$TAG" ] || die "api runs $running, expected $REPOSITORY:$TAG"

ln -sfn "$RELEASE_DIR" "$APP_DIR/current"
printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$TAG" >>"$APP_DIR/deploy-history"
log "deployed $TAG"

# Housekeeping. Older release directories go (ECR still has the images, and a
# rollback extracts its directory again); so do images unused for a week.
find "$APP_DIR/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
  | sort -rn | tail -n +$((RELEASES_KEPT + 1)) | cut -d' ' -f2- \
  | while read -r old; do
      [ "$old" = "$RELEASE_DIR" ] || rm -rf -- "$old"
    done
docker image prune --all --force --filter "until=168h" >/dev/null
