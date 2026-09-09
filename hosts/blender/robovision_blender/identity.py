from __future__ import annotations

from array import array
import hashlib
import uuid
from typing import Any

import bpy

from .registry import HostError

OBJECT_ID_KEY = "_robovision_id"
MESH_REV_KEY = "_robovision_mesh_revision"
MESH_TOPOLOGY_KEY = "_robovision_topology"

# Maps a RoboVision object id to the Blender object pointer that owns it in this
# session. Copying an object in Blender copies its custom properties, so two
# objects can present the same id. The owner registry lets duplicate repair keep
# the id on the object the agent already addressed instead of handing it to
# whichever copy happens to sort first by name.
_ID_OWNERS: dict[str, int] = {}


def _derived_linked_id(obj: bpy.types.Object) -> str:
    library = obj.library.filepath if obj.library else ""
    raw = f"{library}|{obj.name_full}".encode("utf-8")
    return "b3d:linked:" + hashlib.sha256(raw).hexdigest()[:32]


def _claim(oid: str, obj: bpy.types.Object) -> str:
    _ID_OWNERS[oid] = obj.as_pointer()
    return oid


def object_id(obj: bpy.types.Object, *, create: bool = True) -> str:
    existing = obj.get(OBJECT_ID_KEY)
    if isinstance(existing, str) and existing:
        _ID_OWNERS.setdefault(existing, obj.as_pointer())
        return existing
    if not create:
        return _derived_linked_id(obj) if obj.library else ""
    if obj.library is not None:
        return _derived_linked_id(obj)
    value = "b3d:" + str(uuid.uuid4())
    try:
        obj[OBJECT_ID_KEY] = value
    except (TypeError, AttributeError, RuntimeError):
        return _derived_linked_id(obj)
    return _claim(value, obj)


def forget_identity_owners() -> None:
    """Drop the session owner registry, e.g. when a different .blend is loaded."""
    _ID_OWNERS.clear()


def resolve_object(ref: Any) -> bpy.types.Object:
    if isinstance(ref, bpy.types.Object):
        return ref
    if not isinstance(ref, str) or not ref:
        raise HostError("INVALID_PARAMS", "object must be a RoboVision id or object name")
    # IDs are authoritative. Names are accepted only as an explicit convenience fallback.
    for obj in bpy.data.objects:
        if object_id(obj) == ref:
            return obj
    obj = bpy.data.objects.get(ref)
    if obj is None:
        raise HostError("NOT_FOUND", f"object not found: {ref}")
    return obj


def normalize_object_ids() -> list[dict[str, str]]:
    """Repair copied custom IDs on local objects and report every reassignment.

    When two objects carry the same id, the object already registered as that
    id's owner keeps it. Blender copies custom properties into duplicates, so
    without the owner check a copy could silently inherit the identity an agent
    is holding and later mutations would address the wrong object.
    """
    claimants: dict[str, list[bpy.types.Object]] = {}
    for obj in sorted(bpy.data.objects, key=lambda item: item.name_full):
        if obj.library is not None:
            continue
        claimants.setdefault(object_id(obj), []).append(obj)

    repaired: list[dict[str, str]] = []
    for oid, objects in claimants.items():
        if len(objects) < 2:
            if objects:
                _ID_OWNERS.setdefault(oid, objects[0].as_pointer())
            continue
        owner_pointer = _ID_OWNERS.get(oid)
        keeper = next((obj for obj in objects if obj.as_pointer() == owner_pointer), objects[0])
        _claim(oid, keeper)
        for obj in objects:
            if obj is keeper:
                continue
            new_id = "b3d:" + str(uuid.uuid4())
            obj[OBJECT_ID_KEY] = new_id
            _claim(new_id, obj)
            repaired.append(
                {
                    "object": obj.name_full,
                    "duplicate": oid,
                    "assigned": new_id,
                    "retained_by": keeper.name_full,
                }
            )
    return repaired


def topology_signature(mesh: bpy.types.Mesh) -> str:
    """Hash the structure that gives element indices meaning.

    Vertex positions are excluded on purpose: moving a vertex does not
    invalidate an index, but adding, removing or rewiring elements does.
    Buffers are pulled with foreach_get so the cost stays proportional to a
    memcpy rather than to per-element Python attribute access.
    """
    edge_count = len(mesh.edges)
    loop_count = len(mesh.loops)
    poly_count = len(mesh.polygons)
    digest = hashlib.sha256()
    digest.update(f"v{len(mesh.vertices)}e{edge_count}l{loop_count}p{poly_count};".encode())

    if edge_count:
        edges = array("i", bytes(edge_count * 2 * 4))
        mesh.edges.foreach_get("vertices", edges)
        digest.update(edges.tobytes())
    if poly_count:
        loop_totals = array("i", bytes(poly_count * 4))
        mesh.polygons.foreach_get("loop_total", loop_totals)
        digest.update(loop_totals.tobytes())
    if loop_count:
        loop_vertices = array("i", bytes(loop_count * 4))
        mesh.loops.foreach_get("vertex_index", loop_vertices)
        digest.update(loop_vertices.tobytes())
    return digest.hexdigest()


def _store(mesh: bpy.types.Mesh, revision: int, signature: str) -> None:
    try:
        mesh[MESH_REV_KEY] = revision
        mesh[MESH_TOPOLOGY_KEY] = signature
    except (TypeError, AttributeError, RuntimeError) as exc:
        raise HostError("UNSUPPORTED", "mesh data is not writable") from exc


def _mesh_of(obj: bpy.types.Object) -> bpy.types.Mesh:
    if obj.type != "MESH" or obj.data is None:
        raise HostError("INVALID_PARAMS", "object is not a mesh")
    return obj.data


def mesh_revision(obj: bpy.types.Object) -> int:
    """Return the current topology revision, detecting out-of-band edits.

    A revision that only advanced when RoboVision mutated could not protect an
    agent from a manual Edit Mode change, another add-on or an undo. The stored
    signature is therefore re-checked on every read and the revision advances
    whenever the topology no longer matches what RoboVision last recorded.
    """
    mesh = _mesh_of(obj)
    stored = mesh.get(MESH_REV_KEY, 0)
    revision = int(stored) if isinstance(stored, (int, float)) else 0
    current = topology_signature(mesh)
    recorded = mesh.get(MESH_TOPOLOGY_KEY)
    if not isinstance(recorded, str) or not recorded:
        try:
            _store(mesh, revision, current)
        except HostError:
            pass
        return revision
    if recorded != current:
        revision += 1
        try:
            _store(mesh, revision, current)
        except HostError:
            pass
    return revision


def assert_mesh_revision(obj: bpy.types.Object, expected: Any) -> int:
    current = mesh_revision(obj)
    if expected is None:
        raise HostError("INVALID_PARAMS", "expected_mesh_revision is required for topology-indexed mutation")
    if not isinstance(expected, int) or isinstance(expected, bool):
        raise HostError("INVALID_PARAMS", "expected_mesh_revision must be an integer")
    if current != expected:
        raise HostError(
            "STALE_TOPOLOGY",
            "mesh topology revision changed",
            data={"expected": expected, "actual": current, "object": object_id(obj)},
            retryable=True,
        )
    return current


def bump_mesh_revision(obj: bpy.types.Object) -> int:
    """Record a RoboVision-authored topology change and its new signature."""
    mesh = _mesh_of(obj)
    stored = mesh.get(MESH_REV_KEY, 0)
    revision = (int(stored) if isinstance(stored, (int, float)) else 0) + 1
    _store(mesh, revision, topology_signature(mesh))
    return revision
