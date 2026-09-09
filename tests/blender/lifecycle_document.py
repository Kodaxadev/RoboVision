"""Blender document lifecycle: what survives a save, a reopen, and a swap.

Unity has restart, domain-reload and package-reload evidence. Blender had none
at this layer, so its lifecycle behaviour was assumed rather than known. This
gauntlet states what each boundary must do and fails if it does not.

The contract being asserted, from docs/CROSS_EDITOR_STATE.md:

  save            object ids keep, mesh revisions keep, fingerprint continuous
  reopen same     ids resolve to the same objects, fingerprint matches
  load different  the document incarnation is new while the bridge is not — the
                  same code is running with its sockets still bound — the
                  revision resets, ids from the old document stop resolving, the
                  identity owner registry is cleared, and an open transaction is
                  abandoned with a reason rather than left pointing at a
                  document that is gone

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
    described_a = rv.result("scene.describe")
    bridge_a = described_a["bridge"]
    document_a = described_a["document_incarnation"]
    expect(bridge_a.startswith("rvbridge:"), f"no bridge identity reported: {bridge_a}")
    expect(document_a.startswith("rvdoc:"), f"no document incarnation reported: {document_a}")

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
    document_reopened = described["document_incarnation"]
    expect(
        document_reopened != document_a,
        "reopening a document is a new loaded world; the document incarnation must change",
    )
    expect(
        described["bridge"] == bridge_a,
        "reopening a document must not rotate the bridge identity; the same code is "
        "still loaded with its sockets still bound",
    )

    # ------------------------------------------------- a different document
    clean_scene()
    beta = rv.result("object.create", {"kind": "cube", "name": "Beta"})["id"]
    bpy.ops.wm.save_as_mainfile(filepath=str(DOC_B))
    expect(beta != alpha, "two documents minted the same object id")

    bpy.ops.wm.open_mainfile(filepath=str(DOC_A))
    swapped = rv.result("scene.describe")

    expect(
        swapped["document_incarnation"] not in (document_a, document_reopened),
        "loading a different document must mint a new document incarnation",
    )
    expect(
        swapped["bridge"] == bridge_a,
        "loading a different document must not rotate the bridge identity",
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

    # ------------------------------------------------ unsaved, then saved
    # read_homefile is how Blender reaches a genuinely pathless document;
    # emptying the objects of a saved file leaves it that saved file.
    bpy.ops.wm.read_homefile(use_empty=True)
    rv2 = Host("lifecycle-save")
    unsaved = rv2.result("scene.describe")
    expect(unsaved["document"] is None, f"an unsaved document reported a path: {unsaved['document']}")
    fresh_id = rv2.result("object.create", {"kind": "cube", "name": "BeforeSave"})["id"]
    before_first_save = rv2.fingerprint()

    saved_path = WORK / "first_save.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(saved_path))
    first_saved = rv2.result("scene.describe")
    expect(
        first_saved["document"] and Path(first_saved["document"]) == Path(bpy.data.filepath),
        f"the host did not learn the document path on save: {first_saved['document']}",
    )
    expect(
        rv2.fingerprint() == before_first_save,
        "the first save changed the scene fingerprint; saving is not an edit",
    )
    expect(
        rv2.result("object.inspect", {"object": fresh_id})["name"] == "BeforeSave",
        "an id minted before the first save stopped resolving after it",
    )
    expect(
        first_saved["document_incarnation"] == unsaved["document_incarnation"],
        "saving is not a load; the document incarnation must not rotate",
    )

    # ------------------------------------------------------------- Save As
    save_as_path = WORK / "saved_as.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(save_as_path))
    saved_as = rv2.result("scene.describe")
    expect(
        saved_as["document"] and Path(saved_as["document"]) == Path(bpy.data.filepath),
        f"the host kept the previous path after Save As: {saved_as['document']}",
    )
    expect(
        saved_as["document_incarnation"] == first_saved["document_incarnation"],
        "Save As loads nothing; the document incarnation must not rotate",
    )
    expect(
        rv2.result("object.inspect", {"object": fresh_id})["name"] == "BeforeSave",
        "Save As invalidated an object id",
    )

    # --------------------------------------------- repeated reopen, A to B to A
    seen_incarnations = []
    for _ in range(3):
        bpy.ops.wm.open_mainfile(filepath=str(DOC_A))
        seen_incarnations.append(rv2.result("scene.describe")["document_incarnation"])
    expect(
        len(set(seen_incarnations)) == 3,
        f"reopening the same document reused an incarnation id: {seen_incarnations}",
    )

    bpy.ops.wm.open_mainfile(filepath=str(DOC_B))
    bpy.ops.wm.open_mainfile(filepath=str(DOC_A))
    round_trip = rv2.result("scene.describe")
    expect(
        {entry["name"] for entry in round_trip["objects"]} == {"Subject"},
        f"A to B to A did not return to A: {[e['name'] for e in round_trip['objects']]}",
    )
    expect(round_trip["revision"] == 0, "the revision did not reset on the return leg")

    # ------------------------------- a handle from a closed document incarnation
    stale_snapshot = rv2.result("scene.snapshot")["snapshot"]
    bpy.ops.wm.open_mainfile(filepath=str(DOC_A))
    rv2.call("scene.diff", {"from_snapshot": stale_snapshot}, ok=False, code="STALE_DOCUMENT")

    # ------------------------------------------- a reattached bridge is a new one
    previous_bridge = rv2.result("scene.describe")["bridge"]
    rv2.runtime.remove_handlers()
    rv2.runtime.install_handlers()
    reattached = rv2.result("scene.describe")
    expect(
        reattached["bridge"] != previous_bridge,
        "reattaching the bridge must mint a new bridge identity; this is what "
        "add-on disable and enable does to the runtime",
    )
    expect(
        reattached["document_incarnation"] != round_trip["document_incarnation"],
        "a rebuilt bridge cannot know the previous document incarnation and must "
        "not claim continuity it has no way to establish",
    )

    (artifact_dir("blender-lifecycle") / "summary.txt").write_text(
        "\n".join(
            [
                f"blender {bpy.app.version_string}",
                f"bridge {bridge_a}",
                f"document_a {document_a}",
                f"document_reopened {document_reopened}",
                f"document_after_swap {swapped['document_incarnation']}",
                f"owner_registry_after_swap {len(owners)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print("  document lifecycle boundaries held", flush=True)


run_gate("BLENDER_LIFECYCLE", main, "blender-lifecycle")
