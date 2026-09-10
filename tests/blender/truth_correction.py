"""The correction evaluation contract, run inside real transactions.

This closes DAT v0. The evaluator does not decide what artistic correction to
attempt — that stays the frontier model's job — but it decides whether the
attempt earned its commit, and that decision has to be arithmetic rather than
confidence, because it is the thing that gets committed.

Every branch below runs the whole shape: Q0 measured, transaction opened,
candidate applied through typed RoboVision operations, Q1 measured, evaluator
consulted, then commit or verified rollback. The rejected branches are the point.
A rejection that merely reported a rejection would be worthless; each one here
rolls back and proves the scene hashes to the begin fingerprint afterwards.

**Interactive Blender.** Background Blender's `ed.undo` reports FINISHED and
restores nothing, so a rejected branch proved there would be theatre — the scene
would still contain the bad candidate and the gate would pass. `system.health`
reports exactly this by refusing to call `begin_correction` available in
background, and this gate asserts the opposite condition before it starts.
"""
from __future__ import annotations

import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import Host, artifact_dir, clean_scene, expect, run_gate  # noqa: E402
from truth_support import cube, metrics  # noqa: E402

FINDINGS: dict[str, object] = {}
WORK = Path(bpy.app.tempdir) / "robovision-correction"

TARGET = "reference.macro.excess_fraction"


def reference_for(rv: Host, name: str, view: str, path: Path, size: int = 96) -> dict:
    """Write the current silhouette as a reference, and keep the frame with it."""
    from mathutils.bvhtree import BVHTree

    from robovision_blender.truth import mask as masks
    from robovision_blender.truth import reference as reference_module
    from robovision_blender.truth import views as canonical

    points, indices, corners = reference_module._triangles([bpy.data.objects[name]])
    frame = canonical.subject_frame(corners)
    cameras = canonical.camera_set(frame, level=1, projection="orthographic",
                                   width=size, height=size)
    camera = next(entry for entry in cameras if entry["view"] == view)
    silhouette = masks.silhouette(BVHTree.FromPolygons(points, indices, all_triangles=True),
                                  camera, frame)
    image = bpy.data.images.new(f"ref-{view}", width=size, height=size, alpha=True)
    try:
        pixels = [0.0] * (size * size * 4)
        for y in range(size):
            source = (size - 1 - y) * size
            for x in range(size):
                on = 1.0 if silhouette[source + x] else 0.0
                pixels[(y * size + x) * 4:(y * size + x) * 4 + 4] = [on, on, on, on]
        image.pixels = pixels
        image.filepath_raw = str(path)
        image.file_format = "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)
    del rv
    return {"center": frame["center"], "radius": frame["radius"]}


def axis_view(rv: Host, name: str, axis: int, sign: float) -> str:
    contract = rv.result("truth.views", {"object": name, "level": 1})
    return max(contract["cameras"],
               key=lambda camera: camera["direction"][axis] * sign)["view"]


class Bench:
    """A too-wide box, its front and side references, and a way to measure it."""

    def __init__(self, rv: Host) -> None:
        self.rv = rv
        WORK.mkdir(parents=True, exist_ok=True)
        clean_scene()
        cube(rv, "Part", size=2.0)
        cube(rv, "Neighbour", size=1.0)
        bpy.data.objects["Neighbour"].location = (5.0, 0.0, 0.0)
        bpy.context.view_layer.update()
        rv.runtime.mark_dirty()

        self.front = axis_view(rv, "Part", 1, -1.0)
        self.side = axis_view(rv, "Part", 0, 1.0)
        self.front_path = WORK / "front.png"
        self.side_path = WORK / "side.png"
        self.front_frame = reference_for(rv, "Part", self.front, self.front_path)
        self.side_frame = reference_for(rv, "Part", self.side, self.side_path)

        # The defect the loop exists to correct: 60% too wide in the front view.
        bpy.data.objects["Part"].scale = (1.6, 1.0, 1.0)
        bpy.context.view_layer.update()
        rv.runtime.mark_dirty()

    def measure(self) -> dict:
        """Q0 or Q1: geometry, plus one reference certificate per view.

        Keyed by view, which is why the evaluator has to be told which
        certificate a metric belongs to. Both reference certificates publish
        `reference.macro.excess_fraction`, and improving the front while
        protecting the side is precisely the policy that needs them apart.
        """
        return {
            "geometry": self.rv.result("truth.geometry", {"object": "Part"}),
            "front": self.rv.result("truth.reference",
                                    {"object": "Part", "reference": str(self.front_path),
                                     "view": self.front, "frame": self.front_frame}),
            "side": self.rv.result("truth.reference",
                                   {"object": "Part", "reference": str(self.side_path),
                                    "view": self.side, "frame": self.side_frame}),
        }

    def scale(self, factors) -> None:
        self.rv.result("object.transform",
                       {"object": "Part", "scale": list(factors)})


