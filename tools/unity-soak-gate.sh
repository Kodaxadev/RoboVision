#!/usr/bin/env bash
set -euo pipefail

# Soak the RoboVision Unity host inside one long-lived editor process.
#
# The point is accumulated state. A harness that starts a clean editor for each
# cycle would never catch an undo stack, a handle table, a snapshot cache or a
# managed heap that only misbehaves after hundreds of edits. So one editor is
# launched, it runs every cycle, and it reloads its own scripting domain at
# intervals so the reloads happen inside the soak rather than beside it.
#
# Usage:
#   tools/unity-soak-gate.sh [--cycles N] [--reload-every N] [--unity <binary>]

REPO="${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=tools/unity-common.sh
. "$REPO/tools/unity-common.sh"

PACKAGE_SRC="$REPO/hosts/unity/Packages/com.kodaxa.robovision"
PROJECT="${PROJECT:-$REPO/.unity-soak}"
ARTIFACTS="${ARTIFACTS:-$REPO/artifacts/unity-soak}"
REPORT="$ARTIFACTS/soak.json"
LOG="$ARTIFACTS/editor.log"
CYCLES="${ROBOVISION_SOAK_CYCLES:-200}"
RELOAD_EVERY="${ROBOVISION_SOAK_RELOAD_EVERY:-50}"
UNITY_EXE="$(rv_find_unity "${UNITY_EXE:-}")"

while [ $# -gt 0 ]; do
  case "$1" in
    --cycles)       CYCLES="$2"; shift 2 ;;
    --reload-every) RELOAD_EVERY="$2"; shift 2 ;;
    --unity)        UNITY_EXE="$2"; shift 2 ;;
    *) rv_fail "unknown argument: $1" ;;
  esac
done

[ -d "$PACKAGE_SRC" ] || rv_fail "RoboVision Unity package not found: $PACKAGE_SRC"
[ -n "$UNITY_EXE" ] && [ -x "$UNITY_EXE" ] || rv_fail "Unity editor binary not found; set UNITY_EXE"
UNITY_VERSION="$(rv_unity_version "$UNITY_EXE" "${UNITY_VERSION:-}")"
[ -n "$UNITY_VERSION" ] || rv_fail "could not determine the Unity version; set UNITY_VERSION"

mkdir -p "$ARTIFACTS"
rm -f "$REPORT"

echo "RoboVision Unity soak"
echo "  editor:       $UNITY_EXE"
echo "  version:      $UNITY_VERSION"
echo "  cycles:       $CYCLES"
echo "  reload every: $RELOAD_EVERY"

rv_generate_project "$PROJECT" "$PACKAGE_SRC" "$UNITY_VERSION" wipe

set +e
ROBOVISION_SOAK_REPORT="$(rv_native "$REPORT")" \
ROBOVISION_SOAK_CYCLES="$CYCLES" \
ROBOVISION_SOAK_RELOAD_EVERY="$RELOAD_EVERY" \
"$UNITY_EXE" \
  -batchmode \
  -nographics \
  -disable-assembly-updater \
  -projectPath "$(rv_native "$PROJECT")" \
  -executeMethod Kodaxa.RoboVision.Editor.Tests.RoboVisionSoakGate.Run \
  -logFile "$(rv_native "$LOG")"
STATUS=$?
set -e

echo "  unity exit: $STATUS"

if [ -f "$LOG" ] && grep -qE "error CS[0-9]+" "$LOG"; then
  echo "  COMPILE ERRORS - any report below describes stale code:"
  grep -E "error CS[0-9]+" "$LOG" | sort -u | head -n 20 | sed 's/^/    /'
  rv_fail "the Unity assemblies did not compile"
fi

if [ -s "$REPORT" ]; then
  python - "$REPORT" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
print("  state:      {}".format(data.get("state")))
print("  cycles:     {} of {}".format(data.get("cycles_completed"), data.get("cycles_requested")))
print("  reloads:    {}".format(data.get("reloads")))
latency = data.get("latency_ms") or {}
if latency:
    print("  cycle ms:   mean {} p50 {} p95 {} max {}".format(
        latency.get("mean"), latency.get("p50"), latency.get("p95"), latency.get("max")))
print("  revision:   {}".format(data.get("revision")))
print("  scene roots:{}".format(data.get("scene_roots")))
print("  leaked:     {}".format(data.get("leaked_gameobjects")))
print("  undo group: {}".format(data.get("undo_group")))
print("  mono heap:  {:.1f} MB".format((data.get("mono_heap_bytes") or 0) / 1048576.0))
print("  fingerprint restored: {}".format(data.get("final_fingerprint") == data.get("baseline")))
for failure in data.get("failures", [])[:20]:
    print("  FAILURE:", failure)
PY
else
  echo "  no soak report was produced"
  grep -vE "SymType|SymGetSym|^0x|[.]dll:" "$LOG" 2>/dev/null | tail -n 20 | sed 's/^/    /' || true
fi

[ "$STATUS" = "0" ] || rv_fail "soak failed"
echo "ROBOVISION_UNITY_SOAK_PASS"
