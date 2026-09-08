#!/usr/bin/env bash
set -euo pipefail

# Generate a throwaway Unity project that installs the RoboVision package the
# way a real project does — a Package Manager dependency plus a `testables`
# entry — and run the package's EditMode tests inside a real Unity Editor.
#
# The manifest is written by hand rather than via -createProject. A template
# project pulls in Rider, Visual Studio, Analytics, Purchasing, Timeline, AI
# Navigation and more, and Unity's Bee backend then fans out a compiler process
# per assembly. On a machine near its commit limit that fan-out is what fails,
# with "The paging file is too small for this operation to complete". Listing
# only what the package actually needs keeps the assembly count small.
#
# Usage:
#   tools/unity-testbed.sh [--unity <editor binary>] [--project <dir>]
#                          [--results <xml>] [--log <file>] [--keep]
#
# Environment:
#   UNITY_EXE      path to the Unity editor binary
#   UNITY_VERSION  version string for ProjectVersion.txt (else derived)

REPO="${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PACKAGE_SRC="$REPO/hosts/unity/Packages/com.kodaxa.robovision"
PROJECT="${PROJECT:-$REPO/.unity-testbed}"
RESULTS="${RESULTS:-$REPO/artifacts/unity-gate4/results.xml}"
LOG="${LOG:-$REPO/artifacts/unity-gate4/editor.log}"
UNITY_EXE="${UNITY_EXE:-}"
KEEP=0

fail() { echo "::error::$*" >&2; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --unity)   UNITY_EXE="$2"; shift 2 ;;
    --project) PROJECT="$2"; shift 2 ;;
    --results) RESULTS="$2"; shift 2 ;;
    --log)     LOG="$2"; shift 2 ;;
    --keep)    KEEP=1; shift ;;
    *) fail "unknown argument: $1" ;;
  esac
done

if command -v cygpath >/dev/null 2>&1; then
  native() { cygpath -w "$1"; }
else
  native() { printf %s "$1"; }
fi

[ -d "$PACKAGE_SRC" ] || fail "RoboVision Unity package not found: $PACKAGE_SRC"

if [ -z "$UNITY_EXE" ]; then
  for candidate in \
    /opt/unity/Editor/Unity \
    "/c/Program Files/Unity/Hub/Editor"/*/Editor/Unity.exe; do
    [ -x "$candidate" ] && { UNITY_EXE="$candidate"; break; }
  done
fi
[ -n "$UNITY_EXE" ] && [ -x "$UNITY_EXE" ] || fail "Unity editor binary not found; pass --unity"

if [ -z "${UNITY_VERSION:-}" ]; then
  UNITY_VERSION="$(printf %s "$UNITY_EXE" | grep -oE '[0-9]{4,}[.][0-9]+[.][0-9]+[a-z0-9]*' | head -n 1 || true)"
fi
[ -n "$UNITY_VERSION" ] || fail "could not determine the Unity version; set UNITY_VERSION"

echo "RoboVision Unity testbed"
echo "  editor:  $UNITY_EXE"
echo "  version: $UNITY_VERSION"
echo "  project: $PROJECT"

rm -rf "$PROJECT"
mkdir -p "$PROJECT/Assets" "$PROJECT/Packages" "$PROJECT/ProjectSettings"
mkdir -p "$(dirname "$RESULTS")" "$(dirname "$LOG")"

# Install the package by path, exactly as a consumer would, so a broken
# assembly definition or a missing dependency fails here rather than silently
# producing an editor with no RoboVision in it.
PACKAGE_REF="file:$(native "$PACKAGE_SRC" | sed 's|\\|/|g')"
cat > "$PROJECT/Packages/manifest.json" <<EOF
{
  "dependencies": {
    "com.kodaxa.robovision": "$PACKAGE_REF",
    "com.unity.nuget.newtonsoft-json": "3.0.2",
    "com.unity.test-framework": "1.4.5",
    "com.unity.modules.imageconversion": "1.0.0",
    "com.unity.modules.screencapture": "1.0.0",
    "com.unity.modules.jsonserialize": "1.0.0",
    "com.unity.modules.uielements": "1.0.0"
  },
  "testables": [
    "com.kodaxa.robovision"
  ]
}
EOF

printf 'm_EditorVersion: %s\n' "$UNITY_VERSION" > "$PROJECT/ProjectSettings/ProjectVersion.txt"

echo "  manifest:"
sed 's/^/    /' "$PROJECT/Packages/manifest.json"

set +e
"$UNITY_EXE" \
  -batchmode \
  -nographics \
  -disable-assembly-updater \
  -projectPath "$(native "$PROJECT")" \
  -runTests \
  -testPlatform EditMode \
  -testResults "$(native "$RESULTS")" \
  -logFile "$(native "$LOG")"
STATUS=$?
set -e

echo "  unity exit: $STATUS"
if [ -s "$RESULTS" ]; then
  python - "$RESULTS" <<'PY'
import sys, xml.etree.ElementTree as ET
root = ET.parse(sys.argv[1]).getroot()
print("  tests: total={} passed={} failed={} skipped={} inconclusive={}".format(
    root.get("total"), root.get("passed"), root.get("failed"),
    root.get("skipped"), root.get("inconclusive")))
for case in root.iter("test-case"):
    if case.get("result") != "Passed":
        message = case.findtext("failure/message") or ""
        print("  FAILED {}: {}".format(case.get("fullname"), " ".join(message.split())[:400]))
PY
else
  echo "  no test results were produced"
  if [ -f "$LOG" ]; then
    if grep -q "Native Crash Reporting" "$LOG"; then
      echo "  the editor crashed before writing results"
      grep -nE "The paging file is too small|Couldn.t launch process|OutOfMemory" "$LOG" | head -n 3 | sed 's/^/    /'
    fi
    echo "  --- compile errors ---"
    grep -E "error CS[0-9]+" "$LOG" | sort -u | head -n 20 | sed 's/^/    /' || true
    echo "  --- editor log tail ---"
    grep -vE "SymType|SymGetSym|^0x|[.]dll:" "$LOG" | tail -n 25 | sed 's/^/    /'
  fi
fi

[ "$KEEP" = "1" ] || rm -rf "$PROJECT/Library/ShaderCache" 2>/dev/null || true
exit "$STATUS"
