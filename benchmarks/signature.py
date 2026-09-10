"""A normalized signature for the authored asset a benchmark run starts from.

"Byte-identical A0" was the wrong claim and is retired. Blender assigns fresh
RoboVision identities when objects are created, so replaying the same operation
list into two fresh scenes produces two scenes whose control-plane state differs
in every UUID, every revision and every journal position. They are not identical
and never could be.

What the benchmark actually requires is weaker and sufficient: that every
participant starts from the same *authored geometry*. This computes a
**normalized-authoring signature** — a hash over exactly the authored state, with
the run-specific identity deliberately excluded.

Included, because a difference in any of these is a materially different
challenge:

- object names and parent names (the hierarchy as authored, by name);
- the full world transform of each object;
- local bounding extent and world dimensions;
- mesh topology counts — vertices, edges, faces;
- material slot assignments and modifier stacks, where present.

Excluded, because these differ between two correct restores of the same asset:

- RoboVision object UUIDs — and parents are recorded by *name* for the same
  reason;
- world and bridge incarnation;
- scene revision;
- journal identity and cursors;
- transaction and idempotency metadata.

Numbers are rounded before hashing. Floating-point noise below the rounding
threshold is not a difference in the asset, and a signature that changed on the
last bit of a float would refuse every run for a reason that is not about
geometry.

For a v2-shaped asset — transformed primitives with no modifiers or materials —
this is the smallest representation that still detects a materially different
restore, which is why it is not extended with state the asset does not have.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

# Six decimal places on metres is a micrometre. Two restores of the same asset
# agree far inside that; two different assets do not differ inside it.
PLACES = 6

SIGNATURE_SCHEMA = 1


def _round(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), PLACES)
    if isinstance(value, list):
        return [_round(item) for item in value]
    return value


def _result(response: dict[str, Any]) -> dict[str, Any]:
    return response.get("result") or {}


def collect(session) -> dict[str, Any]:
    """The authored state, read over the public path, identity stripped."""
    described = _result(session.call("scene.describe", {"level": "deep"}))
    names = {obj["id"]: obj["name"] for obj in described["objects"]}
    subjects = sorted(names.values())

    geometry = _result(session.call("truth.geometry", {"objects": subjects}))
    spatial = _result(session.call("truth.spatial", {"objects": subjects}))
    counts = {entry["name"]: entry["counts"]
              for entry in geometry["subjects"] if "counts" in entry}
    placement = {entry["name"]: entry for entry in spatial["subjects"]
                 if entry.get("object") != "__combined__"}

    objects = []
    for obj in sorted(described["objects"], key=lambda item: item["name"]):
        entry = placement.get(obj["name"], {})
        objects.append({
            "name": obj["name"],
            "type": obj["type"],
            # By name, never by id: the id is exactly the part that legitimately
            # differs between two correct restores.
            "parent": names.get(obj["parent"]) if obj.get("parent") else None,
            "matrix_world": _round(obj["matrix_world"]),
            "local_extent": _round((entry.get("local_bounds") or {}).get("size")),
            "world_extent": _round((entry.get("world_bounds") or {}).get("size")),
            "counts": counts.get(obj["name"]),
            "materials": [slot.get("material") for slot in obj.get("material_slots", [])],
            "modifiers": [modifier.get("type") for modifier in obj.get("modifiers", [])]
            if isinstance(obj.get("modifiers"), list) else [],
        })

    combined = next((entry for entry in spatial["subjects"]
                     if entry.get("object") == "__combined__"), {})
    return {
        "schema": SIGNATURE_SCHEMA,
        "objects": objects,
        "combined_extent": _round((combined.get("world_bounds") or {}).get("size")),
        "excludes": ["object uuids", "world incarnation", "bridge incarnation",
                     "scene revision", "journal identity", "transaction metadata",
                     "idempotency metadata"],
    }


def signature(session) -> str:
    """A content hash of the authored state. Same asset, same value."""
    body = collect(session)
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return "rvsig:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def differences(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    """Where two collected signatures disagree, in words a human can act on."""
    found: list[str] = []
    by_name = {entry["name"]: entry for entry in right["objects"]}
    for entry in left["objects"]:
        other = by_name.pop(entry["name"], None)
        if other is None:
            found.append(f"missing object: {entry['name']}")
            continue
        for field in ("parent", "matrix_world", "local_extent", "world_extent",
                      "counts", "materials", "modifiers"):
            if entry.get(field) != other.get(field):
                found.append(f"{entry['name']}.{field} differs")
    found.extend(f"unexpected object: {name}" for name in sorted(by_name))
    if left.get("combined_extent") != right.get("combined_extent"):
        found.append("combined extent differs")
    return found
