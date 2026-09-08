"""Guards on the Unity package layout that decide whether Unity loads it at all.

These run in the host-independent suite because the failure they exist to catch
is invisible to a compiler. The RoboVision Unity host shipped without an
assembly definition, and Unity's manual states that "the Editor ignores scripts
inside the Packages folder unless they're part of an assembly definition". The
semantic compile gate passed the whole time, because compiling .cs files with
csc proves the C# is valid and nothing about whether Unity ever builds it.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "hosts" / "unity" / "Packages" / "com.kodaxa.robovision"
EDITOR = PACKAGE / "Editor"


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:  # pragma: no cover - the assertion carries the message
        pytest.fail(f"{path.relative_to(PACKAGE.parent)} is not valid JSON: {exc}")


def _assembly_definitions() -> list[Path]:
    return sorted(PACKAGE.rglob("*.asmdef"))


def test_package_manifest_is_valid():
    manifest = _load_json(PACKAGE / "package.json")
    assert manifest["name"] == "com.kodaxa.robovision"
    assert "unity" in manifest, "package.json must declare its minimum Unity version"


def test_editor_scripts_are_covered_by_an_assembly_definition():
    """The regression guard: no asmdef means Unity silently ignores the host."""
    definitions = _assembly_definitions()
    assert definitions, (
        "no .asmdef in the Unity package. Unity ignores package scripts that are not part of "
        "an assembly definition, so the whole Editor host would never compile inside Unity "
        "even while the semantic compile gate passes."
    )

    covered_roots = {path.parent for path in definitions}
    uncovered = []
    for script in sorted(PACKAGE.rglob("*.cs")):
        if not any(root in script.parents or root == script.parent for root in covered_roots):
            uncovered.append(script.relative_to(PACKAGE))
    assert not uncovered, f"C# files not covered by any assembly definition: {uncovered}"


def test_editor_assembly_definition_is_editor_only_and_well_formed():
    editor_definitions = [path for path in _assembly_definitions() if "Tests" not in path.parts]
    assert editor_definitions, "the Editor host has no assembly definition"

    for path in editor_definitions:
        data = _load_json(path)
        name = data.get("name")
        assert name, f"{path.name} has no assembly name"
        assert path.stem == name, (
            f"{path.name} must be named after its assembly ({name}); Unity resolves references by "
            "assembly name and a mismatch is a common silent break"
        )
        # An Editor assembly that is not Editor-only gets compiled into player
        # builds, where UnityEditor APIs do not exist.
        assert data.get("includePlatforms") == ["Editor"], (
            f"{name} must set includePlatforms to [\"Editor\"], got {data.get('includePlatforms')!r}"
        )
        assert not data.get("noEngineReferences"), f"{name} needs the engine references"


def test_assembly_definitions_have_meta_files():
    """A package distributed by git needs stable GUIDs, which live in .meta files."""
    missing = [
        path.relative_to(PACKAGE)
        for path in _assembly_definitions()
        if not path.with_suffix(path.suffix + ".meta").is_file()
    ]
    assert not missing, f"assembly definitions without .meta files: {missing}"


def test_every_script_has_a_meta_file():
    missing = [
        path.relative_to(PACKAGE)
        for path in sorted(PACKAGE.rglob("*.cs"))
        if not path.with_suffix(".cs.meta").is_file()
    ]
    assert not missing, f"scripts without .meta files: {missing}"


def test_no_obsolete_instance_id_apis():
    """Unity 6.3 made these obsolete-as-error and rules out int round-tripping.

    Identity must stay behind the host-issued session handle, so a reintroduced
    GetInstanceID would both break the 6000.6 build and leak a numeric id onto
    the wire.
    """
    offenders = []
    for script in sorted(EDITOR.rglob("*.cs")):
        text = script.read_text(encoding="utf-8-sig")
        for needle in ("GetInstanceID(", "InstanceIDToObject("):
            if needle in text:
                offenders.append(f"{script.relative_to(PACKAGE)}: {needle}")
    assert not offenders, f"obsolete instance-id APIs reintroduced: {offenders}"


def test_test_assembly_is_wired_for_the_editor_test_runner():
    tests = [path for path in _assembly_definitions() if "Tests" in path.parts]
    if not tests:
        pytest.skip("package ships no test assembly")
    for path in tests:
        data = _load_json(path)
        references = set(data.get("references", []))
        assert "Kodaxa.RoboVision.Editor" in references, f"{path.name} does not reference the host assembly"
        assert {"UnityEngine.TestRunner", "UnityEditor.TestRunner"} <= references, (
            f"{path.name} must reference both test runner assemblies"
        )
        assert "nunit.framework.dll" in data.get("precompiledReferences", []), (
            f"{path.name} must list nunit.framework.dll as a precompiled reference"
        )
        assert data.get("defineConstraints") == ["UNITY_INCLUDE_TESTS"], (
            f"{path.name} must be constrained to UNITY_INCLUDE_TESTS so it is excluded from normal builds"
        )
