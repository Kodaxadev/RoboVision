"""Build the calibration asset, capture its references, then break it blindly.

This fixture is not the quality benchmark. It exists to establish that the loop
wiring works end to end against a correction problem whose answer is objectively
measurable: Q0, diagnose, correct, Q1, evaluate, commit or roll back.

Two properties matter more than the geometry.

**Everything goes through the public path.** The asset is assembled with typed
RoboVision operations over a socket, exactly as a frontier model would assemble
it. Nothing here touches `bpy`. If the fixture needed private access to be built,
the loop it is calibrating could not be driven by an external client either.

**The defects are blind.** Which defects are applied, to which fin, and by how
much, are drawn from a seed and never printed. The author of the loop is also its
first reasoning client, so the only way the experiment means anything is if the
specific faults reach that client through the discrepancy packet like any other
evidence. The seed is recorded for reproducibility in a file the run does not
read back.

The kinds of defect are public — the brief names them — and that is fine. Knowing
that a width may be wrong is not knowing whether it is, which one, or by how much.
"""
from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from robovision.session import HostSession  # noqa: E402

ARTIFACTS = ROOT / "artifacts" / "artist-loop" / "calibration"
FIN_COUNT = 8
FIN_SPACING = 0.065
FIN_START = -0.2275


def result(response):
    return response.get("result") or {}


def create(session, name, scale, location):
    session.call("object.create", {"kind": "cube", "name": name, "size": 1.0})
    session.call("object.transform", {"object": name, "scale": list(scale),
                                      "location": list(location)})


def build_correct(session) -> None:
    """The asset as it is meant to be. References are captured from this."""
    create(session, "Chassis", (0.62, 0.34, 0.24), (0.0, 0.0, 0.0))
    create(session, "Housing", (0.17, 0.17, 0.13), (0.26, 0.0, 0.30))
    for index in range(FIN_COUNT):
        create(session, f"Fin_{index}",
               (0.012, 0.15, 0.09),
               (FIN_START + index * FIN_SPACING, 0.0, 0.29))
    # Off to one side and irrelevant to the silhouette: it exists purely so a
    # correction has something it must demonstrably not touch.
    create(session, "Anchor", (0.07, 0.07, 0.07), (0.0, 0.62, 0.0))


def subjects(session) -> list[str]:
    described = result(session.call("scene.describe"))
    return sorted(obj["name"] for obj in described["objects"])


def capture_references(session, names) -> list[dict]:
    """Front and side profiles of the correct asset, with the frame that made them.

    The frame travels with each reference. Without it a later comparison would
    re-derive framing from whatever the asset had become, and a change in overall
    size would be silently absorbed by the camera backing off to fit it.
    """
    views = result(session.call("truth.views", {"objects": names, "level": 1}))
    cameras = views["cameras"]

    def axis_view(axis: int, sign: float) -> str:
        return max(cameras, key=lambda c: c["direction"][axis] * sign)["view"]

    references = []
    for key, axis, sign in (("front", 1, -1.0), ("side", 0, 1.0)):
        view = axis_view(axis, sign)
        path = ARTIFACTS / f"reference-{key}.png"
        produced = result(session.call("truth.silhouette", {
            "objects": names, "view": view, "path": str(path),
            "width": 256, "height": 256}))
        references.append({
            "key": key, "path": produced["artifact"]["path"], "view": view,
            "frame": {"center": produced["frame"]["center"],
                      "radius": produced["frame"]["radius"]},
            "content": produced["content"],
            "subjects": produced["subjects"],
            "occupied_fraction": produced["occupied_fraction"],
        })
    return references


def apply_blind_defects(session, rng: random.Random) -> dict:
    """Break the asset in ways the brief admits are possible, chosen blindly."""
    applied = {}
    if rng.random() < 0.8:
        factor = round(rng.uniform(1.18, 1.35), 4)
        session.call("object.transform",
                     {"object": "Chassis", "scale": [0.62 * factor, 0.34, 0.24]})
        applied["chassis_width_factor"] = factor
    if rng.random() < 0.8:
        offset = [round(rng.uniform(-0.06, 0.06), 4), round(rng.uniform(0.04, 0.10), 4),
                  round(rng.uniform(-0.03, 0.05), 4)]
        session.call("object.transform",
                     {"object": "Housing",
                      "location": [0.26 + offset[0], offset[1], 0.30 + offset[2]]})
        applied["housing_offset"] = offset
    if rng.random() < 0.8:
        victim = rng.randrange(1, FIN_COUNT - 1)
        session.call("object.delete", {"object": f"Fin_{victim}"})
        applied["missing_fin"] = victim
    if rng.random() < 0.8:
        remaining = [i for i in range(FIN_COUNT) if i != applied.get("missing_fin")]
        victim = rng.choice(remaining[1:-1])
        shift = round(rng.choice([-1, 1]) * rng.uniform(0.012, 0.022), 4)
        session.call("object.transform",
                     {"object": f"Fin_{victim}",
                      "location": [FIN_START + victim * FIN_SPACING + shift, 0.0, 0.29]})
        applied["misspaced_fin"] = {"fin": victim, "shift": shift}
    return applied


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9877
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else int(time.time())
    rng = random.Random(seed)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    session = HostSession("127.0.0.1", port)
    try:
        for name in subjects(session):
            session.call("object.delete", {"object": name})
        build_correct(session)
        # The reference must depict exactly the parts the measurement will cover.
        # Rendering it over every object in the scene — Anchor included — made the
        # subject read as 59% too narrow in the side view on the first run, and
        # sent a correction after a part that was not the problem. The image
        # cannot say what it contains, so the caller has to.
        measured = [name for name in subjects(session) if name != "Anchor"]
        references = capture_references(session, measured)
        applied = apply_blind_defects(session, rng)
        # The brief names the asset as intended, including any part a defect has
        # just removed. Freezing the post-defect list instead would mean a
        # restored part never rejoined the measurement it belongs to.
        intended = measured

        brief = {
            "name": "calibration regulator",
            "subjects": sorted(intended),
            "references": references,
            "patterns": [{
                "key": "fins",
                "params": {"members_prefix": "Fin_", "kind": "linear", "count": FIN_COUNT,
                           "axis": "x", "spacing": FIN_SPACING,
                           "spacing_tolerance": 0.002,
                           "orientation_tolerance": 0.5,
                           "dimension_variance": 0.02},
            }],
            "protected_objects": ["Anchor"],
            "required_invariants": ["geometry.manifold", "geometry.no_degenerate_faces",
                                    "geometry.normals_consistent",
                                    "geometry.no_unintended_open_boundaries"],
            "max_dimension": None,
            "notes": "The references are the profiles of the intended asset, captured "
                     "before any defect was applied, in the frame recorded with each. "
                     "They are projected geometric occupancy and carry no shading. "
                     "Anchor is protected and must not change.",
        }
        (ARTIFACTS / "brief.json").write_text(json.dumps(brief, indent=2), encoding="utf-8")
        # Written where the run does not read it, so the reasoning client learns
        # the faults from the discrepancy packet rather than from here.
        (ARTIFACTS / "SEALED-ground-truth.json").write_text(
            json.dumps({"seed": seed, "applied": applied}, indent=2), encoding="utf-8")
        print(f"CALIBRATION_FIXTURE_READY seed={seed} subjects={len(brief['subjects'])}")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
