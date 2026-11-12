#!/usr/bin/env bash
# Builds, pushes, and deploys the backend image. Runs on a developer machine with
# `aws login` credentials, and later in CI with the OIDC deploy role.
#
#   release.sh push           build HEAD for linux/arm64 and push it, tagged with its SHA
#   release.sh deploy <sha>   deploy that tag to the instance through SSM, then check it
#   release.sh tags           list pushed tags, newest first, and the deployed one
#   release.sh verify-backup  restore the newest backup into a throwaway database on
#                             the instance and check it (verify-backup.sh); monthly
#
# Rollback: `release.sh deploy <earlier-sha>`. Tags are immutable, so a SHA always
# names the image that was pushed for it; there is no `latest`.
set -euo pipefail

readonly PROJECT="${PROJECT:-gpu-reserve}"
readonly ENVIRONMENT="${ENVIRONMENT:-prod}"
readonly REPOSITORY_NAME="$PROJECT/api"
readonly SSM_PREFIX="/$PROJECT/$ENVIRONMENT"
readonly DEPLOY_TIMEOUT_SECONDS=1800
readonly VERIFY_TIMEOUT_SECONDS=900

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly ROOT

log() { printf '%s release: %s\n' "$(date -u +%H:%M:%S)" "$*" >&2; }
die() { log "error: $*"; exit 1; }

region() {
  local value="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
  [ -n "$value" ] || value="$(aws configure get region || true)"
  [ -n "$value" ] || die "set AWS_REGION"
  printf '%s' "$value"
}

repository_uri() {
  local account
  account="$(aws sts get-caller-identity --query Account --output text)"
  printf '%s.dkr.ecr.%s.amazonaws.com/%s' "$account" "$(region)" "$REPOSITORY_NAME"
}

ecr_login() {
  aws ecr get-login-password --region "$(region)" \
    | docker login --username AWS --password-stdin "${1%%/*}" >/dev/null
}

tag_exists() {
  aws ecr describe-images --region "$(region)" --repository-name "$REPOSITORY_NAME" \
    --image-ids imageTag="$1" >/dev/null 2>&1
}

check_tag() {
  [[ "$1" =~ ^[0-9a-f]{40}$ ]] || die "the tag must be a full git commit SHA (git rev-parse <ref>)"
}

cmd_push() {
  cd "$ROOT"
  [ -z "$(git status --porcelain)" ] || die "the working tree has changes; commit them first"
  local tag repo
  tag="$(git rev-parse HEAD)"
  repo="$(repository_uri)"
  if tag_exists "$tag"; then
    log "$tag is already in ECR"
  else
    ecr_login "$repo"
    log "building $tag for linux/arm64"
    docker build --platform linux/arm64 \
      --build-context kb=kb --build-context deploy=infra/deploy \
      --label "org.opencontainers.image.revision=$tag" \
      --tag "$repo:$tag" backend
    log "pushing"
    docker push --quiet "$repo:$tag"
  fi
  printf '%s\n' "$tag"
}

instance_id() {
  local id
  id="$(aws ec2 describe-instances --region "$(region)" \
    --filters "Name=tag:Name,Values=$PROJECT-$ENVIRONMENT" "Name=instance-state-name,Values=running" \
    --query 'Reservations[].Instances[].InstanceId' --output text)"
  [[ "$id" =~ ^i-[0-9a-f]+$ ]] || die "expected one running instance named $PROJECT-$ENVIRONMENT, found: ${id:-none}"
  printf '%s' "$id"
}

