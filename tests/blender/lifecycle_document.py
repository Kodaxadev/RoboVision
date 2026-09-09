"""Blender document lifecycle: what survives a save, a reopen, and a swap.

Unity has restart, domain-reload and package-reload evidence. Blender had none
at this layer, so its lifecycle behaviour was assumed rather than known. This
gauntlet states what each boundary must do and fails if it does not.

The contract being asserted, from docs/CROSS_EDITOR_STATE.md:

  save            object ids keep, mesh revisions keep, fingerprint continuous
  reopen same     ids resolve to the same objects, fingerprint matches
  load different  runtime incarnation is new, revision resets, ids from the old
                  document stop resolving, the identity owner registry is
                  cleared, and an open transaction is abandoned with a reason
                  rather than left pointing at a document that is gone

Runs headless: none of it needs a viewport.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import Host, artifact_dir, expect, run_gate  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "hosts" / "blender"))
from robovision_blender import identity as identity_module  # noqa: E402

WORK = Path(tempfile.gettempdir()) / "robovision-lifecycle-gate"
DOC_A = WORK / "document_a.blend"
DOC_B = WORK / "document_b.blend"


def clean_scene() -> None:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def main() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    for stale in (DOC_A, DOC_B):
        if stale.exists():
            stale.unlink()

    clean_scene()
    rv = Host("lifecycle")

    # ---------------------------------------------------------------- save
    created = rv.result("object.create", {"kind": "cube", "name": "Alpha"})
    alpha = created["id"]
    alpha_mesh_revision = rv.result("mesh.inspect", {"object": alpha})["mesh_revision"]
    before_save = rv.fingerprint()
    incarnation_a = rv.result("scene.describe")["runtime"]
    expect(incarnation_a.startswith("rvrt:"), f"no runtime incarnation reported: {incarnation_a}")

    bpy.ops.wm.save_as_mainfile(filepath=str(DOC_A))
    expect(DOC_A.exists(), "the document did not save")
    expect(
        rv.fingerprint() == before_save,
        "saving the document changed the scene fingerprint; saving is not an edit",
    )

    # -------------------------------------------------------------- reopen
    bpy.ops.wm.open_mainfile(filepath=str(DOC_A))

    described = rv.result("scene.describe")
    by_name = {entry["name"]: entry["id"] for entry in described["objects"]}
    expect(by_name.get("Alpha") == alpha, "the object id did not survive a save and reopen")
    expect(
        rv.result("object.inspect", {"object": alpha})["name"] == "Alpha",
        "the durable id no longer resolves to its object after a reopen",
    )
    expect(
        rv.result("mesh.inspect", {"object": alpha})["mesh_revision"] == alpha_mesh_revision,
        "the mesh revision did not survive a reopen",
    )
    expect(
        rv.fingerprint() == before_save,
        "the scene fingerprint changed across a save and reopen of the same document",
    )
    expect(
        described["document"] and Path(described["document"]).name == DOC_A.name,
        f"the host reports the wrong document: {described['document']}",
    )
    incarnation_reopened = described["runtime"]
    expect(
        incarnation_reopened != incarnation_a,
        "reopening a document is a new runtime incarnation; the id must change",
    )

    # ------------------------------------------------- a different document
    clean_scene()
    beta = rv.result("object.create", {"kind": "cube", "name": "Beta"})["id"]
    bpy.ops.wm.save_as_mainfile(filepath=str(DOC_B))
    expect(beta != alpha, "two documents minted the same object id")

    bpy.ops.wm.open_mainfile(filepath=str(DOC_A))
    swapped = rv.result("scene.describe")

    expect(
        swapped["runtime"] not in (incarnation_a, incarnation_reopened),
        "loading a different document must mint a new runtime incarnation",
    )
    expect(swapped["revision"] == 0, f"the scene revision must reset on load, got {swapped['revision']}")
    expect(
        {entry["name"] for entry in swapped["objects"]} == {"Alpha"},
        "the wrong document is loaded",
    )
    rv.call("object.inspect", {"object": beta}, ok=False, code="NOT_FOUND")
    expect(
        swapped["identity_repairs"] == [],
        f"loading a document required identity repair: {swapped['identity_repairs']}",
    )
    # Stale pointers from a closed document must not linger; a reused address
    # could otherwise hand an id to the wrong object.
    owners = identity_module._ID_OWNERS
    expect(
        all(entry.startswith("b3d:") for entry in owners),
        "the identity owner registry holds malformed entries",
    )
    expect(
        len(owners) <= len(swapped["objects"]),
        f"the owner registry kept {len(owners)} entries for {len(swapped['objects'])} objects; "
        "state from a closed document survived",
    )

    # ------------------------------------- a transaction across a document swap
    clean_scene()
    rv.result("object.create", {"kind": "cube", "name": "Subject"})
    bpy.ops.wm.save_as_mainfile(filepath=str(DOC_A))

    transaction = rv.result("transaction.begin", {"label": "across documents"})["transaction"]
    rv.call("object.create", {"kind": "cube", "name": "Doomed"})

    bpy.ops.wm.open_mainfile(filepath=str(DOC_B))

    # Rolling back is impossible: the begin fingerprint describes a document
    # that is no longer open. The host must say that, not blame a human edit.
    rolled = rv.call("transaction.rollback", {"transaction": transaction}, ok=False, code="DOCUMENT_CHANGED")
    expect(
        rolled["error"]["data"]["reason"] == "document_changed",
        f"the abandonment reason was not reported: {rolled['error']}",
    )
    rv.call("transaction.commit", {"transaction": transaction}, ok=False, code="DOCUMENT_CHANGED")

    # And the host must be usable again immediately afterwards.
    fresh = rv.result("transaction.begin", {"label": "after the swap"})["transaction"]
    rv.result("transaction.rollback", {"transaction": fresh})

    (artifact_dir("blender-lifecycle") / "summary.txt").write_text(
        "\n".join(
            [
                f"blender {bpy.app.version_string}",
                f"incarnation_a {incarnation_a}",
                f"incarnation_reopened {incarnation_reopened}",
                f"incarnation_after_swap {swapped['runtime']}",
                f"owner_registry_after_swap {len(owners)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print("  document lifecycle boundaries held", flush=True)


run_gate("BLENDER_LIFECYCLE", main, "blender-lifecycle")
