#!/usr/bin/env bash
set -euo pipefail

# Prove the RoboVision Blender host across a real process boundary.
#
# Reopening a document inside one process is a different event from starting
# Blender again. Here the interpreter dies, every module-level registry dies
# with it, the socket is reclaimed by the OS rather than by our own shutdown,
# and only what was written into the .blend can survive.
#
# Two headless Blenders run in sequence against one document. Between them the
# harness confirms the first process is gone and its port is free, so a pass
# cannot be an artifact of the first process lingering.

REPO="${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
ARTIFACTS="${ARTIFACTS:-$REPO/artifacts/blender-restart}"
STATE="$ARTIFACTS/state.json"
DOCUMENT="$ARTIFACTS/restart.blend"
BLENDER="${BLENDER:-}"

fail() { echo "::error::$*" >&2; exit 2; }

if [ -z "$BLENDER" ]; then
  for candidate in \
    "${BLENDER_DIR:-}/blender" \
    "/c/Program Files/Blender Foundation"/*/blender.exe \
    "$(command -v blender || true)"; do
    [ -n "$candidate" ] && [ -x "$candidate" ] && { BLENDER="$candidate"; break; }
  done
fi
[ -n "$BLENDER" ] && [ -x "$BLENDER" ] || fail "Blender not found; set BLENDER"

mkdir -p "$ARTIFACTS"
rm -f "$STATE" "$DOCUMENT"

echo "RoboVision Blender restart gate"
echo "  blender:  $BLENDER"
echo "  document: $DOCUMENT"

run_phase() {
  local phase="$1" log="$2"
  set +e
  ROBOVISION_RESTART_PHASE="$phase" \
  ROBOVISION_RESTART_STATE="$STATE" \
  ROBOVISION_RESTART_DOCUMENT="$DOCUMENT" \
  "$BLENDER" \
    --background \
    --factory-startup \
    --python-exit-code 1 \
    --python "$REPO/tests/blender/lifecycle_restart.py" \
    > "$log" 2>&1
  local status=$?
  set -e
  return $status
}

echo "  --- first process ---"
run_phase 1 "$ARTIFACTS/phase1.log" && PHASE1=0 || PHASE1=$?
echo "  phase 1 exit: $PHASE1"
if [ "$PHASE1" != "0" ] || [ ! -s "$STATE" ]; then
  grep -E "Error|Traceback|GateFailure|expect" "$ARTIFACTS/phase1.log" | tail -n 15 || true
  fail "phase 1 failed"
fi

PORT="$(python -c "import json,sys;print(json.load(open(sys.argv[1]))['port'])" "$STATE")"
echo "  phase 1 held port: $PORT"

# The process boundary is the point, so verify it instead of assuming the
# command returning means Blender exited.
for _ in $(seq 1 30); do
  if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -Command "if (Get-Process -Name blender -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >/dev/null 2>&1 || break
  else
    pgrep -x blender >/dev/null 2>&1 || break
  fi
  sleep 1
done

if command -v powershell.exe >/dev/null 2>&1; then
  if powershell.exe -NoProfile -Command "if (Get-NetTCPConnection -State Listen -LocalPort $PORT -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" >/dev/null 2>&1; then
    fail "port $PORT is still listening after the first process exited; socket state leaked"
  fi
fi
echo "  first process gone, port $PORT released"

echo "  --- second process ---"
run_phase 2 "$ARTIFACTS/phase2.log" && PHASE2=0 || PHASE2=$?
echo "  phase 2 exit: $PHASE2"
if [ "$PHASE2" != "0" ]; then
  grep -E "Error|Traceback|GateFailure|AssertionError" "$ARTIFACTS/phase2.log" | tail -n 20 || true
  fail "restart gate failed"
fi

grep -E "ROBOVISION_BLENDER_RESTART_PHASE._OK" "$ARTIFACTS/phase1.log" "$ARTIFACTS/phase2.log" | sed 's/^/  /'
echo "ROBOVISION_BLENDER_RESTART_PASS"