def policy(**overrides) -> dict:
    base = {
        "targets": [{"metric": TARGET, "kind": "front", "epsilon": 0.1}],
        "protected": [{"metric": TARGET, "kind": "side", "tolerance": 0.02}],
        "required_invariants": ["geometry.manifold", "geometry.no_degenerate_faces",
                                "geometry.normals_consistent"],
        "advisory": ["geometry.faces"],
    }
    base.update(overrides)
    return base


def run_correction(rv: Host, bench: Bench, candidate, spec: dict,
                   locality_declaration: dict | None = None) -> dict:
    """Q0, transaction, candidate, Q1, evaluate, commit or verified rollback."""
    before_snapshot = rv.result("scene.snapshot", {"level": "deep"})
    begin_fingerprint = before_snapshot["fingerprint"]
    q0 = bench.measure()

    transaction = rv.result("transaction.begin", {"label": "candidate"})["transaction"]
    candidate()
    q1 = bench.measure()

    request = dict(spec)
    if locality_declaration is not None:
        request["locality"] = rv.result(
            "truth.locality", {"before": before_snapshot["snapshot"],
                               **locality_declaration})
        request["require_locality"] = True
    request["before"], request["after"] = q0, q1
    decision = rv.result("truth.evaluate", request)

    if decision["accepted"]:
        rv.result("transaction.commit", {"transaction": transaction})
        outcome = "committed"
    else:
        rolled = rv.result("transaction.rollback", {"transaction": transaction})
        outcome = "rolled_back"
        decision["restored_fingerprint"] = rolled["fingerprint"]

    decision["outcome"] = outcome
    decision["begin_fingerprint"] = begin_fingerprint
    decision["final_fingerprint"] = rv.fingerprint()
    return decision


def a_good_correction_is_accepted_and_committed(rv: Host) -> None:
    bench = Bench(rv)
    decision = run_correction(
        rv, bench, lambda: bench.scale((1.0, 1.0, 1.0)), policy(),
        {"targets": ["Part"], "protecteds": ["Neighbour"]})

    FINDINGS["accept_decision"] = decision["decision"]
    FINDINGS["accept_outcome"] = decision["outcome"]
    FINDINGS["accept_reject_causes"] = decision["reject_causes"]
    FINDINGS["accept_target"] = decision["targets_achieved"][0]
    FINDINGS["accept_invariants"] = decision["invariants_checked"]
    FINDINGS["accept_advisory"] = decision["advisory"]
    FINDINGS["accept_survived"] = (
        decision["final_fingerprint"] != decision["begin_fingerprint"])
    FINDINGS["accept_front_excess_after"] = round(
        metrics(bench.measure()["front"])["reference.macro.excess_fraction"], 4)


