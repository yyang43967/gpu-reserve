#!/usr/bin/env bash
# Emails an alert with the recent journal of a failed unit, through SES with the
# instance role. Started by systemd: OnFailure=gpu-reserve-alert@%n.service.
#
#   alert.sh <unit>
#
# The journal of the units that use it (backups) holds file sizes, object keys, and
# pg_dump errors; the scripts never log secrets.
set -euo pipefail
umask 077
RELEASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly RELEASE_DIR
# shellcheck source=lib.sh
source "$RELEASE_DIR/lib.sh"

unit="${1:-}"
[[ "$unit" =~ ^[A-Za-z0-9@._-]+$ ]] || die "usage: alert.sh <unit>"

to="$(env_value ALERT_EMAIL)"
from="$(env_value EMAIL_FROM)"
region="$(env_value AWS_REGION)"
[ -n "$to" ] && [ -n "$from" ] && [ -n "$region" ] \
  || die "ALERT_EMAIL, EMAIL_FROM, and AWS_REGION must be set; no alert sent for $unit"

host="$(hostname)"
journal="$(journalctl --unit "$unit" --since -2h --lines 40 --no-pager --output short-iso 2>&1 \
  || echo '(the journal could not be read)')"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
python3 - "$work/email.json" "$from" "$to" "$unit" "$host" "$journal" <<'PY'
import json, sys
path, sender, to, unit, host, journal = sys.argv[1:]
body = (
    f"{unit} failed on {host}.\n\n"
    f"Last journal lines:\n\n{journal}\n\n"
    f"On the instance: journalctl -u {unit} --since -1d\n"
)
json.dump({
    "FromEmailAddress": sender,
    "Destination": {"ToAddresses": [to]},
    "Content": {"Simple": {
        "Subject": {"Data": f"GPU Reserve: {unit} failed on {host}"},
        "Body": {"Text": {"Data": body}},
    }},
}, open(path, "w"))
PY
aws sesv2 send-email --region "$region" --cli-input-json "file://$work/email.json" >/dev/null
log "alert for $unit sent"
