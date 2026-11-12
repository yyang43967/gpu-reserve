#!/usr/bin/env bash
# Runs deploy.sh against stub `aws` and `docker` commands in a scratch directory and
# checks what it writes and in which order it runs the steps. Linux only (flock,
# GNU find); CI runs it, and locally: docker run --rm -v "$PWD:/src:ro" -w /src
# python:3.12-slim bash infra/deploy/tests/deploy-test.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAG=0123456789abcdef0123456789abcdef01234567
REPO=123456789012.dkr.ecr.us-east-2.amazonaws.com/gpu-reserve/api
# A literal $HOME: values must reach the env file without any expansion.
# shellcheck disable=SC2016
SECRET='p@ss/w0rd<&>$HOME'

fail() { echo "FAIL: $*" >&2; exit 1; }

setup() {
  WORK="$(mktemp -d)"
  mkdir -p "$WORK/app/releases/$TAG" "$WORK/bin" "$WORK/units"
  cp -R "$HERE/../deploy.sh" "$HERE/../backup.sh" "$HERE/../lib.sh" "$HERE/../compose.yml" \
    "$HERE/../systemd" "$WORK/app/releases/$TAG/"
  # aws: the parameters under the prefix, as get-parameters-by-path returns them;
  # other calls (the backup upload) are recorded.
  cat >"$WORK/bin/aws" <<STUB
#!/usr/bin/env bash
case "\$1" in
  ssm) cat "$WORK/parameters.json" ;;
  *) echo "aws \$*" >>"$WORK/calls.log" ;;
esac
STUB
  cat >"$WORK/bin/systemctl" <<STUB
#!/usr/bin/env bash
echo "systemctl \$*" >>"$WORK/calls.log"
STUB
  # docker: records each call; answers the two queries deploy.sh makes.
  cat >"$WORK/bin/docker" <<STUB
#!/usr/bin/env bash
echo "\$*" >>"$WORK/calls.log"
case "\$*" in
  *"ps --quiet api"*) echo api-container ;;
  *pg_dump*) printf 'PGDMP-test-dump' ;;
  inspect*) echo "$REPO:$TAG" ;;
esac
STUB
  chmod +x "$WORK/bin/aws" "$WORK/bin/docker" "$WORK/bin/systemctl"
  python3 - "$WORK/parameters.json" "$SECRET" <<'PY'
import json, sys
path, secret = sys.argv[1:]
params = {
    "/gpu-reserve/prod/JWT_SECRET": secret,
    "/gpu-reserve/prod/POSTGRES_PASSWORD": "pg-password",
    "/gpu-reserve/prod/EMAIL_FROM": "GPU Reserve <no-reply@gpu.example.test>",
    "/gpu-reserve/prod/BACKUPS_BUCKET": "backups-bucket",
}
json.dump({"Parameters": [{"Name": k, "Value": v} for k, v in params.items()]}, open(path, "w"))
PY
}

run_deploy() {
  PATH="$WORK/bin:$PATH" DEPLOY_APP_DIR="$WORK/app" SYSTEMD_UNIT_DIR="$WORK/units" \
    bash "$WORK/app/releases/$TAG/deploy.sh" "$@" >"$WORK/out.log" 2>&1
}

# A normal deploy.
setup
run_deploy "$TAG" "$REPO" || { cat "$WORK/out.log"; fail "deploy failed"; }
env_file="$WORK/app/.env"
[ "$(stat -c %a "$env_file")" = 600 ] || fail "env file mode is not 600"
grep -qxF "JWT_SECRET='$SECRET'" "$env_file" || fail "secret not written literally"
grep -qxF "EMAIL_FROM='GPU Reserve <no-reply@gpu.example.test>'" "$env_file" || fail "EMAIL_FROM"
grep -qxF "API_IMAGE='$REPO:$TAG'" "$env_file" || fail "API_IMAGE"
! grep -qF "$SECRET" "$WORK/out.log" || fail "a secret reached the output"
! grep -qF "pg-password" "$WORK/out.log" || fail "a secret reached the output"
order="$(grep -oE 'pull|up --detach --wait --wait-timeout 120 postgres redis|pg_dump|s3 cp|run --rm migrate|up --detach --wait --wait-timeout 240 --remove-orphans|systemctl enable' "$WORK/calls.log" | tr '\n' ',')"
expected='pull,up --detach --wait --wait-timeout 120 postgres redis,pg_dump,s3 cp,run --rm migrate,up --detach --wait --wait-timeout 240 --remove-orphans,systemctl enable,'
[ "$order" = "$expected" ] || fail "steps ran as: $order"
grep -qE "s3 cp .* s3://backups-bucket/postgres/[0-9TZ:-]+-pre-deploy-${TAG:0:12}\.dump" "$WORK/calls.log" \
  || fail "the pre-deploy backup was not uploaded under the expected key"
cmp -s "$HERE/../systemd/gpu-reserve-backup.timer" "$WORK/units/gpu-reserve-backup.timer" \
  || fail "the backup timer was not installed"
[ "$(readlink "$WORK/app/current")" = "$WORK/app/releases/$TAG" ] || fail "current link"
grep -q " $TAG$" "$WORK/app/deploy-history" || fail "history"
rm -rf "$WORK"

# Inputs that must be refused before anything runs.
setup
run_deploy latest "$REPO" && fail "accepted a tag that is not a SHA"
run_deploy "$TAG" "example.com/evil" && fail "accepted an unexpected repository"
[ ! -e "$WORK/calls.log" ] || fail "docker ran for refused input"
rm -rf "$WORK"

# A value Compose cannot hold safely: no env file is replaced, nothing is started.
setup
echo "API_IMAGE='previous'" >"$WORK/app/.env"
python3 - "$WORK/parameters.json" <<'PY'
import json, sys
path = sys.argv[1]
data = json.load(open(path))
data["Parameters"].append({"Name": "/gpu-reserve/prod/BAD", "Value": "it's"})
json.dump(data, open(path, "w"))
PY
run_deploy "$TAG" "$REPO" && fail "accepted a value with a quote"
grep -qxF "API_IMAGE='previous'" "$WORK/app/.env" || fail "the previous env file was replaced"
! grep -q 'up --detach' "$WORK/calls.log" 2>/dev/null || fail "containers were started"
rm -rf "$WORK"

echo "deploy.sh: all checks passed"