def a_change_below_epsilon_is_rejected_and_rolled_back(rv: Host) -> None:
    bench = Bench(rv)
    # A real, measurable improvement that is still not enough — the case epsilon
    # exists for, and the one an agent making cosmetic nudges actually produces.
    # The demanded improvement is raised rather than the step made smaller: a
    # 1.6-to-1.58 nudge is sub-pixel at this mask resolution and improves the
    # metric by exactly nothing, which would prove only that an unmeasurable
    # change is rejected.
    decision = run_correction(
        rv, bench, lambda: bench.scale((1.5, 1.0, 1.0)),
        policy(targets=[{"metric": TARGET, "kind": "front", "epsilon": 0.4}]))

    FINDINGS["tiny_decision"] = decision["decision"]
    FINDINGS["tiny_outcome"] = decision["outcome"]
    FINDINGS["tiny_cause"] = decision["reject_causes"][0]["cause"]
    FINDINGS["tiny_achieved"] = round(
        decision["reject_causes"][0]["achieved_improvement"], 4)
    FINDINGS["tiny_required"] = decision["reject_causes"][0]["required_improvement"]
    FINDINGS["tiny_restored"] = (
        decision["final_fingerprint"] == decision["begin_fingerprint"])
    FINDINGS["tiny_rollback_proved"] = (
        decision["restored_fingerprint"] == decision["begin_fingerprint"])


def improving_the_target_while_breaking_geometry_is_rejected(rv: Host) -> None:
    """A target improvement must never buy a broken mesh."""
    bench = Bench(rv)

    def candidate() -> None:
        # The target really is fixed: the width goes back to the reference.
        bench.scale((1.0, 1.0, 1.0))
        # And the mesh is quietly wrecked doing it. Extruding a face with no
        # translation leaves zero-area side faces behind — invisible in every
        # silhouette, fatal to the geometry. That is the shape of the failure
        # hard invariants exist to catch: a candidate whose declared objective
        # improved and whose mesh must not be kept.
        revision = rv.result("mesh.inspect", {"object": "Part"})["mesh_revision"]
        rv.result("mesh.extrude_faces",
                  {"object": "Part", "face_indices": [0],
                   "translation": [0.0, 0.0, 0.0],
                   "expected_mesh_revision": revision})

    decision = run_correction(rv, bench, candidate, policy())
    FINDINGS["broken_decision"] = decision["decision"]
    FINDINGS["broken_outcome"] = decision["outcome"]
    FINDINGS["broken_causes"] = sorted({c["cause"] for c in decision["reject_causes"]})
    FINDINGS["broken_invariants"] = sorted(
        c["invariant"] for c in decision["reject_causes"] if c["cause"] == "failed_invariant")
    FINDINGS["broken_restored"] = (
        decision["final_fingerprint"] == decision["begin_fingerprint"])


def improving_the_front_while_regressing_the_side_is_rejected(rv: Host) -> None:
    """The case a single-view metric would wave through.

    The front silhouette genuinely improves. The side view, which the front
    camera cannot see at all, gets 40% deeper. One metric name, two views, two
    opposite verdicts — which is why the policy names the certificate.
    """
    bench = Bench(rv)
    decision = run_correction(rv, bench, lambda: bench.scale((1.0, 1.4, 1.0)), policy())

    FINDINGS["side_decision"] = decision["decision"]
    FINDINGS["side_outcome"] = decision["outcome"]
    FINDINGS["side_causes"] = sorted({c["cause"] for c in decision["reject_causes"]})
    regression = next(c for c in decision["reject_causes"]
                      if c["cause"] == "protected_regression")
    FINDINGS["side_regression_kind"] = regression["kind"]
    FINDINGS["side_regression"] = round(regression["regression"], 4)
    FINDINGS["side_tolerance"] = regression["tolerance"]
    FINDINGS["side_target_still_achieved"] = bool(decision["targets_achieved"])
    FINDINGS["side_restored"] = (
        decision["final_fingerprint"] == decision["begin_fingerprint"])


