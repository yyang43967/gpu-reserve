#!/usr/bin/env bash
# The body of one phase's systemd unit on the driver (started by phase.sh): runs the
# runner against the twin, then copies the results and the log to the bucket, whether
# or not the run succeeded. Runs from the source directory.
#
#   run-phase.sh <label> <bucket> <run.py arguments...>
set -uo pipefail
readonly DIR=/opt/gpu-reserve-loadtest
label="$1" bucket="$2"
shift 2

python3.11 loadtest/run.py --no-build --label "$label" \
  --compose-file infra/deploy/compose.yml --compose-file loadtest/compose.instance.yml \
  --project gpu-reserve-loadtest --env-file "$DIR/.env" "$@"
rc=$?
echo "run.py exited with $rc"
aws s3 sync --only-show-errors loadtest/out "s3://$bucket/out/"
aws s3 cp --only-show-errors "$DIR/logs/$label.log" "s3://$bucket/logs/"
exit "$rc"
