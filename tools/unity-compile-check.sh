#!/usr/bin/env bash
set -euo pipefail

# Compile the RoboVision Unity Editor package with Unity's bundled Roslyn
# compiler and reference assemblies without launching or licensing Unity.
# This is a semantic compile gate, not an Editor/Test Runner replacement.

UNITY_DATA="${UNITY_DATA:-/opt/unity/Editor/Data}"
REPO="${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
EXTRA_REFS="${EXTRA_REFS:-$REPO/.unity-compile-refs}"
OUT="${OUT:-/tmp/robovision-unity-compile}"
SRC="$REPO/hosts/unity/Packages/com.kodaxa.robovision/Editor"

# Unity moves its bundled Roslyn between releases: 6000.0 ships
# DotNetSdkRoslyn/csc.dll, 6000.6 ships DotNetSdk/sdk/<version>/Roslyn/bincore.
# Discover it rather than pinning one layout, so this gate can run against
# whichever Unity 6 a developer actually has installed.
find_csc() {
  local candidate
  candidate="$UNITY_DATA/DotNetSdkRoslyn/csc.dll"
  [ -f "$candidate" ] && { printf '%s' "$candidate"; return 0; }
  # A miss here is a normal outcome, not a script error: report it through the
  # empty result so the caller can fail with a message instead of set -e
  # aborting silently.
  find "$UNITY_DATA/DotNetSdk" -name csc.dll -path '*Roslyn*' -print 2>/dev/null | sort | head -n 1 || true
}

find_dotnet() {
  local candidate
  for candidate in \
    "$UNITY_DATA/NetCoreRuntime/dotnet" \
    "$UNITY_DATA/NetCoreRuntime/dotnet.exe" \
    "$UNITY_DATA/DotNetSdk/dotnet" \
    "$UNITY_DATA/DotNetSdk/dotnet.exe"; do
    [ -x "$candidate" ] && { printf '%s' "$candidate"; return 0; }
  done
  command -v dotnet || true
}

CSC="$(find_csc)"
DOTNET="$(find_dotnet)"

# Git Bash hands out POSIX paths (/d/repo, /c/Program Files/...) that a native
# Windows csc cannot open. cygpath -m yields the mixed form both accept; on
# Linux CI there is no cygpath and paths pass through unchanged.
if command -v cygpath >/dev/null 2>&1; then
  native() { cygpath -m "$1"; }
else
  native() { printf %s "$1"; }
fi

fail() { echo "::error::$*" >&2; exit 2; }

[ -d "$SRC" ] || fail "Unity Editor source directory not found: $SRC"
[ -n "$CSC" ] && [ -f "$CSC" ] || fail "Unity Roslyn compiler not found under $UNITY_DATA"
[ -n "$DOTNET" ] || fail "dotnet runtime not found"
[ -f "$EXTRA_REFS/Newtonsoft.Json.dll" ] || fail "Newtownsoft.Json.dll not found in $EXTRA_REFS"

# Derive the Unity version from the Hub directory layout unless told otherwise,
# so the gate compiles the same branch the installed editor would.
if [ -z "${UNITY_VERSION:-}" ]; then
  # A Hub install encodes the version in its path; a CI image such as
  # /opt/unity/Editor/Data does not. grep exiting 1 there must not take
  # the whole script down under set -e, so the miss is defaulted below.
  UNITY_VERSION="$(printf %s "$UNITY_DATA" | grep -oE '[0-9]{4,}[.][0-9]+[.][0-9]+' | head -n 1 || true)"
fi
UNITY_VERSION="${UNITY_VERSION:-6000.0.0}"
UNITY_MAJOR="${UNITY_VERSION%%.*}"
UNITY_MINOR="$(printf %s "$UNITY_VERSION" | cut -d. -f2)"
case "$(uname -s)" in
  Linux*)  EDITOR_PLATFORM_DEFINE="UNITY_EDITOR_LINUX" ;;
  Darwin*) EDITOR_PLATFORM_DEFINE="UNITY_EDITOR_OSX" ;;
  *)       EDITOR_PLATFORM_DEFINE="UNITY_EDITOR_WIN" ;;
