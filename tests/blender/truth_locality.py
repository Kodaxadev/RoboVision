"""Edit locality: what a correction was allowed to touch against what it touched.

Iterative polish is only safe if a correction's blast radius is declared before
it runs and checked afterwards. Without that, "the silhouette improved" is
perfectly compatible with "and an unrelated panel moved four centimetres", and a
loop optimising the first would keep doing the second for as long as it was
rewarded.

Each fixture below performs a real edit and then asks whether the declaration
covered it. The interesting ones are not the violations but the near misses: a
helper object created and left behind, a dependency that was genuinely needed and
genuinely declared, a protected object deleted rather than modified. Those are
the shapes a correction actually takes when it goes slightly wrong.

Headless: this is snapshot diffing and needs no undo stack. The accept/reject
branches that do need one are proved separately, in an interactive Blender.
"""
from __future__ import annotations

import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import Host, artifact_dir, clean_scene, expect, run_gate  # noqa: E402
from truth_support import cube, metrics  # noqa: E402

FINDINGS: dict[str, object] = {}


def scene(rv: Host) -> str:
    """Three objects and a recorded starting point."""
    clean_scene()
    cube(rv, "Target", size=1.0)
    cube(rv, "Bystander", size=1.0)
    cube(rv, "Helper", size=1.0)
    bpy.data.objects["Bystander"].location = (3.0, 0.0, 0.0)
    bpy.data.objects["Helper"].location = (6.0, 0.0, 0.0)
    bpy.context.view_layer.update()
    rv.runtime.mark_dirty()
    return rv.result("scene.snapshot", {"level": "deep"})["snapshot"]


def move(name: str, offset: float, rv: Host) -> None:
    obj = bpy.data.objects[name]
    obj.location = (obj.location.x, obj.location.y + offset, obj.location.z)
    bpy.context.view_layer.update()
    rv.runtime.mark_dirty()


def failing(certificate: dict) -> list[str]:
    return sorted(name for name, entry in certificate["invariants"].items()
                  if not entry["holds"])


def observed(certificate: dict) -> dict:
    return next(s for s in certificate["subjects"] if s["object"] == "__observed__")


def changing_only_the_target_passes(rv: Host) -> None:
    before = scene(rv)
    move("Target", 0.25, rv)

    certificate = rv.result("truth.locality",
                            {"before": before, "targets": ["Target"],
                             "protecteds": ["Bystander", "Helper"]})
    values = metrics(certificate)
    FINDINGS["target_only_failing"] = failing(certificate)
    FINDINGS["target_only_changed"] = values["locality.changed_objects"]
    FINDINGS["target_only_target_changes"] = values["locality.target_changes"]
    FINDINGS["target_only_roles"] = sorted(
        entry["role"] for entry in observed(certificate)["changed"])
    FINDINGS["target_only_limits"] = sorted(certificate["limits"])
    FINDINGS["target_only_mesh_revision_reported"] = (
        "mesh_revision_after" in observed(certificate)["changed"][0])


def an_unchanged_scene_is_not_a_violation(rv: Host) -> None:
    """A correction that did nothing is not a correction that went out of scope."""
    before = scene(rv)
    certificate = rv.result("truth.locality",
                            {"before": before, "targets": ["Target"],
                             "protecteds": ["Bystander"]})
    FINDINGS["noop_failing"] = failing(certificate)
    FINDINGS["noop_changed"] = metrics(certificate)["locality.changed_objects"]
    FINDINGS["noop_unchanged_flag"] = certificate["pins"]["locality"]["unchanged"]


def touching_a_protected_object_fails(rv: Host) -> None:
    before = scene(rv)
    move("Target", 0.25, rv)
    move("Bystander", 0.25, rv)

    certificate = rv.result("truth.locality",
                            {"before": before, "targets": ["Target"],
                             "protecteds": ["Bystander"]})
    values = metrics(certificate)
    FINDINGS["protected_failing"] = failing(certificate)
    FINDINGS["protected_changes"] = values["locality.protected_changes"]
    FINDINGS["protected_evidence"] = \
        certificate["invariants"]["locality.protected_unchanged"]["evidence"]
    # The target moved too, and that is fine: the point is that the two are
    # reported separately rather than as one "something changed".
    FINDINGS["protected_target_changes"] = values["locality.target_changes"]


