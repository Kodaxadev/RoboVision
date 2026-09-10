"""Shared plumbing for the Deterministic Asset Truth gates.

Damage is applied through BMesh rather than through RoboVision's own mutation
path, deliberately: what these gates test is whether a measurement notices a
state, not whether the host can produce it. A defect built with the tool being
tested would prove the two agree, which is a weaker claim than either of them
being right.
"""
from __future__ import annotations

import bmesh
import bpy


def cube(rv, name: str, **params) -> str:
    return rv.result("object.create", {"kind": "cube", "name": name, **params})["id"]


def edit(name: str, mutate) -> None:
    """Damage a mesh the way any other add-on or a human would."""
    mesh = bpy.data.objects[name].data
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        mutate(bm)
        bm.to_mesh(mesh)
        mesh.update()
    finally:
        bm.free()


def metrics(certificate: dict) -> dict:
    return {name: entry["value"] for name, entry in certificate["metrics"].items()}


def failing(certificate: dict) -> list[str]:
    return sorted(name for name, entry in certificate["invariants"].items()
                  if not entry["holds"])
