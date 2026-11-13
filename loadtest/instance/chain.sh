#!/usr/bin/env bash
# On the k6 driver, as root (remote.sh driver chain.sh start <bucket>). Runs the timed
# phases one after another as a systemd unit of their own, so the whole sequence
# carries on without a laptop session, and stops at the first phase that fails (a
# failed run, including a failed race correctness check, fails its phase).
#
#   chain.sh start <bucket>     copy this script to the driver and start the chain
#   chain.sh status             the chain's log
#
# Ramp ceilings and soak rates come from the calibrate phase on the twin
# (loadtest/results.md): ceilings at twice each script's best successful throughput;
# the soak at about a sixth of each script's clean capacity, half the API's one core in
# all; the simulated baseline's ceilings scaled to its 0.4 CPU.
set -uo pipefail
readonly DIR=/opt/gpu-reserve-loadtest
readonly RAMPS=availability=200,browse_servers=150,slot_search=30
readonly BASELINE_RAMPS=availability=80,browse_servers=60,slot_search=12
readonly SOAK_RATES=browse_servers=12,availability=15,slot_search=2
readonly KEEPALIVE=(--configs cache-on --scripts "slot_search,booking_race" --skip-hit-rate --ramp-max "$RAMPS")

log() { printf '%s chain: %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

phase() {
  local label="$1"
  shift
  log "starting $label"
  bash "$DIR/src/loadtest/instance/phase.sh" start "$label" "$bucket" "$@" || return 1
  # "activating" counts as running: right after systemd-run the unit may not be active yet.
  while [[ "$(systemctl show -p ActiveState --value "loadtest-$label")" =~ ^(active|activating|reloading)$ ]]; do
    sleep 20
  done
  local result
  result="$(systemctl show -p Result --value "loadtest-$label")"
  log "$label finished: $result"
  aws s3 cp --only-show-errors "$DIR/logs/chain.log" "s3://$bucket/logs/" || true
  [ "$result" = success ]
}

run() {
  phase burst -- --base-url http://TWIN:80 --ramp-max "$RAMPS" &&
    phase keepalive-a -- --base-url http://TWIN:80 "${KEEPALIVE[@]}" &&
    phase keepalive-b API_UPSTREAM_KEEPALIVE=60s API_KEEP_ALIVE_SECONDS=75 -- \
      --base-url http://TWIN:80 "${KEEPALIVE[@]}" &&
    phase keepalive-c -- --base-url http://TWIN:8000 "${KEEPALIVE[@]}" &&
    phase soak -- --base-url http://TWIN:80 --soak 30m --soak-rates "$SOAK_RATES" --settle 10m &&
    phase baseline -- --base-url http://TWIN:80 --compose-file loadtest/compose.baseline.yml \
      --configs cache-on --capacity-only --repeats 1 --ramp-max "$BASELINE_RAMPS"
  local rc=$?
  if [ "$rc" = 0 ]; then log "all phases done"; else log "stopped: a phase failed"; fi
  aws s3 cp --only-show-errors "$DIR/logs/chain.log" "s3://$bucket/logs/" || true
  return "$rc"
}

case "${1:-}" in
  start)
    bucket="${2:?bucket}"
    systemctl is-active --quiet loadtest-chain && { echo "the chain is already running" >&2; exit 1; }
    systemctl reset-failed loadtest-chain 2>/dev/null || true
    install -d -m 0755 "$DIR/logs"
    install -m 0755 "$0" "$DIR/chain.sh"
    systemd-run --unit loadtest-chain --service-type=exec \
      --property "StandardOutput=append:$DIR/logs/chain.log" \
      --property "StandardError=append:$DIR/logs/chain.log" \
      /bin/bash "$DIR/chain.sh" run "$bucket"
    ;;
  run)
    bucket="${2:?bucket}"
    run
    ;;
  status)
    echo "loadtest-chain: $(systemctl show -p ActiveState -p Result "loadtest-chain" | sort | paste -sd ' ')"
    tail -n "${LINES_SHOWN:-20}" "$DIR/logs/chain.log" 2>/dev/null || echo "(no log yet)"
    ;;
  *)
    echo "usage: chain.sh start <bucket> | status" >&2
    exit 2
    ;;
esac