def deleting_a_protected_object_fails_too(rv: Host) -> None:
    """Protecting an object means it survives, not merely that it is not edited."""
    before = scene(rv)
    move("Target", 0.25, rv)
    bpy.data.objects.remove(bpy.data.objects["Bystander"], do_unlink=True)
    bpy.context.view_layer.update()
    rv.runtime.mark_dirty()

    certificate = rv.result("truth.locality",
                            {"before": before, "targets": ["Target"],
                             "protecteds": ["Bystander"]})
    FINDINGS["deleted_failing"] = failing(certificate)
    FINDINGS["deleted_protected_changes"] = \
        metrics(certificate)["locality.protected_changes"]
    FINDINGS["deleted_evidence_names"] = \
        certificate["invariants"]["locality.protected_unchanged"]["evidence"]["deleted"]


def an_undeclared_object_left_behind_fails(rv: Host) -> None:
    """The classic quiet failure: it worked, and the file now has a stray cube."""
    before = scene(rv)
    move("Target", 0.25, rv)
    cube(rv, "ScaffoldLeftBehind", size=0.2)

    certificate = rv.result("truth.locality",
                            {"before": before, "targets": ["Target"],
                             "protecteds": ["Bystander"]})
    values = metrics(certificate)
    FINDINGS["stray_failing"] = failing(certificate)
    FINDINGS["stray_created"] = values["locality.undeclared_created"]
    FINDINGS["stray_names"] = \
        certificate["invariants"]["locality.no_undeclared_objects"]["evidence"]["created"]
    FINDINGS["stray_protected_intact"] = values["locality.protected_changes"]


def a_declared_dependency_passes(rv: Host) -> None:
    """A correction may need to touch something else — if it said so first."""
    before = scene(rv)
    move("Target", 0.25, rv)
    move("Helper", 0.25, rv)

    undeclared = rv.result("truth.locality",
                           {"before": before, "targets": ["Target"],
                            "protecteds": ["Bystander"]})
    FINDINGS["dependency_undeclared_failing"] = failing(undeclared)
    FINDINGS["dependency_undeclared_count"] = \
        metrics(undeclared)["locality.undeclared_changes"]

    declared = rv.result("truth.locality",
                         {"before": before, "targets": ["Target"],
                          "alloweds": ["Helper"], "protecteds": ["Bystander"]})
    FINDINGS["dependency_declared_failing"] = failing(declared)
    FINDINGS["dependency_declared_roles"] = sorted(
        entry["role"] for entry in observed(declared)["changed"])
    # The same edit, judged differently only because it was declared. Nothing
    # about the geometry changed between the two measurements.
    FINDINGS["dependency_same_change_count"] = (
        metrics(declared)["locality.changed_objects"]
        == metrics(undeclared)["locality.changed_objects"])


def a_contradictory_declaration_is_refused(rv: Host) -> None:
    before = scene(rv)
    both = rv.call("truth.locality",
                   {"before": before, "targets": ["Target"], "protecteds": ["Target"]},
                   ok=False, code="INVALID_PARAMS")
    FINDINGS["contradiction_message"] = both["error"]["message"]
    FINDINGS["contradiction_objects"] = both["error"]["data"]["objects"]

    bare = rv.call("truth.locality", {"before": before}, ok=False, code="INVALID_PARAMS")
    FINDINGS["no_targets_refused"] = "targets is required" in bare["error"]["message"]

    missing = rv.call("truth.locality", {"before": "snap:nope", "targets": ["Target"]},
                      ok=False)
    FINDINGS["unknown_snapshot_code"] = missing["error"]["code"]


