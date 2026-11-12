#!/usr/bin/env bash
# Checks that the newest backup restores, without touching the production database.
# Runs the daily backup unit, restores the newest dump into a throwaway Postgres
# container (no network, its own anonymous volume, the production image), compares it
# with production, and checks that the exclusion constraint still rejects an overlap.
# Production is only read: pg_dump, and SELECTs for counts and catalog rows.
#
#   release.sh verify-backup                          from a laptop, through SSM
#   sudo /opt/gpu-reserve/current/verify-backup.sh    on the instance
#
# Self-contained (no lib.sh): release.sh sends this file to the instance, so it also
# works when the deployed release predates it. Run it once a month.
set -euo pipefail
umask 077
cd /opt/gpu-reserve/current
compose() { docker compose -p gpu-reserve --project-directory /opt/gpu-reserve/current \
  -f compose.yml --env-file /opt/gpu-reserve/.env "$@"; }

echo "== 1. backup through the systemd unit"
systemctl start gpu-reserve-backup.service
echo "unit result: $(systemctl show -p Result --value gpu-reserve-backup.service)"
journalctl -u gpu-reserve-backup.service --since -10min --no-pager -o cat | tail -3
systemctl list-timers gpu-reserve-backup.timer --no-pager

bucket="$(sed -n "s/^BACKUPS_BUCKET='\(.*\)'\$/\1/p" /opt/gpu-reserve/.env)"
key="$(aws s3api list-objects-v2 --bucket "$bucket" --prefix postgres/ \
  --query 'sort_by(Contents, &LastModified)[-1].Key' --output text)"
echo "newest dump: $key"

echo "== 2. restore into a throwaway container"
name=gpu-reserve-restore-check
# Leftovers from a run that was killed outright (SIGKILL cannot be trapped).
echo "leftovers: container [$(docker ps -aq --filter "name=^${name}\$")]" \
  "dirs [$(find /opt/gpu-reserve -maxdepth 1 -name 'restore-check.*' | tr '\n' ' ')]"
docker rm -f -v "$name" >/dev/null 2>&1 || true
rm -rf /opt/gpu-reserve/restore-check.*
work="$(mktemp -d /opt/gpu-reserve/restore-check.XXXXXX)"
# Removes the container with its anonymous volume and the downloaded dump on any exit.
cleanup() { docker rm -f -v "$name" >/dev/null 2>&1 || true; rm -rf "$work"; }
trap cleanup EXIT
trap 'exit 143' INT TERM HUP
aws s3 cp --only-show-errors "s3://$bucket/$key" "$work/db.dump"
image="$(docker inspect --format '{{.Config.Image}}' "$(compose ps -q postgres)")"
echo "image: $image, dump: $(du -h "$work/db.dump" | cut -f1)"
docker run -d --name "$name" --network none --memory 256m --memory-swap 256m \
  -e POSTGRES_USER=gpureserve -e POSTGRES_DB=gpureserve -e POSTGRES_HOST_AUTH_METHOD=trust \
  "$image" >/dev/null
# Init runs a temporary server on the socket only; TCP answers once the real one is up.
for _ in $(seq 60); do
  docker exec "$name" pg_isready -q -h 127.0.0.1 -U gpureserve -d gpureserve && break
  sleep 1
done
docker exec "$name" pg_isready -h 127.0.0.1 -U gpureserve -d gpureserve
# The same pg_restore flags as restore.sh.
docker exec -i "$name" pg_restore -h 127.0.0.1 --username=gpureserve --dbname=gpureserve \
  --no-owner --exit-on-error <"$work/db.dump"
echo "restored"

checks="
SELECT 'alembic ' || version_num FROM alembic_version;
SELECT 'extension ' || extname || ' ' || extversion FROM pg_extension ORDER BY extname;
SELECT 'exclusion ' || conrelid::regclass || ' ' || conname || ' ' || pg_get_constraintdef(oid)
  FROM pg_constraint WHERE contype = 'x' ORDER BY conname;
SELECT 'invalid-index ' || indexrelid::regclass FROM pg_index WHERE NOT indisvalid;
SELECT 'rows ' || table_name || ' ' || (xpath('/row/c/text()', query_to_xml(
  format('SELECT count(*) AS c FROM public.%I', table_name), false, true, '')))[1]::text
  FROM information_schema.tables
  WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name;
"
compose exec -T postgres psql -U gpureserve -d gpureserve -At -v ON_ERROR_STOP=1 \
  -c "$checks" >"$work/production.txt"
docker exec "$name" psql -h 127.0.0.1 -U gpureserve -d gpureserve -At -v ON_ERROR_STOP=1 \
  -c "$checks" >"$work/restored.txt"

echo "== 3. restored copy"
cat "$work/restored.txt"
echo "== 4. differences from production now (rows written after the dump show here)"
diff "$work/production.txt" "$work/restored.txt" && echo "none"

echo "== 5. exclusion constraint enforced in the restored copy"
overlap="BEGIN;
INSERT INTO gpu_allocations (reservation_id, gpu_id, starts_at, ends_at, active)
  SELECT reservation_id, gpu_id, starts_at, ends_at, true FROM gpu_allocations WHERE active LIMIT 1;
ROLLBACK;"
if out="$(docker exec "$name" psql -h 127.0.0.1 -U gpureserve -d gpureserve -v ON_ERROR_STOP=1 \
  -c "$overlap" 2>&1)"; then
  echo "FAIL: an overlapping allocation was accepted"; echo "$out"; exit 1
fi
echo "$out" | grep -o 'violates exclusion constraint "[a-z_]*"'

echo "== 6. memory while the copy ran"
docker stats --no-stream --format '{{.Name}} {{.MemUsage}}'
free -m
