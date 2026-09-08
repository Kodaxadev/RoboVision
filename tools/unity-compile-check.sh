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
CSC="$UNITY_DATA/DotNetSdkRoslyn/csc.dll"
DOTNET="$UNITY_DATA/NetCoreRuntime/dotnet"

fail() { echo "::error::$*" >&2; exit 2; }

[ -d "$SRC" ] || fail "Unity Editor source directory not found: $SRC"
[ -f "$CSC" ] || fail "Unity Roslyn compiler not found: $CSC"
[ -x "$DOTNET" ] || DOTNET="$(command -v dotnet || true)"
[ -n "$DOTNET" ] || fail "dotnet runtime not found"
[ -f "$EXTRA_REFS/Newtonsoft.Json.dll" ] || fail "Newtownsoft.Json.dll not found in $EXTRA_REFS"

rm -rf "$OUT"
mkdir -p "$OUT"
RSP="$OUT/RoboVision.Editor.rsp"

# The package declares Unity 6000.0 as its minimum. Keep the version symbols
# deliberately pinned to that floor so newer-only API use fails this gate.
{
  echo "-target:library"
  echo "-langversion:9.0"
  echo "-nostdlib+"
  echo "-nullable:disable"
  echo "-preferreduilang:en-US"
  echo "-nowarn:CS1701,CS1702"
  echo "-out:$OUT/Kodaxa.RoboVision.Editor.dll"
  echo "-define:UNITY_EDITOR"
  echo "-define:UNITY_EDITOR_LINUX"
  echo "-define:UNITY_6000_0"
  echo "-define:UNITY_6000_0_OR_NEWER"
  echo "-define:UNITY_6000"

  # Unity's own compile graph uses the 4.8 reference API plus the modular
  # UnityEngine/UnityEditor managed assemblies. Restrict globs to those
  # directories rather than all of Editor/Data, which contains conflicting
  # vendored framework copies.
  find "$UNITY_DATA/UnityReferenceAssemblies/unity-4.8-api" \
    -maxdepth 2 -type f -name '*.dll' -print0 \
    | sort -z \
    | while IFS= read -r -d '' ref; do printf '%s\n' "-r:$ref"; done

  find "$UNITY_DATA/Managed/UnityEngine" \
    -maxdepth 1 -type f \( -name 'UnityEngine*.dll' -o -name 'UnityEditor*.dll' \) -print0 \
    | sort -z \
    | while IFS= read -r -d '' ref; do printf '%s\n' "-r:$ref"; done

  printf '%s\n' "-r:$EXTRA_REFS/Newtonsoft.Json.dll"

  find "$SRC" -type f -name '*.cs' | sort | while IFS= read -r source; do
    printf '%s\n' "$source"
  done
} > "$RSP"

source_count=$(find "$SRC" -type f -name '*.cs' | wc -l | tr -d ' ')
ref_count=$(grep -c '^-r:' "$RSP" || true)
echo "RoboVision Unity compile: $source_count sources, $ref_count references"
"$DOTNET" "$CSC" "@$RSP"
[ -f "$OUT/Kodaxa.RoboVision.Editor.dll" ] || fail "compiler returned without producing output assembly"
echo "ROBOVISION_UNITY_COMPILE_PASS"
