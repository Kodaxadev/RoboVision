from __future__ import annotations

from mathutils import Vector
import bpy

from ..identity import normalize_object_ids, object_id
from ..registry import NOTIFIED, HostError
from ..snapshots import DEEP, diff_snapshots, object_snapshot, resolve_level, scene_snapshot


def describe(params, runtime):
    level = resolve_level(params, default="shallow")
    repairs = normalize_object_ids()
    return {
        "scene": bpy.context.scene.name_full,
        "revision": runtime.revision,
        "bridge": runtime.bridge,
        "document_incarnation": runtime.document_incarnation,
        "document": runtime.document,
        "frame": int(bpy.context.scene.frame_current),
        "level": level,
        "objects": [
            object_snapshot(obj, level=level)
            for obj in sorted(bpy.context.scene.objects, key=lambda item: object_id(item))
        ],
        "identity_repairs": repairs,
    }


def search(params, _runtime):
    query = str(params.get("query", "")).casefold()
    requested_types = params.get("types")
    type_set = {str(value).upper() for value in requested_types} if isinstance(requested_types, list) else None
    matches = []
    for obj in bpy.context.scene.objects:
        if query and query not in obj.name_full.casefold():
            continue
        if type_set is not None and obj.type not in type_set:
            continue
        matches.append({"id": object_id(obj), "name": obj.name_full, "type": obj.type})
    return {"matches": sorted(matches, key=lambda item: item["name"].casefold())}


def snapshot(params, runtime):
    level = resolve_level(params)
    # The dispatcher reconciled authoritatively before this handler ran, so the
    # host's baseline is a deep read of the scene as it is now. Reuse it rather
    # than paying for a second one; a caller asking for a cheaper level still
    # gets the level it asked for, over a baseline that is not cheaper.
    snap = runtime.current_snapshot() if level == DEEP else scene_snapshot(level=level)
    snapshot_id = runtime.store_snapshot(snap)
    # A full authoritative read is the only thing that may restore certainty
    # after the host has admitted it lost track.
    opened_epoch = runtime.journal.authoritative_snapshot(revision=runtime.revision)
    return {
        "snapshot": snapshot_id,
        "journal": {**runtime.journal.state(), "opened_new_epoch": opened_epoch},
        **snap,
    }


def diff(params, runtime):
    snapshot_id = params.get("from_snapshot")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise HostError("INVALID_PARAMS", "from_snapshot is required")
    before = runtime.get_snapshot(snapshot_id)
    # Compare like with like: a shallow "after" against a deep "before" would
    # report equality it cannot actually support.
    after = scene_snapshot(level=resolve_level(params, default=before.get("level", "deep")))
    return {"from_snapshot": snapshot_id, **diff_snapshots(before, after)}


def changes_since(params, runtime):
    """Cheap incremental history, with its limits reported rather than hidden.

    Called without a cursor this is a bootstrap: it reports where the journal is
    and returns no events. Every other call must present a cursor this host
    issued, which names the document and the certainty epoch it belongs to.
    """
    for legacy in ("after", "epoch"):
        if legacy in params:
            raise HostError(
                "INVALID_PARAMS",
                "changes_since takes a cursor; a bare sequence number cannot say which "
                "document incarnation or certainty epoch it came from, and both restart "
                "at 1",
                data={"rejected_parameter": legacy, "current_cursor": runtime.journal.cursor()},
            )
    # Absent means bootstrap. Present-but-null does not: a client that computed
    # a null cursor has lost its position, and answering "nothing changed" would
    # be the exact failure this call exists to avoid.
    if "cursor" in params:
        result = runtime.journal.changes_since(params["cursor"])
    else:
        result = runtime.journal.bootstrap()
    result["revision"] = runtime.revision
    result["bridge"] = runtime.bridge
    return result


def raycast(params, _runtime):
    origin = params.get("origin")
    direction = params.get("direction")
    distance = float(params.get("distance", 1.0e9))
    if not (isinstance(origin, list) and len(origin) == 3 and isinstance(direction, list) and len(direction) == 3):
        raise HostError("INVALID_PARAMS", "origin and direction must be three-number arrays")
    direction_v = Vector(tuple(float(v) for v in direction))
    if direction_v.length_squared == 0:
        raise HostError("INVALID_PARAMS", "direction cannot be zero")
    direction_v.normalize()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    hit, location, normal, face_index, obj, _matrix = bpy.context.scene.ray_cast(
        depsgraph,
        Vector(tuple(float(v) for v in origin)),
        direction_v,
        distance=distance,
    )
    return {
        "hit": bool(hit),
        "location": list(location) if hit else None,
        "normal": list(normal) if hit else None,
        "face_index": int(face_index) if hit else None,
        "object": {"id": object_id(obj), "name": obj.name_full} if hit and obj else None,
    }


def register(registry) -> None:
    registry.add("scene.describe", describe, stability="beta")
    registry.add("scene.search", search, stability="beta")
    registry.add("scene.snapshot", snapshot, stability="beta")
    registry.add("scene.diff", diff, stability="beta")
    # Polling the journal must not cost a deep read, and must not be the thing
    # that discovers a change: it reports the host's position, it does not
    # establish it.
    registry.add("scene.changes_since", changes_since, reads=NOTIFIED, stability="alpha")
    registry.add("scene.raycast", raycast, stability="alpha")