def main() -> None:
    rv = Host("truth-locality")
    changing_only_the_target_passes(rv)
    an_unchanged_scene_is_not_a_violation(rv)
    touching_a_protected_object_fails(rv)
    deleting_a_protected_object_fails_too(rv)
    an_undeclared_object_left_behind_fails(rv)
    a_declared_dependency_passes(rv)
    a_contradictory_declaration_is_refused(rv)

    expect(FINDINGS["target_only_failing"] == [],
           f"a correction inside its declared scope failed: "
           f"{FINDINGS['target_only_failing']}")
    expect(FINDINGS["target_only_changed"] == 1,
           f"more than the target was reported as changed: "
           f"{FINDINGS['target_only_changed']}")
    expect(FINDINGS["target_only_target_changes"] == 1, "the target change was not counted")
    expect(FINDINGS["target_only_roles"] == ["target"],
           f"the changed object was not classified as the target: "
           f"{FINDINGS['target_only_roles']}")
    expect(FINDINGS["target_only_mesh_revision_reported"],
           "the strongest available granularity was not reported")
    expect("locality.element_granularity" in FINDINGS["target_only_limits"],
           f"locality did not declare that it is object-level: "
           f"{FINDINGS['target_only_limits']}")

    expect(FINDINGS["noop_failing"] == [], "a scene nobody touched failed locality")
    expect(FINDINGS["noop_changed"] == 0, "an untouched scene reported changes")
    expect(FINDINGS["noop_unchanged_flag"] is True,
           "an untouched scene was not recorded as unchanged")

    expect("locality.protected_unchanged" in FINDINGS["protected_failing"],
           f"touching a protected object passed: {FINDINGS['protected_failing']}")
    expect(FINDINGS["protected_changes"] == 1, "the protected change was not counted")
    expect(FINDINGS["protected_target_changes"] == 1,
           "the legitimate target change was folded into the violation")
    expect(FINDINGS["protected_evidence"]["changed"],
           "the violation did not name the object it was about")

    expect("locality.protected_unchanged" in FINDINGS["deleted_failing"],
           f"deleting a protected object was not a protected change: "
           f"{FINDINGS['deleted_failing']}")
    expect(FINDINGS["deleted_protected_changes"] == 1,
           "a deleted protected object was not counted as a protected change")
    expect(FINDINGS["deleted_evidence_names"],
           "the deletion did not name what was deleted")

    expect("locality.no_undeclared_objects" in FINDINGS["stray_failing"],
           f"a helper object left behind passed: {FINDINGS['stray_failing']}")
    expect(FINDINGS["stray_created"] == 1, "the stray object was not counted")
    expect("ScaffoldLeftBehind" in str(FINDINGS["stray_names"]),
           f"the stray object was not named: {FINDINGS['stray_names']}")
    expect(FINDINGS["stray_protected_intact"] == 0,
           "creating a stray object was also reported as touching a protected one")

    expect("locality.no_undeclared_changes" in FINDINGS["dependency_undeclared_failing"],
           f"an undeclared dependency passed: {FINDINGS['dependency_undeclared_failing']}")
    expect(FINDINGS["dependency_undeclared_count"] == 1,
           "the undeclared change was not counted")
    expect(FINDINGS["dependency_declared_failing"] == [],
           f"a declared dependency still failed: "
           f"{FINDINGS['dependency_declared_failing']}")
    expect(FINDINGS["dependency_declared_roles"] == ["allowed", "target"],
           f"the declared dependency was not classified: "
           f"{FINDINGS['dependency_declared_roles']}")
    expect(FINDINGS["dependency_same_change_count"],
           "declaring a dependency changed what was measured rather than how it "
           "was judged")

    expect("both a target and protected" in FINDINGS["contradiction_message"],
           f"a contradictory declaration was accepted: "
           f"{FINDINGS['contradiction_message']}")
    expect(FINDINGS["contradiction_objects"] == ["Target"],
           "the contradiction did not name the object")
    expect(FINDINGS["no_targets_refused"],
           "a correction with no declared blast radius was checked anyway")
    expect(FINDINGS["unknown_snapshot_code"] in ("NOT_FOUND", "STALE_WORLD"),
           f"an unknown snapshot handle was not refused: "
           f"{FINDINGS['unknown_snapshot_code']}")

    (artifact_dir("blender-truth-locality") / "findings.txt").write_text(
        "\n".join(f"{key}={value}" for key, value in sorted(FINDINGS.items())) + "\n",
        encoding="utf-8")


run_gate("BLENDER_TRUTH_LOCALITY", main, "blender-truth-locality")