# Runs a bash script on the instance as root through SSM Run Command, waits for it,
# and prints its output to stderr. Returns non-zero unless the command succeeded.
# AWS-RunShellScript runs its commands with sh; the script is written to a file there
# and run with bash, so it gets the shell it was written and tested for.
# Callers use it as `run_on_instance ... || die`, where bash ignores `set -e` inside
# the function, so every step that can fail returns explicitly.
run_on_instance() {
  local instance="$1" comment="$2" timeout="$3" script="$4" region_name parameters
  region_name="$(region)" || return 1
  parameters="$(python3 - "$script" "$timeout" <<'PY'
import json, sys
script, timeout = sys.argv[1:]
marker = "GPU_RESERVE_SCRIPT"
if marker in script:
    sys.exit(f"the script must not contain {marker}")
commands = [
    "f=$(mktemp)",
    f"cat >\"$f\" <<'{marker}'",
    script.rstrip("\n"),
    marker,
    'bash "$f"; rc=$?; rm -f "$f"; exit $rc',
]
print(json.dumps({"commands": commands, "executionTimeout": [timeout]}))
PY
)" || return 1

  local command_id
  command_id="$(aws ssm send-command --region "$region_name" --instance-ids "$instance" \
    --document-name AWS-RunShellScript --comment "$comment" \
    --parameters "$parameters" --query Command.CommandId --output text)" || return 1
  [ -n "$command_id" ] || return 1

  local status="Pending" waited=0
  while [[ "$status" =~ ^(Pending|InProgress|Delayed)$ ]]; do
    sleep "${SSM_POLL_SECONDS:-5}"
    waited=$((waited + ${SSM_POLL_SECONDS:-5}))
    [ "$waited" -le "$timeout" ] || die "timed out waiting for command $command_id"
    status="$(aws ssm get-command-invocation --region "$region_name" --command-id "$command_id" \
      --instance-id "$instance" --query Status --output text 2>/dev/null || echo Pending)"
  done
  aws ssm get-command-invocation --region "$region_name" --command-id "$command_id" \
    --instance-id "$instance" --query '[StandardOutputContent, StandardErrorContent]' --output text >&2
  log "command $command_id finished: $status"
  [ "$status" = "Success" ]
}

cmd_deploy() {
  local tag="${1:-}"
  check_tag "$tag"
  tag_exists "$tag" || die "$tag is not in ECR; run 'release.sh push' on that commit first"
  local repo instance hostname region_name
  repo="$(repository_uri)"
  region_name="$(region)"
  instance="$(instance_id)"
  hostname="$(aws ssm get-parameter --region "$region_name" --name "$SSM_PREFIX/API_SITE_ADDRESS" \
    --query Parameter.Value --output text)"

  # On the instance: log in to ECR, pull the image, extract its deploy directory,
  # and run that directory's deploy.sh. Values are checked above, so they are safe to
  # place in the script.
  local script
  script="$(cat <<EOF
set -euo pipefail
repo='$repo'; tag='$tag'
aws ecr get-login-password --region '$region_name' | docker login --username AWS --password-stdin "\${repo%%/*}" >/dev/null 2>&1
docker pull --quiet "\$repo:\$tag" >/dev/null
dir="/opt/$PROJECT/releases/\$tag"
rm -rf "\$dir.tmp" && mkdir -p "\$dir.tmp"
cid="\$(docker create "\$repo:\$tag")"
docker cp "\$cid:/app/deploy/." "\$dir.tmp/"
docker rm "\$cid" >/dev/null
rm -rf "\$dir" && mv "\$dir.tmp" "\$dir"
SSM_PREFIX='$SSM_PREFIX' bash "\$dir/deploy.sh" "\$tag" "\$repo"
EOF
)"
  log "deploying $tag to $instance"
  run_on_instance "$instance" "deploy $tag" "$DEPLOY_TIMEOUT_SECONDS" "$script" \
    || die "deploy of $tag failed"

  log "checking https://$hostname/api/v1/ready"
  local attempt
  for attempt in $(seq 1 20); do
    if curl --fail --silent --show-error --max-time 5 "https://$hostname/api/v1/ready" >/dev/null; then
      log "deployed $tag; the API is ready"
      return 0
    fi
    log "not ready yet (attempt $attempt)"
    sleep 3
  done
  die "the API did not become ready"
}

cmd_verify_backup() {
  local instance
  instance="$(instance_id)"
  log "verifying the newest backup on $instance"
  run_on_instance "$instance" "verify backup" "$VERIFY_TIMEOUT_SECONDS" \
    "$(cat "$ROOT/infra/deploy/verify-backup.sh")" || die "backup verification failed"
  log "the newest backup restores and matches production"
}

cmd_tags() {
  aws ecr describe-images --region "$(region)" --repository-name "$REPOSITORY_NAME" \
    --query 'reverse(sort_by(imageDetails, &imagePushedAt))[].[imagePushedAt, imageTags[0]]' \
    --output text
  log "deploy history on the instance: aws ssm start-session, then tail /opt/$PROJECT/deploy-history"
}

case "${1:-}" in
  push) cmd_push ;;
  deploy) shift; cmd_deploy "$@" ;;
  tags) cmd_tags ;;
  verify-backup) cmd_verify_backup ;;
  *) die "usage: release.sh push | deploy <sha> | tags | verify-backup" ;;
esac