esac

rm -rf "$OUT"
mkdir -p "$OUT"
RSP="$OUT/RoboVision.Editor.rsp"

# Version symbols must describe the Unity whose assemblies we compile against.
# Pinning them to the 6000.0 floor while linking newer reference assemblies
# selects the wrong branch of a version-guarded API and reports errors the real
# editor would never produce.
{
  echo "-target:library"
  echo "-langversion:9.0"
  echo "-nostdlib+"
  echo "-nullable:disable"
  echo "-preferreduilang:en-US"
  echo "-nowarn:CS1701,CS1702"
  echo "-out:\"$(native "$OUT/Kodaxa.RoboVision.Editor.dll")\""
  echo "-define:UNITY_EDITOR"
  echo "-define:$EDITOR_PLATFORM_DEFINE"
  echo "-define:UNITY_6000"
  echo "-define:UNITY_${UNITY_MAJOR}_${UNITY_MINOR}"
  for minor in $(seq 0 "$UNITY_MINOR"); do
    echo "-define:UNITY_${UNITY_MAJOR}_${minor}_OR_NEWER"
  done

  # Unity's own compile graph uses the 4.8 reference API plus the modular
  # UnityEngine/UnityEditor managed assemblies. Restrict globs to those
  # directories rather than all of Editor/Data, which contains conflicting
  # vendored framework copies.
  find "$UNITY_DATA/UnityReferenceAssemblies/unity-4.8-api" \
    -maxdepth 2 -type f -name '*.dll' -print0 \
    | sort -z \
    | while IFS= read -r -d '' ref; do printf '%s\n' "-r:\"$(native "$ref")\""; done

  # 6000.0 keeps the UnityEditor assemblies beside the UnityEngine modules;
  # 6000.6 keeps UnityEditor.dll one level up in Managed/. Reference both so the
  # gate works against whichever Unity 6 layout is installed.
  find "$UNITY_DATA/Managed/UnityEngine" "$UNITY_DATA/Managed" \
    -maxdepth 1 -type f \( -name 'UnityEngine*.dll' -o -name 'UnityEditor*.dll' \) -print0 \
    2>/dev/null \
    | sort -z -u \
    | while IFS= read -r -d '' ref; do printf '%s\n' "-r:\"$(native "$ref")\""; done

  printf '%s\n' "-r:\"$(native "$EXTRA_REFS/Newtonsoft.Json.dll")\""

  find "$SRC" -type f -name '*.cs' | sort | while IFS= read -r source; do
    printf '%s
' "\"$(native "$source")\""
  done
} > "$RSP"

# A UPM package whose scripts are not covered by an assembly definition is
# silently ignored by the Editor, so this gate would pass while the host never
# compiled inside Unity at all.
asmdef_count=$(find "$SRC" -type f -name '*.asmdef' | wc -l | tr -d ' ')
[ "$asmdef_count" -ge 1 ] || fail "no .asmdef under $SRC; Unity ignores package scripts without one"

source_count=$(find "$SRC" -type f -name '*.cs' | wc -l | tr -d ' ')
ref_count=$(grep -c '^-r:' "$RSP" || true)
echo "RoboVision Unity compile: $source_count sources, $ref_count references, $asmdef_count assembly definition(s)"
echo "  csc:    $CSC"
echo "  dotnet: $DOTNET"
echo "  unity:  $UNITY_VERSION"
"$DOTNET" "$CSC" "@$RSP"
[ -f "$OUT/Kodaxa.RoboVision.Editor.dll" ] || fail "compiler returned without producing output assembly"
echo "ROBOVISION_UNITY_COMPILE_PASS"
