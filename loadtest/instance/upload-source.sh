#!/usr/bin/env bash
# From a laptop: uploads HEAD of this repository, with its commit in a COMMIT file, to
# the load-test results bucket, where driver-setup.sh and twin-setup.sh fetch it.
# Refuses a working tree with changes, so the twin always runs a commit.
#
#   AWS_PROFILE=gpu-reserve AWS_REGION=us-east-2 loadtest/instance/upload-source.sh <bucket>
set -euo pipefail
bucket="${1:?usage: upload-source.sh <bucket>}"
cd "$(git rev-parse --show-toplevel)"
[ -z "$(git status --porcelain)" ] || { echo "the working tree has changes; commit them first" >&2; exit 1; }
commit="$(git rev-parse HEAD)"
archive="$(mktemp)"
trap 'rm -f "$archive"' EXIT
git archive --format=tar.gz --add-virtual-file="COMMIT:$commit" -o "$archive" HEAD
aws s3 cp --only-show-errors "$archive" "s3://$bucket/src.tar.gz"
echo "uploaded $commit"
