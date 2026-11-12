#!/usr/bin/env bash
# On the twin, as root (remote.sh twin twin-setup.sh ...). Lets the driver reach Docker
# over SSH, unpacks the source at the driver's path, pulls the release image with the
# twin's role, and creates the CPU slice for the simulated baseline. Safe to run again.
#
#   twin-setup.sh <results bucket> <image URI> <driver public key>
set -euo pipefail
umask 022
bucket="$1" image="$2" key="$3"
readonly DIR=/opt/gpu-reserve-loadtest
[[ "$key" =~ ^ssh-ed25519\ [A-Za-z0-9+/=]+\ loadtest-driver$ ]] || { echo "unexpected key" >&2; exit 1; }

cloud-init status --wait >/dev/null

usermod -aG docker ec2-user
install -d -m 0700 -o ec2-user -g ec2-user /home/ec2-user/.ssh
keys=/home/ec2-user/.ssh/authorized_keys
touch "$keys" && chown ec2-user:ec2-user "$keys" && chmod 0600 "$keys"
grep -qxF "$key" "$keys" || echo "$key" >>"$keys"

install -d -m 0755 "$DIR"
aws s3 cp --only-show-errors "s3://$bucket/src.tar.gz" "$DIR/src.tar.gz"
rm -rf "$DIR/src" && mkdir "$DIR/src" && tar -xzf "$DIR/src.tar.gz" -C "$DIR/src"

# Compose runs on the driver, whose Docker client has no ECR login; the image is
# pulled here, so `up` finds it locally.
registry="${image%%/*}"
aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin "$registry" >/dev/null
docker pull --quiet "$image" >/dev/null

# The simulated baseline (loadtest/compose.baseline.yml): 40% of one CPU for every
# container together, a t4g.small's 20% of each of its two vCPUs.
cat >/etc/systemd/system/loadtest-baseline.slice <<'EOF'
[Unit]
Description=Load test: a t4g.small's CPU baseline for all containers together

[Slice]
CPUQuota=40%
EOF
systemctl daemon-reload

echo "docker: $(docker info --format '{{.ServerVersion}} cgroup {{.CgroupDriver}} v{{.CgroupVersion}}')"
echo "image: $(docker image inspect --format '{{.Id}}' "$image")"
echo "source: $(cat "$DIR/src/COMMIT")"
echo "slice: $(systemctl show -p CPUQuotaPerSecUSec --value loadtest-baseline.slice)"
