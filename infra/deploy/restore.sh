#!/usr/bin/env bash
# Replaces the database with a backup from the backups bucket.
#
#   restore.sh latest --confirm
#   restore.sh postgres/<timestamp>-<reason>.dump --confirm
#   restore.sh ... --confirm --no-safety-backup    when the current database cannot be dumped
#
# Steps: download and check the dump, back up the current database (pre-restore),
# stop the API and worker, recreate the database, restore, empty Redis (its cache,
# queue, and counters describe the old data), and start the API and worker again.
set -euo pipefail
umask 077
RELEASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly RELEASE_DIR
# shellcheck source=lib.sh
source "$RELEASE_DIR/lib.sh"

key="${1:-}"
confirmed=0 safety_backup=1
shift || true
for arg in "$@"; do
  case "$arg" in
    --confirm) confirmed=1 ;;
    --no-safety-backup) safety_backup=0 ;;
    *) die "unknown option $arg" ;;
  esac
done
[ -n "$key" ] || die "usage: restore.sh latest|<key> --confirm [--no-safety-backup]"
[ "$confirmed" = 1 ] || die "this replaces the database; add --confirm"

take_lock 0

bucket="$(env_value BACKUPS_BUCKET)"
[ -n "$bucket" ] || die "BACKUPS_BUCKET is not set"
if [ "$key" = latest ]; then
  key="$(aws s3api list-objects-v2 --bucket "$bucket" --prefix postgres/ \
    --query 'sort_by(Contents, &LastModified)[-1].Key' --output text)"
fi
[[ "$key" =~ ^postgres/[A-Za-z0-9._-]+\.dump$ ]] || die "no backup found (got '$key')"

work="$(mktemp -d "$APP_DIR/restore.XXXXXX")"
trap 'rm -rf "$work"' EXIT
file="$work/gpureserve.dump"
aws s3 cp --only-show-errors "s3://$bucket/$key" "$file"
compose exec -T postgres pg_restore --list <"$file" >/dev/null
log "restoring $key ($(du -h "$file" | cut -f1))"

if [ "$safety_backup" = 1 ]; then
  "$RELEASE_DIR/backup.sh" pre-restore
fi

compose stop api worker
compose exec -T postgres psql --username=gpureserve --dbname=postgres -v ON_ERROR_STOP=1 \
  -c 'DROP DATABASE IF EXISTS gpureserve WITH (FORCE)' \
  -c 'CREATE DATABASE gpureserve OWNER gpureserve'
compose exec -T postgres pg_restore --username=gpureserve --dbname=gpureserve \
  --no-owner --exit-on-error <"$file"
compose exec -T redis redis-cli FLUSHALL >/dev/null
compose up --detach --wait --wait-timeout 240 api worker
log "restored $key"
