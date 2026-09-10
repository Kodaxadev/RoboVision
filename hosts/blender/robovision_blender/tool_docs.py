from __future__ import annotations

from typing import Any


def _object(properties: dict[str, Any] | None = None, required: tuple[str, ...] = ()) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties or {},
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


def _array(items: dict[str, Any], *, min_items: int | None = None, max_items: int | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"type": "array", "items": items}
    if min_items is not None:
        value["minItems"] = min_items
    if max_items is not None:
        value["maxItems"] = max_items
    return value


NUMBER = {"type": "number"}
INTEGER = {"type": "integer"}
BOOLEAN = {"type": "boolean"}
STRING = {"type": "string"}
VEC3 = _array(NUMBER, min_items=3, max_items=3)
INDEX_ARRAY = _array({"type": "integer", "minimum": 0}, min_items=1)
OBJECT_REF = {"type": "string", "description": "RoboVision object id (preferred) or Blender object name"}
MESH_REV = {"type": "integer", "minimum": 0, "description": "Revision returned by mesh inspection/query; required for topology-indexed mutation"}


def _entry(summary: str, tags: tuple[str, ...], params: dict[str, Any]) -> dict[str, Any]:
    return {"summary": summary, "tags": tags, "params_schema": params}


METHOD_DOCS: dict[str, dict[str, Any]] = {
    "system.ping": _entry("Check that the Blender host is responsive.", ("system",), _object()),
    "system.hello": _entry("Discover editor version, security facts, revision and the compact live capability catalog.", ("system", "discovery"), _object()),
    "truth.measure": _entry(
        "Measure deterministic asset truth for one or more meshes: geometry validity, normals, "
        "components, boundaries, intersections, dimensions, transforms and pivots.",
        ("truth", "inspect", "verify"),
        _object(
            {
                "object": OBJECT_REF,
                "objects": _array(OBJECT_REF),
                "kinds": _array({"type": "string", "enum": ["geometry", "spatial"]}),
                "intentional_open": _array(OBJECT_REF),
                "check_intersections": BOOLEAN,
                "epsilon": {"type": "number", "exclusiveMinimum": 0},
                "max_dimension": {"type": "number", "exclusiveMinimum": 0},
            }
        ),
    ),
    "truth.geometry": _entry(
        "Measure geometry truth alone: validity, normals, components, boundaries, intersections.",
        ("truth", "inspect", "verify"),
        _object(
            {
                "object": OBJECT_REF,
                "objects": _array(OBJECT_REF),
                "intentional_open": _array(OBJECT_REF),
                "check_intersections": BOOLEAN,
                "epsilon": {"type": "number", "exclusiveMinimum": 0},
            }
        ),
    ),
    "truth.spatial": _entry(
        "Measure coordinate and scale truth alone: dimensions, bounds, transforms and pivots.",
        ("truth", "inspect", "verify"),
        _object(
            {
                "object": OBJECT_REF,
                "objects": _array(OBJECT_REF),
                "max_dimension": {"type": "number", "exclusiveMinimum": 0},
            }
        ),
    ),
    "truth.views": _entry(
        "The deterministic canonical camera set for a subject, derived from its own bounds "
        "with full projection provenance. Views are independent of any viewport.",
        ("truth", "perception", "inspect"),
        _object(
            {
                "object": OBJECT_REF,
                "objects": _array(OBJECT_REF),
                "level": {"type": "integer", "enum": [0, 1, 2]},
                "projection": {"type": "string", "enum": ["orthographic", "perspective"]},
                "width": INTEGER,
                "height": INTEGER,
            }
        ),
    ),
    "truth.coverage": _entry(
        "Which surface has actually been observed from a canonical view set, which regions "
        "remain unverified, and which camera would reveal the most of what is left.",
        ("truth", "perception", "verify"),
        _object(
            {
                "object": OBJECT_REF,
                "objects": _array(OBJECT_REF),
                "level": {"type": "integer", "enum": [0, 1, 2]},
                "projection": {"type": "string", "enum": ["orthographic", "perspective"]},
                "views": _array(STRING),
                "samples": {"type": "integer", "minimum": 64, "maximum": 65536},
                "min_coverage": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                "width": INTEGER,
                "height": INTEGER,
            }
        ),
    ),
    "truth.reference": _entry(
        "Compare the subject silhouette from one named canonical view against a reference "
        "image: overlap, contour distance and per-sector excess or deficit.",
        ("truth", "perception", "verify"),
        _object(
            {
                "object": OBJECT_REF,
                "objects": _array(OBJECT_REF),
                "reference": STRING,
                "view": STRING,
                "frame": _object({"center": VEC3, "radius": NUMBER}),
                "level": {"type": "integer", "enum": [0, 1, 2]},
                "projection": {"type": "string", "enum": ["orthographic", "perspective"]},
                "threshold": {"type": "number", "exclusiveMinimum": 0, "maximum": 1},
                "use_alpha": BOOLEAN,
                "alignment": {"type": "string", "enum": ["declared"]},
            },
            ("reference", "view"),
        ),
    ),
    "truth.pattern": _entry(
        "Check a declared repeated structure — count, spacing, orientation and dimensional "
        "consistency — against the geometry meant to satisfy it.",
        ("truth", "verify"),
        _object(
            {
                "components_of": OBJECT_REF,
                "members": _array(OBJECT_REF),
                "kind": {"type": "string", "enum": ["linear", "radial", "mirror"]},
                "count": {"type": "integer", "minimum": 1},
                "axis": STRING,
                "center": VEC3,
                "spacing": NUMBER,
                "spacing_tolerance": {"type": "number", "minimum": 0},
                "orientation_tolerance": {"type": "number", "minimum": 0},
                "dimension_variance": {"type": "number", "minimum": 0},
            },
            ("count",),
        ),
    ),
    "truth.locality": _entry(
        "Compare the current state against a stored snapshot under a declared blast "
        "radius: which targets changed, whether anything protected or undeclared did.",
        ("truth", "verify"),
        _object(
            {
                "before": STRING,
                "targets": _array(OBJECT_REF),
                "protecteds": _array(OBJECT_REF),
                "alloweds": _array(OBJECT_REF),
            },
            ("before", "targets"),
        ),
    ),
    "truth.evaluate": _entry(
        "Decide whether a candidate correction should be committed, rejected or treated "
        "as indeterminate, from Q0 and Q1 certificates and a vector acceptance policy.",
        ("truth", "verify"),
        _object(
            {
                "before": {"type": "object"},
                "after": {"type": "object"},
                "targets": _array(_object(
                    {"metric": STRING, "kind": STRING, "epsilon": NUMBER}, ("metric",))),
                "protected": _array(_object(
                    {"metric": STRING, "kind": STRING, "tolerance": NUMBER}, ("metric",))),
                "required_invariants": {},
                "advisory": _array(STRING),
                "locality": {"type": "object"},
                "require_locality": BOOLEAN,
            },
            ("before", "after", "targets"),
        ),
    ),
    "truth.compare": _entry(
        "Compare two measurement certificates; refuses when they do not describe the same "
        "subjects in the same world and coordinate contract.",
        ("truth", "verify"),
        _object({"before": {"type": "object"}, "after": {"type": "object"}}, ("before", "after")),
    ),
    "system.health": _entry(
        "Structured readiness: whether it is presently safe to observe, begin a correction, "
        "mutate, verify, and whether a transaction must be resolved first.",
        ("system", "discovery", "health"),
        _object(),
    ),
    "system.capabilities": _entry(
        "Search and page the live operation catalog; optionally include exact parameter schemas.",
        ("system", "discovery"),
        _object(
            {
                "query": STRING,
                "prefix": STRING,
                "tags": _array(STRING),
                "include_schema": BOOLEAN,
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 500},
            }
        ),
    ),
    "system.method": _entry(
        "Describe one operation including its exact parameter schema.",
        ("system", "discovery"),
        _object({"method": STRING}, ("method",)),
    ),
    "scene.describe": _entry(
        "Inspect scene objects and transforms without storing a snapshot.",
        ("scene", "inspect"),
        _object({"deep": BOOLEAN}),
    ),
    "scene.search": _entry(
        "Find scene objects by name substring and optional Blender object types.",
        ("scene", "inspect", "query"),
        _object({"query": STRING, "types": _array(STRING)}),
    ),
    "scene.snapshot": _entry(
        "Store a fingerprinted scene snapshot for later diff or restoration verification.",
        ("scene", "inspect", "history"),
        _object({"deep": BOOLEAN}),
    ),
    "scene.diff": _entry(
        "Compare current scene state with a previously stored RoboVision snapshot.",
        ("scene", "inspect", "history"),
        _object({"from_snapshot": STRING, "deep": BOOLEAN}, ("from_snapshot",)),
    ),
    "scene.changes_since": _entry(
        "List journal events after a cursor this host issued; omit the cursor to bootstrap "
        "a position without claiming any history.",
        ("scene", "inspect", "history"),
        _object({"cursor": STRING}),
    ),
    "scene.raycast": _entry(
        "Raycast evaluated scene geometry in world space and return hit object/face/location/normal.",
        ("scene", "spatial", "query"),
        _object({"origin": VEC3, "direction": VEC3, "distance": {"type": "number", "minimum": 0}}, ("origin", "direction")),
    ),
    "object.inspect": _entry(
        "Inspect one object including stable identity, transform, mesh digest and modifiers.",
        ("object", "inspect"),
        _object({"object": OBJECT_REF, "deep": BOOLEAN}, ("object",)),
    ),
    "object.create": _entry(
        "Create an empty, cube, or explicit mesh through Blender data/BMesh APIs.",
        ("object", "create", "modeling"),
        _object(
            {
                "kind": {"type": "string", "enum": ["empty", "cube", "mesh"]},
                "name": STRING,
                "size": {"type": "number", "exclusiveMinimum": 0},
                "vertices": _array(VEC3),
                "edges": _array(_array({"type": "integer", "minimum": 0}, min_items=2, max_items=2)),
                "faces": _array(_array({"type": "integer", "minimum": 0}, min_items=3)),
                "location": VEC3,
                "rotation": VEC3,
                "scale": VEC3,
            }
        ),
    ),
    "object.delete": _entry("Delete a local object and unlink it from the blend.", ("object", "modeling"), _object({"object": OBJECT_REF}, ("object",))),
    "object.duplicate": _entry(
        "Duplicate an object with a fresh stable identity and optional independent data copy.",
        ("object", "modeling"),
        _object({"object": OBJECT_REF, "name": STRING, "copy_data": BOOLEAN}, ("object",)),
    ),
    "object.transform": _entry(
        "Set object transform by location/rotation/scale or a complete world matrix.",
        ("object", "transform", "modeling"),
        _object(
            {
                "object": OBJECT_REF,
                "location": VEC3,
                "rotation": VEC3,
                "scale": VEC3,
                "matrix_world": _array(_array(NUMBER, min_items=4, max_items=4), min_items=4, max_items=4),
            },
            ("object",),
        ),
    ),
    "object.parent": _entry(
        "Set or clear an object's parent while optionally preserving world transform.",
        ("object", "hierarchy", "modeling"),
        _object({"object": OBJECT_REF, "parent": {"type": ["string", "null"]}, "preserve_world": BOOLEAN}, ("object",)),
    ),
    "mesh.inspect": _entry(
        "Inspect source mesh counts/revision and optionally evaluated post-modifier geometry.",
        ("mesh", "inspect"),
        _object({"object": OBJECT_REF, "evaluated": BOOLEAN}, ("object",)),
    ),
    "mesh.elements": _entry(
        "Page raw vertex, edge or face data for a mesh revision.",
        ("mesh", "inspect", "query"),
        _object(
            {
                "object": OBJECT_REF,
                "kind": {"type": "string", "enum": ["vertices", "edges", "faces"]},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
            },
            ("object",),
        ),
    ),
    "mesh.validate": _entry(
        "Check manifold/boundary/wire/loose/zero-length/degenerate/ngon topology and return implicated indices.",
        ("mesh", "inspect", "validation"),
        _object({"object": OBJECT_REF, "epsilon": {"type": "number", "minimum": 0}}, ("object",)),
    ),
    "mesh.query": _entry(
        "Semantically select mesh elements by spatial bounds, normals, size, connectivity properties and material.",
        ("mesh", "inspect", "query", "spatial"),
        _object(
            {
                "object": OBJECT_REF,
                "domain": {"type": "string", "enum": ["VERTEX", "EDGE", "FACE"]},
                "space": {"type": "string", "enum": ["OBJECT", "WORLD"]},
                "selected": BOOLEAN,
                "hidden": BOOLEAN,
                "area": _object({"min": NUMBER, "max": NUMBER}),
                "length": _object({"min": NUMBER, "max": NUMBER}),
                "valence": _object({"min": NUMBER, "max": NUMBER}),
                "material_index": {"type": "integer", "minimum": 0},
                "boundary": BOOLEAN,
                "manifold": BOOLEAN,
                "wire": BOOLEAN,
                "bounds": _object({"min": VEC3, "max": VEC3}),
                "normal": _object({"direction": VEC3, "min_dot": {"type": "number", "minimum": -1, "maximum": 1}, "max_dot": {"type": "number", "minimum": -1, "maximum": 1}}, ("direction",)),
                "sort_by": STRING,
                "max_results": {"type": "integer", "minimum": 1, "maximum": 20000},
                "details": BOOLEAN,
            },
            ("object",),
        ),
    ),
    "mesh.components": _entry(
        "Find disconnected topological islands with counts, bounds, centroids and revision-scoped indices.",
        ("mesh", "inspect", "query", "spatial"),
        _object({"object": OBJECT_REF, "space": {"type": "string", "enum": ["OBJECT", "WORLD"]}, "include_indices": BOOLEAN, "max_components": {"type": "integer", "minimum": 1, "maximum": 10000}}, ("object",)),
    ),
    "mesh.bevel": _entry(
        "Bevel explicit revision-scoped edge indices using BMesh.",
        ("mesh", "modeling", "hard-surface"),
        _object({"object": OBJECT_REF, "expected_mesh_revision": MESH_REV, "edge_indices": INDEX_ARRAY, "width": {"type": "number", "exclusiveMinimum": 0}, "segments": {"type": "integer", "minimum": 1}, "clamp_overlap": BOOLEAN}, ("object", "expected_mesh_revision", "edge_indices")),
    ),
    "mesh.extrude_faces": _entry(
        "Extrude a revision-scoped face region and translate the new vertices.",
        ("mesh", "modeling", "hard-surface"),
        _object({"object": OBJECT_REF, "expected_mesh_revision": MESH_REV, "face_indices": INDEX_ARRAY, "translation": VEC3}, ("object", "expected_mesh_revision", "face_indices")),
    ),
    "mesh.subdivide_edges": _entry(
        "Subdivide revision-scoped edges with configurable cuts and smoothing.",
        ("mesh", "modeling"),
        _object({"object": OBJECT_REF, "expected_mesh_revision": MESH_REV, "edge_indices": INDEX_ARRAY, "cuts": {"type": "integer", "minimum": 1, "maximum": 1000}, "smooth": {"type": "number", "minimum": -1, "maximum": 1}, "use_grid_fill": BOOLEAN, "use_single_edge": BOOLEAN, "use_only_quads": BOOLEAN}, ("object", "expected_mesh_revision", "edge_indices")),
    ),
    "mesh.inset_faces": _entry(
        "Inset revision-scoped faces as a region or individually.",
        ("mesh", "modeling", "hard-surface"),
        _object({"object": OBJECT_REF, "expected_mesh_revision": MESH_REV, "face_indices": INDEX_ARRAY, "mode": {"type": "string", "enum": ["REGION", "INDIVIDUAL"]}, "thickness": {"type": "number", "minimum": 0}, "depth": NUMBER, "use_even_offset": BOOLEAN, "use_interpolate": BOOLEAN, "use_relative_offset": BOOLEAN, "use_boundary": BOOLEAN, "use_edge_rail": BOOLEAN, "use_outset": BOOLEAN}, ("object", "expected_mesh_revision", "face_indices")),
    ),
    "mesh.bridge_loops": _entry(
        "Bridge revision-scoped edge loops through BMesh.",
        ("mesh", "modeling"),
        _object({"object": OBJECT_REF, "expected_mesh_revision": MESH_REV, "edge_indices": INDEX_ARRAY, "use_pairs": BOOLEAN, "use_cyclic": BOOLEAN, "use_merge": BOOLEAN, "merge_factor": NUMBER, "twist_offset": INTEGER}, ("object", "expected_mesh_revision", "edge_indices")),
    ),
    "mesh.merge_by_distance": _entry(
        "Merge selected or all vertices within a distance threshold.",
        ("mesh", "modeling", "cleanup"),
        _object({"object": OBJECT_REF, "expected_mesh_revision": MESH_REV, "vertex_indices": _array({"type": "integer", "minimum": 0}), "distance": {"type": "number", "minimum": 0}, "use_connected": BOOLEAN}, ("object", "expected_mesh_revision")),
    ),
    "mesh.triangulate": _entry(
        "Triangulate selected or all faces using explicit quad/ngon methods.",
        ("mesh", "modeling", "cleanup"),
        _object({"object": OBJECT_REF, "expected_mesh_revision": MESH_REV, "face_indices": _array({"type": "integer", "minimum": 0}), "quad_method": STRING, "ngon_method": STRING}, ("object", "expected_mesh_revision")),
    ),
    "mesh.recalc_normals": _entry(
        "Recalculate normals for selected or all faces without changing topology revision.",
        ("mesh", "modeling", "normals"),
        _object({"object": OBJECT_REF, "face_indices": _array({"type": "integer", "minimum": 0})}, ("object",)),
    ),
    "mesh.bisect_plane": _entry(
        "Bisect the mesh by a plane and optionally clear either side.",
        ("mesh", "modeling"),
        _object({"object": OBJECT_REF, "expected_mesh_revision": MESH_REV, "plane_co": VEC3, "plane_no": VEC3, "distance": {"type": "number", "minimum": 0}, "use_snap_center": BOOLEAN, "clear_outer": BOOLEAN, "clear_inner": BOOLEAN}, ("object", "expected_mesh_revision", "plane_no")),
    ),
    "mesh.solidify": _entry(
        "Add shell thickness to selected or all faces directly through BMesh.",
        ("mesh", "modeling"),
        _object({"object": OBJECT_REF, "expected_mesh_revision": MESH_REV, "face_indices": _array({"type": "integer", "minimum": 0}), "thickness": NUMBER}, ("object", "expected_mesh_revision")),
    ),
    "mesh.symmetrize": _entry(
        "Symmetrize geometry across one local axis direction.",
        ("mesh", "modeling", "symmetry"),
        _object({"object": OBJECT_REF, "expected_mesh_revision": MESH_REV, "direction": {"type": "string", "enum": ["-X", "-Y", "-Z", "X", "Y", "Z"]}, "distance": {"type": "number", "minimum": 0}, "use_shapekey": BOOLEAN}, ("object", "expected_mesh_revision")),
    ),
    "modifier.list": _entry("List an object's modifiers.", ("modifier", "inspect"), _object({"object": OBJECT_REF}, ("object",))),
    "modifier.add": _entry(
        "Create a Blender modifier and set validated writable RNA properties.",
        ("modifier", "modeling"),
        _object({"object": OBJECT_REF, "type": STRING, "name": STRING, "properties": {"type": "object"}}, ("object", "type")),
    ),
    "modifier.set": _entry(
        "Update writable RNA properties on an existing modifier.",
        ("modifier", "modeling"),
        _object({"object": OBJECT_REF, "modifier": STRING, "properties": {"type": "object", "minProperties": 1}}, ("object", "modifier", "properties")),
    ),
    "modifier.apply": _entry(
        "Apply a modifier in an explicit object context and advance mesh topology revision.",
        ("modifier", "modeling"),
        _object({"object": OBJECT_REF, "modifier": STRING, "single_user": BOOLEAN}, ("object", "modifier")),
    ),
    "viewport.inspect": _entry("Inspect the active 3D viewport projection, matrices, lens, clipping and display state.", ("viewport", "perception"), _object()),
    "viewport.focus": _entry(
        "Frame one object in the active 3D viewport.",
        ("viewport", "perception", "navigation"),
        _object({"object": OBJECT_REF, "padding": {"type": "number", "minimum": 1}}, ("object",)),
    ),
    "viewport.axis": _entry(
        "Align the active 3D viewport to a principal axis.",
        ("viewport", "perception", "navigation"),
        _object({"axis": {"type": "string", "enum": ["FRONT", "BACK", "LEFT", "RIGHT", "TOP", "BOTTOM"]}}),
    ),
    "viewport.capture": _entry(
        "Capture actual viewport pixels with exact view/projection provenance.",
        ("viewport", "perception", "evidence"),
        _object({"path": STRING, "shading": {"type": "string", "enum": ["WIREFRAME", "SOLID", "MATERIAL", "RENDERED"]}, "overlays": BOOLEAN}),
    ),
    "transaction.begin": _entry(
        "Begin a verified edit transaction and record its scene fingerprint.",
        ("transaction", "history", "safety"),
        _object({
            "transaction": STRING,
            "label": STRING,
            # Documented because the precommitted path is the one a client should
            # take, and a credential nobody knows how to send is a credential
            # nobody sends.
            "recovery_verifier": STRING,
            "recovery_handle": STRING,
        }),
    ),
    "transaction.adopt": _entry(
        "Reclaim an orphaned transaction by proving you opened it.",
        ("transaction", "recovery", "safety"),
        _object(
            {"transaction": STRING, "recovery_token": STRING, "next_recovery_verifier": STRING},
            ("transaction", "recovery_token"),
        ),
    ),
    "transaction.discard": _entry(
        "Give up a transaction rather than claim an outcome for it.",
        ("transaction", "recovery", "safety"),
        _object({"transaction": STRING}, ("transaction",)),
    ),
    "transaction.status": _entry(
        "Read what became of a transaction. Never repeats its side effect.",
        ("transaction", "recovery", "read"),
        _object({"transaction": STRING}),
    ),
    "transaction.commit": _entry(
        "Commit the active transaction, refusing contaminated state unless explicitly forced.",
        ("transaction", "history", "safety"),
        _object({"transaction": STRING, "force": BOOLEAN}, ("transaction",)),
    ),
    "transaction.rollback": _entry(
        "Undo until the transaction begin fingerprint is exactly restored and verified.",
        ("transaction", "history", "safety"),
        _object({"transaction": STRING, "max_steps": {"type": "integer", "minimum": 1, "maximum": 512}, "force": BOOLEAN}, ("transaction",)),
    ),
}
