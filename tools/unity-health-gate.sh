#!/usr/bin/env bash
set -euo pipefail

# Prove that an external agent can discover Unity's pins and act on them, with
# the public Python client driving a real editor over a real socket.
#
# The EditMode suite cannot prove this. An in-process test can read
# RoboVisionRecipe.CoordinateContract() directly, so Unity enforced a coordinate
# contract it never published and every in-editor autonomous test kept passing
# while an external model had no way to construct a pinned call. Only a client
# outside the package finds that out.
#
# The claims are in tests/public_flow.py and run unchanged against Blender.
#
# Usage:
#   tools/unity-health-gate.sh [--unity <editor binary>] [--project <dir>]
#
# Environment:
#   UNITY_EXE          path to the Unity editor binary
#   UNITY_VERSION      version string for ProjectVersion.txt (else derived)
#   ROBOVISION_PYTHON  python interpreter the editor should launch

REPO="${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=tools/unity-common.sh
. "$REPO/tools/unity-common.sh"

PACKAGE_SRC="$REPO/hosts/unity/Packages/com.kodaxa.robovision"
PROJECT="${PROJECT:-$REPO/.unity-health}"
ARTIFACTS="${ARTIFACTS:-$REPO/artifacts/unity-health}"
REPORT="$ARTIFACTS/report.json"
LOG="$ARTIFACTS/editor.log"
UNITY_EXE="$(rv_find_unity "${UNITY_EXE:-}")"

while [ $# -gt 0 ]; do
  case "$1" in
    --unity)   UNITY_EXE="$2"; shift 2 ;;
    --project) PROJECT="$2"; shift 2 ;;
    *) rv_fail "unknown argument: $1" ;;
  esac
done

[ -d "$PACKAGE_SRC" ] || rv_fail "RoboVision Unity package not found: $PACKAGE_SRC"
[ -n "$UNITY_EXE" ] && [ -x "$UNITY_EXE" ] || rv_fail "Unity editor binary not found; set UNITY_EXE"
UNITY_VERSION="$(rv_unity_version "$UNITY_EXE" "${UNITY_VERSION:-}")"
[ -n "$UNITY_VERSION" ] || rv_fail "could not determine the Unity version; set UNITY_VERSION"

mkdir -p "$ARTIFACTS"
rm -f "$REPORT"

echo "RoboVision Unity health and public-flow gate"
echo "  editor:  $UNITY_EXE"
echo "  version: $UNITY_VERSION"
echo "  project: $PROJECT"

rv_generate_project "$PROJECT" "$PACKAGE_SRC" "$UNITY_VERSION" wipe

set +e
ROBOVISION_HEALTH_REPORT="$(rv_native "$REPORT")" \
ROBOVISION_PYTHON="${ROBOVISION_PYTHON:-python}" \
"$UNITY_EXE" \
  -batchmode \
  -nographics \
  -disable-assembly-updater \
  -projectPath "$(rv_native "$PROJECT")" \
  -executeMethod Kodaxa.RoboVision.Editor.Tests.RoboVisionHealthGate.Run \
  -logFile "$(rv_native "$LOG")"
STATUS=$?
set -e

echo "  unity exit: $STATUS"

# A test assembly that fails to compile leaves Unity running the previously
# built one, so the gate would report on stale code.
if [ -f "$LOG" ] && grep -qE "error CS[0-9]+" "$LOG"; then
  echo "  COMPILE ERRORS:"
  grep -E "error CS[0-9]+" "$LOG" | sort -u | head -n 20 | sed 's/^/    /'
  rv_fail "the Unity assemblies did not compile"
fi

if [ ! -s "$REPORT" ]; then
  echo "  --- editor log tail ---"
  grep -vE "SymType|SymGetSym|^0x|[.]dll:" "$LOG" | tail -n 30 | sed 's/^/    /' || true
  rv_fail "the gate wrote no report"
fi

python - "$REPORT" <<'PY'
import json, sys
report = json.load(open(sys.argv[1], encoding="utf-8"))
gate = report.get("gate") or {}
print("  result:", report.get("result"))
if report.get("error"):
    print("  error:", report["error"])
if report.get("python"):
    print("  python:", str(report["python"])[:400])
for name, value in sorted((gate.get("findings") or {}).items()):
    if name == "traceback":
        continue
    print(f"    {name}: {value}")
for failure in gate.get("failures") or []:
    print("  FAILED:", failure)
if (gate.get("findings") or {}).get("traceback"):
    print(gate["findings"]["traceback"])
sys.exit(0 if report.get("result") == "ok" else 1)
PY
