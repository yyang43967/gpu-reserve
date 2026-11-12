#!/usr/bin/env bash
# Shellcheck for the deploy scripts with one pinned version, so CI and local runs
# cannot disagree. CI runs this script; locally, from anywhere in the repository:
#   bash infra/deploy/tests/shellcheck.sh
# Needs Docker. Raising the version is a deliberate change here.
set -euo pipefail

readonly IMAGE="koalaman/shellcheck:v0.11.0@sha256:61862eba1fcf09a484ebcc6feea46f1782532571a34ed51fedf90dd25f925a8d"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"
exec docker run --rm -v "$ROOT:/src:ro" -w /src "$IMAGE" \
  --external-sources --source-path=SCRIPTDIR \
  infra/deploy/*.sh infra/deploy/tests/*.sh
