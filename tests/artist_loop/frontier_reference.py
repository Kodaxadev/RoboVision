"""Build a blockout proxy to the brief, capture its profiles, then delete it.

The Phase B asset is a hard-surface sci-fi prop the reasoning client builds
itself. That only tests anything if the references it is corrected against are
not the client's own output — a model matched against a silhouette it generated
would score perfectly and prove nothing.

So the target profiles come from a blockout: the coarse massing an art director
would hand a modeller. Its overall envelope is declared in the brief, because
"explicit target dimensions" is part of the brief and hiding them would just make
the task guesswork. What is *not* declared is how that envelope divides — how
tall the body is against the manifold, how far the flange protrudes, where the
feet sit. Those are drawn from ranges the brief states and never printed, which
is what makes the client's first candidate a genuine attempt rather than a copy.

The proxy is deleted once captured. Only its silhouettes survive, which is
exactly what a real reference is: an image that constrains an outline and says
nothing about what is behind it.
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

ARTIFACTS = ROOT / "artifacts" / "artist-loop" / "frontier"

# Declared in the brief, and therefore known to the client.
ENVELOPE = {"width": 0.44, "depth": 0.34, "height": 0.64}
FIN_COUNT = 6
FIN_PITCH = 0.052


def result(response):
    return response.get("result") or {}


def create(session, name, scale, location):
    session.call("object.create", {"kind": "cube", "name": name, "size": 1.0})
    session.call("object.transform", {"object": name, "scale": list(scale),
                                      "location": list(location)})


def build_proxy(session, rng: random.Random) -> None:
    """Coarse massing inside the declared envelope, proportions drawn blind."""
    width, depth, height = ENVELOPE["width"], ENVELOPE["depth"], ENVELOPE["height"]

    body_h = height * rng.uniform(0.52, 0.64)
    body_w = width * rng.uniform(0.78, 0.94)
    body_z = -height / 2 + body_h / 2 + height * 0.06

    manifold_h = height * rng.uniform(0.16, 0.24)
    manifold_w = body_w * rng.uniform(0.55, 0.78)
    manifold_x = body_w * rng.uniform(-0.16, 0.16)
    manifold_z = body_z + body_h / 2 + manifold_h / 2

    flange_w = width * rng.uniform(0.16, 0.26)
    flange_h = height * rng.uniform(0.14, 0.22)
    flange_z = body_z + body_h * rng.uniform(-0.10, 0.18)

    foot_h = height * 0.06
    foot_w = width * rng.uniform(0.14, 0.22)
    foot_x = body_w / 2 * rng.uniform(0.62, 0.86)

    create(session, "P_Body", (body_w, depth * rng.uniform(0.84, 1.0), body_h),
           (0.0, 0.0, body_z))
    create(session, "P_Manifold", (manifold_w, depth * rng.uniform(0.6, 0.82), manifold_h),
           (manifold_x, 0.0, manifold_z))
    create(session, "P_Flange", (flange_w, depth * rng.uniform(0.4, 0.6), flange_h),
           (body_w / 2 + flange_w / 2, 0.0, flange_z))
    for sign, label in ((-1.0, "L"), (1.0, "R")):
        create(session, f"P_Foot_{label}", (foot_w, depth * 0.9, foot_h),
               (sign * foot_x, 0.0, -height / 2 + foot_h / 2))


def capture(session, names) -> list[dict]:
    views = result(session.call("truth.views", {"objects": names, "level": 1}))
    cameras = views["cameras"]

    def axis_view(axis: int, sign: float) -> str:
        return max(cameras, key=lambda c: c["direction"][axis] * sign)["view"]

    references = []
    for key, axis, sign in (("front", 1, -1.0), ("side", 0, 1.0)):
        view = axis_view(axis, sign)
        produced = result(session.call("truth.silhouette", {
            "objects": names, "view": view,
            "path": str(ARTIFACTS / f"reference-{key}.png"),
            "width": 256, "height": 256}))
        references.append({
            "key": key, "path": produced["artifact"]["path"], "view": view,
            "frame": {"center": produced["frame"]["center"],
                      "radius": produced["frame"]["radius"]},
            "content": produced["content"],
            # Deliberately NOT the proxy's own subject ids: the client's asset is
            # made of different objects, and a subject-set check against the
            # proxy's parts would be noise rather than evidence.
            "occupied_fraction": produced["occupied_fraction"],
        })
    return references


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9877
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else int(time.time())
    rng = random.Random(seed)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    session = HostSession("127.0.0.1", port)
    try:
        described = result(session.call("scene.describe"))
        for obj in described["objects"]:
            session.call("object.delete", {"object": obj["name"]})
        build_proxy(session, rng)
        names = sorted(obj["name"] for obj in
                       result(session.call("scene.describe"))["objects"])
        references = capture(session, names)
        for name in names:
            session.call("object.delete", {"object": name})

        brief = {
            "name": "frontier coolant pump",
            "subjects": [],
            "references": references,
            "patterns": [{
                "key": "fins",
                "params": {"members_prefix": "Fin_", "kind": "linear", "count": FIN_COUNT,
                           "axis": "z", "spacing": FIN_PITCH,
                           "spacing_tolerance": 0.0015,
                           "orientation_tolerance": 0.5,
                           "dimension_variance": 0.02},
            }],
            "protected_objects": [],
            "required_invariants": ["geometry.manifold", "geometry.no_degenerate_faces",
                                    "geometry.normals_consistent",
                                    "geometry.no_unintended_open_boundaries",
                                    "geometry.no_self_intersection"],
            "max_dimension": 0.70,
            "envelope": ENVELOPE,
            "requirements": [
                f"overall envelope approximately {ENVELOPE['width']} x "
                f"{ENVELOPE['depth']} x {ENVELOPE['height']} metres (w x d x h)",
                f"a vertical cooling stack of exactly {FIN_COUNT} fins at "
                f"{FIN_PITCH} m pitch along +Z, evenly spaced and identical",
                "a mirror-symmetric pair of mounting feet at the base",
                "one asymmetric secondary mass on top (a manifold)",
                "one protruding interface on +X (an outlet flange)",
                "closed, manifold, consistently wound geometry with no "
                "self-intersection between parts",
            ],
            "notes": "The references are the projected geometric occupancy of a "
                     "blockout massing built to this envelope. They constrain the "
                     "front and side outlines and nothing behind them. They carry no "
                     "shading, materials or lighting. The blockout's internal "
                     "proportions are not stated anywhere.",
        }
        (ARTIFACTS / "brief.json").write_text(json.dumps(brief, indent=2), encoding="utf-8")
        (ARTIFACTS / "SEALED-proxy-seed.json").write_text(
            json.dumps({"seed": seed}, indent=2), encoding="utf-8")
        print(f"FRONTIER_REFERENCE_READY seed={seed}")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
