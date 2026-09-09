#!/usr/bin/env bash
# Shared plumbing for the RoboVision Unity harnesses.
#
# Sourced by unity-testbed.sh and unity-restart-gate.sh so both generate an
# identical project. A restart test that built its project differently from the
# EditMode testbed would be testing a different thing than the one we report on.

# shellcheck disable=SC2034

rv_fail() { echo "::error::$*" >&2; exit 2; }

if command -v cygpath >/dev/null 2>&1; then
  rv_native() { cygpath -w "$1"; }
else
  rv_native() { printf %s "$1"; }
fi

# Locate a Unity editor binary: an explicit value, then the Linux CI path, then
# whatever the Hub has installed.
rv_find_unity() {
  local given="${1:-}"
  if [ -n "$given" ]; then printf %s "$given"; return 0; fi
  local candidate
  for candidate in \
    /opt/unity/Editor/Unity \
    "/c/Program Files/Unity/Hub/Editor"/*/Editor/Unity.exe; do
    [ -x "$candidate" ] && { printf %s "$candidate"; return 0; }
  done
  return 0
}

# Hub installs encode the version in the path; a CI image does not.
rv_unity_version() {
  local exe="$1" given="${2:-}"
  if [ -n "$given" ]; then printf %s "$given"; return 0; fi
  printf %s "$exe" | grep -oE '[0-9]{4,}[.][0-9]+[.][0-9]+[a-z0-9]*' | head -n 1 || true
}

# Generate a throwaway project that installs the package the way a consumer
# does. Written by hand rather than via -createProject: a template project pulls
# in a few dozen extra packages, and Bee then runs one compiler process per
# assembly, which is what exhausts a constrained machine's commit limit.
rv_generate_project() {
  local project="$1" package_src="$2" version="$3" wipe="${4:-wipe}"

  if [ "$wipe" = "wipe" ]; then rm -rf "$project"; fi
  mkdir -p "$project/Assets" "$project/Packages" "$project/ProjectSettings"

  local package_ref
  package_ref="file:$(rv_native "$package_src" | sed 's|\\|/|g')"
  cat > "$project/Packages/manifest.json" <<EOF
{
  "dependencies": {
    "com.kodaxa.robovision": "$package_ref",
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
  printf 'm_EditorVersion: %s\n' "$version" > "$project/ProjectSettings/ProjectVersion.txt"
}

# True while any Unity process is holding the given project path.
rv_unity_running_for() {
  local project="$1"
  if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -Command "
      \$p = Get-CimInstance Win32_Process -Filter \"Name='Unity.exe'\" -ErrorAction SilentlyContinue |
            Where-Object { \$_.CommandLine -like '*$(basename "$project")*' }
      if (\$p) { exit 0 } else { exit 1 }" >/dev/null 2>&1
    return $?
  fi
  pgrep -f "Unity.*$(basename "$project")" >/dev/null 2>&1
}

# True while something is listening on a loopback port.
rv_port_in_use() {
  local port="$1"
  if command -v powershell.exe >/dev/null 2>&1; then
    powershell.exe -NoProfile -Command "
      if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }" \
      >/dev/null 2>&1
    return $?
  fi
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "sport = :$port" 2>/dev/null | grep -q LISTEN
    return $?
  fi
  return 1
}