def touching_a_protected_object_is_rejected(rv: Host) -> None:
    bench = Bench(rv)

    def candidate() -> None:
        bench.scale((1.0, 1.0, 1.0))
        rv.result("object.transform",
                  {"object": "Neighbour", "location": [5.0, 0.4, 0.0]})

    decision = run_correction(rv, bench, candidate, policy(),
                              {"targets": ["Part"], "protecteds": ["Neighbour"]})
    FINDINGS["locality_decision"] = decision["decision"]
    FINDINGS["locality_outcome"] = decision["outcome"]
    FINDINGS["locality_causes"] = sorted({c["cause"] for c in decision["reject_causes"]})
    FINDINGS["locality_invariants"] = sorted(
        c["invariant"] for c in decision["reject_causes"]
        if c["cause"] == "locality_violation")
    FINDINGS["locality_target_achieved"] = bool(decision["targets_achieved"])
    FINDINGS["locality_restored"] = (
        decision["final_fingerprint"] == decision["begin_fingerprint"])


def missing_evidence_is_indeterminate_and_not_acceptance(rv: Host) -> None:
    bench = Bench(rv)
    decision = run_correction(
        rv, bench, lambda: bench.scale((1.0, 1.0, 1.0)),
        policy(required_invariants=["geometry.manifold", "coverage.meets_declared_minimum"]))

    FINDINGS["missing_decision"] = decision["decision"]
    FINDINGS["missing_outcome"] = decision["outcome"]
    FINDINGS["missing_causes"] = sorted(
        {c["cause"] for c in decision["indeterminate_causes"]})
    FINDINGS["missing_invariant"] = decision["indeterminate_causes"][0]["invariant"]
    FINDINGS["missing_restored"] = (
        decision["final_fingerprint"] == decision["begin_fingerprint"])

    # An unqualified metric name that two certificates publish is ambiguous, not
    # whichever one happened to be iterated first.
    ambiguous = run_correction(
        rv, Bench(rv), lambda: None,
        policy(targets=[{"metric": TARGET, "epsilon": 0.1}], protected=[]))
    FINDINGS["ambiguous_decision"] = ambiguous["decision"]
    FINDINGS["ambiguous_cause"] = ambiguous["indeterminate_causes"][0]["cause"]


