#!/usr/bin/env bash
# Runs release.sh's SSM commands (verify-backup, deploy) against a stub `aws` that
# plays the SSM agent: it joins the commands with newlines and runs them with sh (dash
# here). Checks that a bash script runs under bash, that its exit status decides
# success, and that a failed send stops at once instead of polling.
# Locally: docker run --rm -v "$PWD:/src:ro" -w /src python:3.12-slim bash infra/deploy/tests/release-test.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TAG=0123456789abcdef0123456789abcdef01234567

fail() { echo "FAIL: $*" >&2; exit 1; }

setup() {
  WORK="$(mktemp -d)"
  # release.sh finds verify-backup.sh from its own location, so a scratch tree with a
  # stand-in script tests it without the real one.
  mkdir -p "$WORK/root/infra/deploy" "$WORK/bin"
  cp "$HERE/../release.sh" "$WORK/root/infra/deploy/"
  cat >"$WORK/root/infra/deploy/verify-backup.sh" <<'BODY'
echo "first line"
# Bash only: dash rejects [[ ]] and arrays.
steps=(restore compare)
[[ "${steps[1]}" == compare ]] && echo "ran under bash"
exit "${VERIFY_EXIT:-0}"
BODY

  cat >"$WORK/bin/aws" <<'STUB'
#!/usr/bin/env python3
import json, os, subprocess, sys
work = os.environ["STUB_WORK"]
args = sys.argv[1:]
def arg(name):
    return args[args.index(name) + 1]
with open(f"{work}/calls.log", "a") as log:
    log.write(" ".join(args[:2]) + "\n")
if args[:2] == ["ec2", "describe-instances"]:
    print("i-0123456789abcdef0")
elif args[:2] == ["sts", "get-caller-identity"]:
    print("123456789012")
elif args[:2] == ["ecr", "describe-images"]:
    pass
elif args[:2] == ["ssm", "get-parameter"]:
    print("api.example.test")
elif args[:2] == ["ssm", "send-command"]:
    if os.environ.get("STUB_SEND_FAILS"):
        sys.exit("An error occurred (AccessDenied)")
    params = json.loads(arg("--parameters"))
    json.dump(params, open(f"{work}/parameters.json", "w"))
    if arg("--comment").startswith("deploy"):
        rc, out = 0, "deployed\n"
    else:
        script = f"{work}/_script.sh"
        open(script, "w").write("\n".join(params["commands"]) + "\n")
        run = subprocess.run(["sh", script], capture_output=True, text=True, env=os.environ)
        rc, out = run.returncode, run.stdout + run.stderr
    open(f"{work}/result", "w").write(json.dumps({"rc": rc, "out": out}))
    print("command-1")
elif args[:2] == ["ssm", "get-command-invocation"]:
    result = json.load(open(f"{work}/result"))
    if arg("--query") == "Status":
        print("Success" if result["rc"] == 0 else "Failed")
    else:
        print(result["out"])
else:
    sys.exit(f"unexpected call: {args}")
STUB
  printf '#!/bin/sh\nexit 0\n' >"$WORK/bin/curl"
  chmod +x "$WORK/bin/aws" "$WORK/bin/curl"
}

run_release() {
  PATH="$WORK/bin:$PATH" STUB_WORK="$WORK" AWS_REGION=us-east-2 SSM_POLL_SECONDS=0 \
    timeout 60 bash "$WORK/root/infra/deploy/release.sh" "$@" >"$WORK/output.txt" 2>&1
}

test_verify_backup_runs_the_script_under_bash() {
  setup
  run_release verify-backup || { cat "$WORK/output.txt"; fail "verify-backup failed"; }
  grep -q "first line" "$WORK/output.txt" || fail "the script's output is missing"
  grep -q "ran under bash" "$WORK/output.txt" || fail "the script did not run under bash"
  grep -q "matches production" "$WORK/output.txt" || fail "no success message"
  python3 - "$WORK/parameters.json" "$WORK/root/infra/deploy/verify-backup.sh" <<'PY' \
    || fail "the script was not sent unchanged"
import json, sys
params, body = json.load(open(sys.argv[1])), open(sys.argv[2]).read().rstrip("\n")
assert body in params["commands"], params["commands"]
assert params["executionTimeout"] == ["900"]
PY
  rm -rf "$WORK"
}

test_verify_backup_fails_when_the_script_fails() {
  setup
  if VERIFY_EXIT=3 run_release verify-backup; then fail "a failing script passed"; fi
  grep -q "backup verification failed" "$WORK/output.txt" || fail "no failure message"
  grep -q "ran under bash" "$WORK/output.txt" || fail "the script did not finish"
  rm -rf "$WORK"
}

test_failed_send_stops_at_once() {
  setup
  if STUB_SEND_FAILS=1 run_release verify-backup; then fail "a failed send passed"; fi
  grep -q "backup verification failed" "$WORK/output.txt" || fail "no failure message"
  if grep -q "get-command-invocation" "$WORK/calls.log"; then fail "it polled after a failed send"; fi
  rm -rf "$WORK"
}

test_deploy_sends_its_script_through_the_same_path() {
  setup
  run_release deploy "$TAG" || { cat "$WORK/output.txt"; fail "deploy failed"; }
  python3 - "$WORK/parameters.json" "$TAG" <<'PY' || fail "unexpected deploy command"
import json, sys
params, tag = json.load(open(sys.argv[1])), sys.argv[2]
commands = "\n".join(params["commands"])
assert f"tag='{tag}'" in commands, commands
assert "repo='123456789012.dkr.ecr.us-east-2.amazonaws.com/gpu-reserve/api'" in commands, commands
assert 'bash "$dir/deploy.sh"' in commands, commands
assert commands.rstrip().endswith('bash "$f"; rc=$?; rm -f "$f"; exit $rc'), commands
assert params["executionTimeout"] == ["1800"]
PY
  grep -q "deployed $TAG; the API is ready" "$WORK/output.txt" || fail "no readiness check"
  rm -rf "$WORK"
}

for test in test_verify_backup_runs_the_script_under_bash test_verify_backup_fails_when_the_script_fails \
  test_failed_send_stops_at_once test_deploy_sends_its_script_through_the_same_path; do
  "$test"
  echo "ok $test"
done
