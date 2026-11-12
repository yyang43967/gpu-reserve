#!/usr/bin/env bash
# On the k6 driver, as root (remote.sh driver phase.sh ...). Runs one phase of the load
# test as a systemd unit, so it carries on if the SSM command or the laptop's session
# ends, and copies loadtest/out to the results bucket when it finishes.
#
#   phase.sh start <label> <bucket> [KEY=VALUE ...] -- <run.py arguments>
#   phase.sh status <label>         running or finished, and the log's last lines
#   phase.sh check                  the twin's Docker over SSH, and k6
#
# KEY=VALUE pairs become the runner's environment: API_KEEP_ALIVE_SECONDS and
# API_UPSTREAM_KEEPALIVE for the keep-alive arms. `--base-url` may use TWIN, which is
# replaced with the twin's address. The Compose files, project, and env file are added
# here; run.py's own options follow `--`.
set -euo pipefail
readonly DIR=/opt/gpu-reserve-loadtest
twin="$(cat "$DIR/twin")"
docker_host="ssh://ec2-user@$twin"

trust_twin() {
  install -d -m 0700 /root/.ssh
  grep -q "^$twin " /root/.ssh/known_hosts 2>/dev/null \
    || ssh-keyscan -t ed25519 "$twin" 2>/dev/null >>/root/.ssh/known_hosts
}

case "${1:-}" in
  check)
    trust_twin
    DOCKER_HOST="$docker_host" docker info --format 'twin docker {{.ServerVersion}}, {{.NCPU}} CPUs, {{.MemTotal}} bytes'
    k6 version | head -1
    ;;
  start)
    label="${2:?label}" bucket="${3:?bucket}"
    shift 3
    [[ "$label" =~ ^[a-z0-9-]{1,40}$ ]] || { echo "bad label" >&2; exit 2; }
    unit="loadtest-$label"
    systemctl is-active --quiet "$unit" && { echo "$unit is already running" >&2; exit 1; }
    systemctl reset-failed "$unit" 2>/dev/null || true
    setenv=(--setenv "DOCKER_HOST=$docker_host")
    while [ $# -gt 0 ] && [ "$1" != -- ]; do
      [[ "$1" =~ ^(API_KEEP_ALIVE_SECONDS|API_UPSTREAM_KEEPALIVE)=[0-9a-z]+$ ]] || { echo "bad setting $1" >&2; exit 2; }
      setenv+=(--setenv "$1")
      shift
    done
    [ "${1:-}" = -- ] && shift
    args=()
    for arg in "$@"; do args+=("${arg//TWIN/$twin}"); done
    trust_twin
    install -d -m 0755 "$DIR/logs"
    systemd-run --unit "$unit" --working-directory "$DIR/src" --service-type=exec \
      --property "StandardOutput=append:$DIR/logs/$label.log" \
      --property "StandardError=append:$DIR/logs/$label.log" \
      "${setenv[@]}" \
      /bin/bash "$DIR/src/loadtest/instance/run-phase.sh" "$label" "$bucket" "${args[@]}"
    echo "started $unit"
    ;;
  status)
    label="${2:?label}"
    unit="loadtest-$label"
    echo "$unit: $(systemctl show -p ActiveState,Result,ExecMainStatus --value "$unit" | paste -sd ' ')"
    tail -n "${LINES_SHOWN:-25}" "$DIR/logs/$label.log" 2>/dev/null || echo "(no log yet)"
    ;;
  *)
    echo "usage: phase.sh start|status|check ..." >&2
    exit 2
    ;;
esac