def main() -> None:
    expect(not bpy.app.background,
           "this gate proves verified rollback, which background Blender cannot do; "
           "run it in an interactive Blender (xvfb-run in CI)")

    rv = Host("truth-correction")
    health = rv.result("system.health")["ready_for"]["begin_correction"]
    FINDINGS["begin_correction_ready"] = health["status"]
    FINDINGS["verified_rollback"] = health.get("verified_rollback")

    a_good_correction_is_accepted_and_committed(rv)
    a_change_below_epsilon_is_rejected_and_rolled_back(rv)
    improving_the_target_while_breaking_geometry_is_rejected(rv)
    improving_the_front_while_regressing_the_side_is_rejected(rv)
    touching_a_protected_object_is_rejected(rv)
    missing_evidence_is_indeterminate_and_not_acceptance(rv)

    expect(FINDINGS["begin_correction_ready"] == "ready",
           f"health said a correction was not possible here: "
           f"{FINDINGS['begin_correction_ready']}")
    expect(FINDINGS["verified_rollback"] is True,
           "health did not confirm a provable rollback, so a rejected branch would "
           "prove nothing")

    expect(FINDINGS["accept_decision"] == "accept",
           f"a correction that fixed its target was not accepted: "
           f"{FINDINGS['accept_reject_causes']}")
    expect(FINDINGS["accept_outcome"] == "committed", "an accepted correction was not committed")
    expect(FINDINGS["accept_target"]["achieved_improvement"] > 0.5,
           f"the improvement was not measured: {FINDINGS['accept_target']}")
    expect(FINDINGS["accept_target"]["kind"] == "front",
           "the achieved target did not record which view it was about")
    expect(len(FINDINGS["accept_invariants"]) == 3,
           f"the declared invariants were not all checked: {FINDINGS['accept_invariants']}")
    expect(FINDINGS["accept_advisory"] and FINDINGS["accept_advisory"][0]["metric"]
           == "geometry.faces", "advisory metrics were not recorded")
    expect(FINDINGS["accept_survived"], "the committed correction did not survive")
    expect(FINDINGS["accept_front_excess_after"] < 0.02,
           f"the accepted correction did not actually fix the profile: "
           f"{FINDINGS['accept_front_excess_after']}")

    expect(FINDINGS["tiny_decision"] == "reject",
           "a change below epsilon was accepted")
    expect(FINDINGS["tiny_cause"] == "insufficient_target_improvement",
           f"the rejection was attributed wrongly: {FINDINGS['tiny_cause']}")
    expect(0.0 < FINDINGS["tiny_achieved"] < FINDINGS["tiny_required"],
           f"the rejected candidate was not a real-but-insufficient improvement: "
           f"{FINDINGS['tiny_achieved']} against {FINDINGS['tiny_required']}")
    expect(FINDINGS["tiny_outcome"] == "rolled_back", "a rejected candidate was kept")
    expect(FINDINGS["tiny_rollback_proved"],
           "the rollback did not reproduce the begin fingerprint")
    expect(FINDINGS["tiny_restored"],
           "the scene did not return to the state the correction started from")

    expect(FINDINGS["broken_decision"] == "reject",
           "a candidate that improved its target and broke the mesh was accepted")
    expect("failed_invariant" in FINDINGS["broken_causes"],
           f"the broken invariant was not the cause: {FINDINGS['broken_causes']}")
    expect(FINDINGS["broken_invariants"],
           "the rejection did not name which invariant broke")
    expect(FINDINGS["broken_restored"], "the broken candidate was not rolled back")

    expect(FINDINGS["side_decision"] == "reject",
           "improving the front while deepening the side was accepted")
    expect("protected_regression" in FINDINGS["side_causes"],
           f"the side regression was not the cause: {FINDINGS['side_causes']}")
    expect(FINDINGS["side_regression_kind"] == "side",
           f"the regression was attributed to the wrong view: "
           f"{FINDINGS['side_regression_kind']}")
    expect(FINDINGS["side_regression"] > FINDINGS["side_tolerance"],
           "the regression did not exceed the tolerance it was rejected for")
    expect(FINDINGS["side_restored"], "the regressing candidate was not rolled back")

    expect(FINDINGS["locality_decision"] == "reject",
           "a correction that moved a protected object was accepted")
    expect("locality_violation" in FINDINGS["locality_causes"],
           f"the locality violation was not the cause: {FINDINGS['locality_causes']}")
    expect("locality.protected_unchanged" in FINDINGS["locality_invariants"],
           f"the violated locality invariant was not named: "
           f"{FINDINGS['locality_invariants']}")
    expect(FINDINGS["locality_target_achieved"],
           "precondition: the target must have improved, or this is not the case "
           "locality exists to catch")
    expect(FINDINGS["locality_restored"], "the out-of-scope candidate was not rolled back")

    expect(FINDINGS["missing_decision"] == "indeterminate",
           f"missing evidence produced {FINDINGS['missing_decision']} rather than "
           f"indeterminate")
    expect(FINDINGS["missing_outcome"] == "rolled_back",
           "an unproven candidate was kept")
    expect("invariant_missing" in FINDINGS["missing_causes"],
           f"the missing evidence was not named: {FINDINGS['missing_causes']}")
    expect(FINDINGS["missing_invariant"] == "coverage.meets_declared_minimum",
           "the missing invariant was not identified")
    expect(FINDINGS["missing_restored"], "the unproven candidate was not rolled back")

    expect(FINDINGS["ambiguous_decision"] == "indeterminate",
           "an ambiguous metric name was resolved by guessing")
    expect(FINDINGS["ambiguous_cause"] == "target_metric_ambiguous",
           f"the ambiguity was not reported: {FINDINGS['ambiguous_cause']}")

    (artifact_dir("blender-truth-correction") / "findings.txt").write_text(
        "\n".join(f"{key}={value}" for key, value in sorted(FINDINGS.items())) + "\n",
        encoding="utf-8")


run_gate("BLENDER_TRUTH_CORRECTION", main, "blender-truth-correction")
