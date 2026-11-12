#!/usr/bin/env bash
# Forces the backup service to fail under real systemd and checks that OnFailure
# starts the alert unit, which calls SES with the failed unit's journal. `aws` is a
# stand-in that records the request. Needs root and a running systemd: CI runs it on
# the runner with sudo; locally, in a systemd container (see the deploy README).
#
# It uses the paths the units use (/opt/gpu-reserve, /etc/systemd/system), so run it
# only on a throwaway machine.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY="$(cd "$HERE/.." && pwd)"
# Not under /run or /tmp, which are often mounted noexec.
STUBS=/usr/local/lib/gpu-reserve-alert-test
UNITS=/etc/systemd/system

fail() { echo "FAIL: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || fail "run as root"
[ -d /run/systemd/system ] || fail "needs a running systemd"

cleanup() {
  systemctl reset-failed 'gpu-reserve-*' >/dev/null 2>&1 || true
  rm -rf "$UNITS/gpu-reserve-backup.service" "$UNITS/gpu-reserve-alert@.service" \
    "$UNITS/gpu-reserve-alert@.service.d" "$STUBS"
  [ "${made_docker_unit:-0}" = 1 ] && rm -f "$UNITS/docker.service"
  rm -rf /opt/gpu-reserve
  systemctl daemon-reload
}
trap cleanup EXIT

# The release as a deploy leaves it, with an env file that has no BACKUPS_BUCKET:
# backup.sh stops on that before touching Docker, which is the failure under test.
mkdir -p /opt/gpu-reserve "$STUBS"
ln -sfn "$DEPLOY" /opt/gpu-reserve/current
cat >/opt/gpu-reserve/.env <<'ENV'
AWS_REGION='us-east-2'
EMAIL_FROM='GPU Reserve <no-reply@gpu.example.test>'
ALERT_EMAIL='ops@example.test'
ENV
chmod 600 /opt/gpu-reserve/.env

# aws: copies the --cli-input-json file it is given.
cat >"$STUBS/aws" <<STUB
#!/usr/bin/env bash
for arg in "\$@"; do
  case "\$arg" in file://*) cp "\${arg#file://}" "$STUBS/request.json" ;; esac
done
echo "aws \$*" >>"$STUBS/calls.log"
STUB
chmod +x "$STUBS/aws"

install -m 0644 "$DEPLOY/systemd/gpu-reserve-backup.service" "$DEPLOY/systemd/gpu-reserve-alert@.service" "$UNITS/"
# The alert unit finds the stand-in before the real CLI.
mkdir -p "$UNITS/gpu-reserve-alert@.service.d"
printf '[Service]\nEnvironment=PATH=%s:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin\n' "$STUBS" \
  >"$UNITS/gpu-reserve-alert@.service.d/test.conf"
# The backup service requires docker.service; a container may not have one.
made_docker_unit=0
if ! systemctl cat docker.service >/dev/null 2>&1; then
  printf '[Service]\nType=oneshot\nRemainAfterExit=yes\nExecStart=/bin/true\n' >"$UNITS/docker.service"
  made_docker_unit=1
fi
systemctl daemon-reload

systemctl start gpu-reserve-backup.service && fail "the backup was expected to fail"

# OnFailure starts the alert unit asynchronously.
for _ in $(seq 1 30); do
  [ -f "$STUBS/request.json" ] && break
  sleep 1
done
[ -f "$STUBS/request.json" ] || {
  journalctl -u 'gpu-reserve-alert@*' --no-pager | tail -20
  fail "no alert was sent"
}

python3 - "$STUBS/request.json" <<'PY'
import json, sys
request = json.load(open(sys.argv[1]))
assert request["Destination"]["ToAddresses"] == ["ops@example.test"], request["Destination"]
assert request["FromEmailAddress"] == "GPU Reserve <no-reply@gpu.example.test>"
subject = request["Content"]["Simple"]["Subject"]["Data"]
assert subject.startswith("GPU Reserve: gpu-reserve-backup.service failed on "), subject
body = request["Content"]["Simple"]["Body"]["Text"]["Data"]
# The backup's own error line, taken from its journal.
assert "BACKUPS_BUCKET is not set" in body, body
PY
grep -q 'sesv2 send-email --region us-east-2' "$STUBS/calls.log" || fail "unexpected aws call"

echo "alert: all checks passed"
