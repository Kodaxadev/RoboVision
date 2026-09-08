#!/usr/bin/env bash
set -euo pipefail

# Prove the RoboVision Unity host across a genuine editor restart.
#
# Domain reload is already covered by the EditMode suite. This is a different
# lifecycle boundary: the process dies, the package is resolved again from
# nothing, the listening socket is reclaimed by the OS rather than by our own
# shutdown handler, and every static is gone because the memory is gone.
#
# Two headless editors are launched in sequence against one generated project.
# Between them the harness confirms the first process is actually gone and that
# the port it held is free, so "it still works" cannot be an artifact of the old
# editor lingering.

REPO="${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# shellcheck source=tools/unity-common.sh
. "$REPO/tools/unity-common.sh"

PACKAGE_SRC="$REPO/hosts/unity/Packages/com.kodaxa.robovision"
PROJECT="${PROJECT:-$REPO/.unity-restart}"
ARTIFACTS="${ARTIFACTS:-$REPO/artifacts/unity-restart}"
STATE="$ARTIFACTS/state.json"
UNITY_EXE="$(rv_find_unity "${UNITY_EXE:-}")"

[ -d "$PACKAGE_SRC" ] || rv_fail "RoboVision Unity package not found: $PACKAGE_SRC"
[ -n "$UNITY_EXE" ] && [ -x "$UNITY_EXE" ] || rv_fail "Unity editor binary not found; set UNITY_EXE"
UNITY_VERSION="$(rv_unity_version "$UNITY_EXE" "${UNITY_VERSION:-}")"
[ -n "$UNITY_VERSION" ] || rv_fail "could not determine the Unity version; set UNITY_VERSION"

mkdir -p "$ARTIFACTS"
rm -f "$STATE" "$STATE.phase2.json"

echo "RoboVision Unity restart gate"
echo "  editor:  $UNITY_EXE"
echo "  version: $UNITY_VERSION"
echo "  project: $PROJECT"

rv_generate_project "$PROJECT" "$PACKAGE_SRC" "$UNITY_VERSION" wipe

run_phase() {
  local method="$1" log="$2"
  set +e
  ROBOVISION_RESTART_STATE="$(rv_native "$STATE")" \
  "$UNITY_EXE" \
    -batchmode \
    -nographics \
    -disable-assembly-updater \
    -projectPath "$(rv_native "$PROJECT")" \
    -executeMethod "$method" \
    -logFile "$(rv_native "$log")"
  local status=$?
  set -e
  return $status
}

echo "  --- first editor ---"
run_phase "Kodaxa.RoboVision.Editor.Tests.RoboVisionRestartGate.Phase1" "$ARTIFACTS/phase1.log" \
  && PHASE1=0 || PHASE1=$?
echo "  phase 1 exit: $PHASE1"
[ -s "$STATE" ] || { grep -E "error CS|Exception|ROBOVISION_RESTART" "$ARTIFACTS/phase1.log" | tail -n 15; rv_fail "phase 1 wrote no state"; }
[ "$PHASE1" = "0" ] || { python -c "import json,sys;print('  phase1:', json.load(open(sys.argv[1])))" "$STATE"; rv_fail "phase 1 failed"; }

PORT="$(python -c "import json,sys;print(json.load(open(sys.argv[1]))['port'])" "$STATE")"
echo "  phase 1 held port: $PORT"

# The process boundary is the point of this gate, so verify it rather than
# assuming the editor exited just because the command returned.
for _ in $(seq 1 30); do
  rv_unity_running_for "$PROJECT" || break
  sleep 1
done
if rv_unity_running_for "$PROJECT"; then
  rv_fail "a Unity process is still holding $PROJECT; the restart boundary was not real"
fi
echo "  first editor process is gone"

if rv_port_in_use "$PORT"; then
  rv_fail "port $PORT is still listening after the editor exited; socket state leaked"
fi
echo "  port $PORT released"

echo "  --- second editor ---"
run_phase "Kodaxa.RoboVision.Editor.Tests.RoboVisionRestartGate.Phase2" "$ARTIFACTS/phase2.log" \
  && PHASE2=0 || PHASE2=$?
echo "  phase 2 exit: $PHASE2"

if [ -s "$STATE.phase2.json" ]; then
  python - "$STATE.phase2.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
checks = [k for k, v in data.items() if isinstance(v, bool)]
for name in checks:
    print("  {} {}".format("PASS" if data[name] else "FAIL", name))
for failure in data.get("failures", []):
    print("  detail:", failure)
PY
else
  echo "  phase 2 produced no findings"
  grep -E "error CS|Exception|ROBOVISION_RESTART" "$ARTIFACTS/phase2.log" | tail -n 15 || true
fi

[ "$PHASE2" = "0" ] || rv_fail "restart gate failed at the process boundary"

# A project restart reuses the resolved package cache and the compiled
# assemblies. Package re-resolution is its own lifecycle event, so force it
# rather than assuming the restart covered it: with the cache and the built
# assemblies removed, Unity must fetch, resolve and rebuild the package before
# any of the contract below can hold.
echo "  --- package re-resolution ---"
rm -rf "$PROJECT/Library/PackageCache" "$PROJECT/Library/ScriptAssemblies"
rm -f "$PROJECT/Packages/packages-lock.json"
rm -f "$STATE.phase2.json"
echo "  removed PackageCache, ScriptAssemblies and packages-lock.json"

run_phase "Kodaxa.RoboVision.Editor.Tests.RoboVisionRestartGate.Phase2" "$ARTIFACTS/phase3.log"   && PHASE3=0 || PHASE3=$?
echo "  re-resolve exit: $PHASE3"

if [ -s "$STATE.phase2.json" ]; then
  python - "$STATE.phase2.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
for name, value in data.items():
    if isinstance(value, bool):
        print("  {} {}".format("PASS" if value else "FAIL", name))
for failure in data.get("failures", []):
    print("  detail:", failure)
PY
else
  echo "  re-resolution produced no findings"
  grep -E "error CS|Exception|ROBOVISION_RESTART" "$ARTIFACTS/phase3.log" | tail -n 15 || true
fi

[ "$PHASE3" = "0" ] || rv_fail "restart gate failed after package re-resolution"
echo "ROBOVISION_UNITY_RESTART_PASS"
