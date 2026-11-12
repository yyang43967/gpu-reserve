#!/usr/bin/env bash
# From a laptop: runs a script from this directory on the load-test twin or driver as
# root, through SSM Run Command, and prints its output. Arguments are passed through.
#
#   AWS_PROFILE=gpu-reserve AWS_REGION=us-east-2 \
#     loadtest/instance/remote.sh driver|twin <script> [args...]
#
# The instances are found by their Name tag (infra/terraform/loadtest.tf).
set -euo pipefail

role="${1:-}" script="${2:-}"
case "$role" in twin | driver) ;; *) echo "usage: remote.sh driver|twin <script> [args...]" >&2; exit 2 ;; esac
shift 2
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$here/$script" ] || { echo "no such script: $here/$script" >&2; exit 2; }
region="${AWS_REGION:?set AWS_REGION}"
timeout="${REMOTE_TIMEOUT_SECONDS:-1800}"

id="$(aws ec2 describe-instances --region "$region" \
  --filters "Name=tag:Name,Values=gpu-reserve-prod-loadtest-$role" "Name=instance-state-name,Values=running" \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
[[ "$id" =~ ^i-[0-9a-f]+$ ]] || { echo "expected one running $role instance, found: ${id:-none}" >&2; exit 1; }

# The script is written to a file there and run with bash (AWS-RunShellScript uses sh);
# its arguments are quoted here, so they arrive as they were given.
quoted="$(printf ' %q' "$@")"
parameters="$(python3 - "$here/$script" "$quoted" "$timeout" <<'PY'
import json, sys
path, quoted, timeout = sys.argv[1:]
body = open(path).read().rstrip("\n")
marker = "GPU_RESERVE_SCRIPT"
if marker in body:
    sys.exit(f"the script must not contain {marker}")
commands = [
    "f=$(mktemp)",
    f"cat >\"$f\" <<'{marker}'",
    body,
    marker,
    f'bash "$f"{quoted}; rc=$?; rm -f "$f"; exit $rc',
]
print(json.dumps({"commands": commands, "executionTimeout": [timeout]}))
PY
)"

command_id="$(aws ssm send-command --region "$region" --instance-ids "$id" \
  --document-name AWS-RunShellScript --comment "load test: $script on $role" \
  --parameters "$parameters" --query Command.CommandId --output text)"
status=Pending waited=0
while [[ "$status" =~ ^(Pending|InProgress|Delayed)$ ]]; do
  sleep 5
  waited=$((waited + 5))
  [ "$waited" -le "$timeout" ] || { echo "timed out waiting for $command_id" >&2; exit 1; }
  status="$(aws ssm get-command-invocation --region "$region" --command-id "$command_id" \
    --instance-id "$id" --query Status --output text 2>/dev/null || echo Pending)"
done
aws ssm get-command-invocation --region "$region" --command-id "$command_id" --instance-id "$id" \
  --query '[StandardOutputContent, StandardErrorContent]' --output text
echo "$role: $script finished: $status" >&2
[ "$status" = Success ]
