"""What happens when an object's identity is damaged, not the object.

`_robovision_id` is an ordinary custom property. A script, another add-on or a
user with the N panel open can delete it or overwrite it, and until now doing so
minted a replacement id — which reached the agent as a deletion and a creation,
the scene claiming an object had been destroyed when only the host's record of
its name was damaged.

These assert the repair and, just as importantly, its limits: where continuity
cannot be proven, nothing is claimed.

Run through `journal_gate.py`; this module only supplies scenarios.
"""
from __future__ import annotations

from pathlib import Path

import bpy

from _harness import Host, clean_scene, cursor_of, events_of, expect

WORK = Path(bpy.app.tempdir) / "robovision-journal-identity"
KEY = "_robovision_id"


def _named(name: str) -> bpy.types.Object:
    obj = bpy.data.objects.get(name)
    expect(obj is not None, f"the test lost track of {name}")
    return obj


def a_removed_id_is_repaired_not_reminted(rv: Host) -> None:
    clean_scene()
    created = rv.result("object.create", {"kind": "cube", "name": "Repairable"})["id"]
    rv.result("scene.snapshot")
    cursor = cursor_of(rv)
    revision = rv.result("scene.describe")["revision"]

    # Exactly the tampering measured before this was fixed.
    del _named("Repairable")[KEY]
    expect(KEY not in _named("Repairable"), "precondition: the property is still there")

    described = rv.result("scene.describe")
    reported = [obj["id"] for obj in described["objects"]]
    expect(reported == [created],
           f"the object came back under a different identity: {reported} instead of {[created]}")
    expect(_named("Repairable")[KEY] == created, "the property was not written back")

    changes = rv.result("scene.changes_since", {"cursor": cursor})
    expect(not events_of(changes, "OBJECT_DELETED"),
           f"a repair was reported as a destruction: {changes['events']}")
    expect(not events_of(changes, "OBJECT_CREATED"),
           f"a repair was reported as a creation: {changes['events']}")

    repairs = events_of(changes, "IDENTITY_REPAIRED")
    expect(len(repairs) == 1, f"the repair was not journalled: {changes['events']}")
    event = repairs[0]
    expect(event["ids"] == [created], f"the repair named {event['ids']}")
    expect(event["source"] == "host", f"a control-plane repair was attributed to {event['source']}")
    expect(event["detail"].get("basis"), "the repair claimed continuity without saying on what basis")

    expect(described["revision"] == revision,
           "repairing the host's own bookkeeping moved the authored scene revision "
           f"from {revision} to {described['revision']}")
    expect(changes["certain"] is True, "a repair the host understood cost it its certainty")


def an_overwritten_id_is_repaired(rv: Host) -> None:
    clean_scene()
    created = rv.result("object.create", {"kind": "cube", "name": "Overwritten"})["id"]
    rv.result("scene.snapshot")
    cursor = cursor_of(rv)

    _named("Overwritten")[KEY] = "b3d:deliberately-wrong"

    described = rv.result("scene.describe")
    expect([obj["id"] for obj in described["objects"]] == [created],
           "a deliberately wrong id was accepted as the object's identity")
    expect(_named("Overwritten")[KEY] == created, "the wrong value was left in place")

    changes = rv.result("scene.changes_since", {"cursor": cursor})
    repairs = events_of(changes, "IDENTITY_REPAIRED")
    expect(len(repairs) == 1, f"the repair was not journalled: {changes['events']}")
    expect(repairs[0]["detail"]["reason"] == "the stored id was overwritten",
           f"the repair gave the wrong reason: {repairs[0]['detail']}")


def a_copy_is_a_new_object_not_a_repair(rv: Host) -> None:
    """Duplicate ids are still separated, and the copy is reported as new.

    Blender copies custom properties into a duplicate, so the copy arrives
    carrying the original's id. That is not damage to the original's identity
    and must not be repaired as if it were: the copy is a different object and
    has to be told apart from the object it was copied from.
    """
    clean_scene()
    original = rv.result("object.create", {"kind": "cube", "name": "Original"})["id"]
    rv.result("scene.snapshot")
    cursor = cursor_of(rv)

    source = _named("Original")
    copy = source.copy()
    copy.data = source.data
    bpy.context.scene.collection.objects.link(copy)
    expect(copy[KEY] == original, "precondition: the copy did not inherit the id")

    described = rv.result("scene.describe")
    ids = sorted(obj["id"] for obj in described["objects"])
    expect(original in ids, "the original lost its identity to its own copy")
    expect(len(ids) == 2 and len(set(ids)) == 2, f"two objects shared one identity: {ids}")

    changes = rv.result("scene.changes_since", {"cursor": cursor})
    creations = events_of(changes, "OBJECT_CREATED")
    expect(len(creations) == 1, f"the copy was not reported as a new object: {changes['events']}")
    expect(creations[0]["ids"] != [original], "the creation was attributed to the original's id")
    expect(not events_of(changes, "OBJECT_DELETED"),
           f"separating a duplicate reported a deletion: {changes['events']}")


def continuity_is_not_claimed_across_a_reopen(rv: Host) -> None:
    """The registry is a claim about one loaded session, and says so.

    Two directions, and the difference between them is the whole point. Damage
    the property while the file is open and the host watched this object being
    given that id, so it can put it back. Damage it in the file itself and
    reopen, and there is nothing left: the registry died with the load and the
    property is the only durable evidence there ever was. Minting a new id there
    is correct; claiming the old one would be inventing proof.
    """
    WORK.mkdir(parents=True, exist_ok=True)
    document = WORK / "identity.blend"

    clean_scene()
    original = rv.result("object.create", {"kind": "cube", "name": "Reopened"})["id"]
    bpy.ops.wm.save_as_mainfile(filepath=str(document))
    bpy.ops.wm.open_mainfile(filepath=str(document))

    described = rv.result("scene.describe")
    expect([obj["id"] for obj in described["objects"]] == [original],
           "the id stored in the file did not survive the reopen")

    # Remove it from the file itself, with no host read in between to repair it.
    del _named("Reopened")[KEY]
    bpy.ops.wm.save_as_mainfile(filepath=str(document))
    expect(KEY not in _named("Reopened"),
           "precondition: the property was restored before the file was written")
    bpy.ops.wm.open_mainfile(filepath=str(document))

    after = rv.result("scene.describe")
    minted = [obj["id"] for obj in after["objects"]]
    expect(minted and minted != [original],
           "the host claimed identity continuity it had no evidence for")

    # Everything this world has ever journalled, since it opened.
    prefix, journal, world, epoch, _ = cursor_of(rv).split(":")
    history = rv.result(
        "scene.changes_since", {"cursor": f"{prefix}:{journal}:{world}:{epoch}:0"}
    )
    expect(not events_of(history, "IDENTITY_REPAIRED"),
           f"a repair was claimed with nothing to base it on: {history['events']}")


SCENARIOS = (
    a_removed_id_is_repaired_not_reminted,
    an_overwritten_id_is_repaired,
    a_copy_is_a_new_object_not_a_repair,
    continuity_is_not_claimed_across_a_reopen,
)
