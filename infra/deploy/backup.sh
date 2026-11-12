#!/usr/bin/env bash
# Dumps the database, checks that the dump reads back, and uploads it to the backups
# bucket (kept 14 days by its lifecycle rule). Runs daily from a systemd timer and
# before every deploy's migrations.
#
#   backup.sh [reason]    daily (default), pre-deploy-<sha>, pre-restore
set -euo pipefail
umask 077
RELEASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly RELEASE_DIR
# shellcheck source=lib.sh
source "$RELEASE_DIR/lib.sh"

reason="${1:-daily}"
[[ "$reason" =~ ^[a-z0-9-]{1,64}$ ]] || die "the reason must be lowercase letters, digits, and hyphens"

# A daily run waits for a deploy in progress rather than skipping the day.
take_lock 900

bucket="$(env_value BACKUPS_BUCKET)"
[ -n "$bucket" ] || die "BACKUPS_BUCKET is not set; deploy first"

work="$(mktemp -d "$APP_DIR/backup.XXXXXX")"
trap 'rm -rf "$work"' EXIT
file="$work/gpureserve.dump"

compose exec -T postgres pg_dump --username=gpureserve --dbname=gpureserve \
  --format=custom --compress=6 >"$file"
# A dump is only a backup if it can be read back.
compose exec -T postgres pg_restore --list <"$file" >/dev/null

key="postgres/$(date -u +%Y-%m-%dT%H%M%SZ)-$reason.dump"
aws s3 cp --only-show-errors --checksum-algorithm SHA256 "$file" "s3://$bucket/$key"
log "backed up $(du -h "$file" | cut -f1) to s3://$bucket/$key"
