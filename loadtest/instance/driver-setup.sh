#!/usr/bin/env bash
# On the k6 driver, as root (remote.sh driver driver-setup.sh ...). Installs k6
# (pinned, checksum checked), unpacks the source archive, creates the SSH key that
# reaches the twin's Docker, and writes the Compose env file with throwaway secrets.
# Prints the public key for twin-setup.sh. Safe to run again.
#
#   driver-setup.sh <results bucket> <image URI> <twin private IP>
set -euo pipefail
umask 022
bucket="$1" image="$2" twin="$3"
[[ "$twin" =~ ^[0-9]+(\.[0-9]+){3}$ ]] || { echo "the twin's address must be an IPv4 address" >&2; exit 1; }
readonly K6_VERSION=2.3.0
readonly K6_SHA256=5ca3433e8201da72a284aaa241a1bb5fb47f4abb4e384d39410ddd8062f49b90
readonly DIR=/opt/gpu-reserve-loadtest

cloud-init status --wait >/dev/null
install -d -m 0755 "$DIR"
# The runner needs Python 3.11 or later; the system python3 is 3.9.
dnf install -y -q python3.11 >/dev/null

if [ "$(k6 version 2>/dev/null | awk '{print $2}')" != "v$K6_VERSION" ]; then
  work="$(mktemp -d)"
  curl -fsSL -o "$work/k6.tgz" \
    "https://github.com/grafana/k6/releases/download/v$K6_VERSION/k6-v$K6_VERSION-linux-arm64.tar.gz"
  echo "$K6_SHA256  $work/k6.tgz" | sha256sum -c - >/dev/null
  tar -xzf "$work/k6.tgz" -C "$work"
  install -m 0755 "$work/k6-v$K6_VERSION-linux-arm64/k6" /usr/local/bin/k6
  rm -rf "$work"
fi

# The source at the commit being tested, at the same path as on the twin: Compose runs
# here, and the bind-mounted Caddyfile must exist at that path on the twin's side.
aws s3 cp --only-show-errors "s3://$bucket/src.tar.gz" "$DIR/src.tar.gz"
rm -rf "$DIR/src" && mkdir "$DIR/src" && tar -xzf "$DIR/src.tar.gz" -C "$DIR/src"

[ -f /root/.ssh/id_ed25519 ] || ssh-keygen -q -t ed25519 -N '' -f /root/.ssh/id_ed25519 -C loadtest-driver
echo "$twin" >"$DIR/twin"

# Throwaway values: this stack holds generated data only. Written once, so a rerun
# keeps the database password the twin's volume was created with.
if [ ! -f "$DIR/.env" ]; then
  (
    umask 077
    cat >"$DIR/.env" <<EOF
API_IMAGE=$image
POSTGRES_PASSWORD=$(openssl rand -hex 32)
JWT_SECRET=$(openssl rand -hex 32)
CORS_ORIGINS=http://localhost
FRONTEND_URL=http://localhost
AWS_REGION=us-east-2
S3_BUCKET_UPLOADS=none
EMAIL_FROM=Load test <no-reply@loadtest.example.edu>
API_SITE_ADDRESS=:80
EOF
  )
fi
grep -q "^API_IMAGE=$image\$" "$DIR/.env" || { echo "the env file names another image" >&2; exit 1; }

echo "k6: $(k6 version | head -1)"
echo "python: $(python3.11 --version)"
echo "source: $(cat "$DIR/src/COMMIT")"
echo "public key: $(cat /root/.ssh/id_ed25519.pub)"
