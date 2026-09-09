"""What the journal must report, and who it must blame.

Silence is the dangerous direction. An empty event list means "nothing
changed", so every path that could produce one while something *had* changed is
asserted against here: a notification Blender never delivered, an authoritative
read that trusted a clean dirty flag, a command that ran without moving
anything.

Run through `journal_gate.py`; this module only supplies scenarios.
"""
from __future__ import annotations

import bpy

from _harness import Host, clean_scene, cursor_of, events_of, expect, missed_notification


def agent_changes_are_attributed(rv: Host) -> None:
    clean_scene()
    rv.result("scene.snapshot")
    cursor = cursor_of(rv)

    created = rv.result("object.create", {"kind": "cube", "name": "Journalled"})
    changes = rv.result("scene.changes_since", {"cursor": cursor})

    creations = events_of(changes, "OBJECT_CREATED")
    expect(len(creations) == 1, f"a create produced {len(creations)} create events")
    event = creations[0]
    expect(event["ids"] == [created["id"]], f"the event named {event['ids']}")
    expect(event["source"] == "agent", f"the agent's own mutation was attributed to {event['source']}")
    expect(event.get("request"), "an agent event carried no request id")
    expect(changes["certain"] is True, "an ordinary mutation cost the journal its certainty")


def sequence_is_monotonic(rv: Host) -> None:
    clean_scene()
    rv.result("scene.snapshot")
    cursor = cursor_of(rv)

    ids = [rv.result("object.create", {"kind": "cube", "name": f"Seq{index}"})["id"] for index in range(4)]
    rv.call("object.transform", {"object": ids[0], "location": [1.0, 0.0, 0.0]})
    rv.call("object.delete", {"object": ids[3]})

    changes = rv.result("scene.changes_since", {"cursor": cursor})
    sequences = [event["sequence"] for event in changes["events"]]
    expect(sequences == sorted(sequences), f"sequences arrived out of order: {sequences}")
    expect(len(set(sequences)) == len(sequences), "a sequence number was reused")
    expect(all(a + 1 == b for a, b in zip(sequences, sequences[1:])), f"sequences have gaps: {sequences}")

    kinds = {event["type"] for event in changes["events"]}
    expect("OBJECT_CREATED" in kinds and "OBJECT_DELETED" in kinds,
           f"create and delete were not both journalled: {kinds}")

    # Asking again from the same point is stable.
    again = rv.result("scene.changes_since", {"cursor": cursor})
    expect([event["sequence"] for event in again["events"]] == sequences,
           "re-reading the same range returned different events")
    # And asking from the end returns nothing.
    tail = rv.result("scene.changes_since", {"cursor": changes["cursor"]})
    expect(tail["events"] == [], f"asking past the end returned {len(tail['events'])} events")


def editor_changes_are_attributed(rv: Host) -> None:
    clean_scene()
    target = rv.result("object.create", {"kind": "cube", "name": "Bystander"})["id"]
    rv.result("scene.snapshot")
    cursor = cursor_of(rv)

    # A change the host did not make, of the kind a human makes.
    bpy.data.objects["Bystander"].location.x = 4.0
    bpy.context.view_layer.update()
    rv.call("scene.describe")  # let the host notice

    changes = rv.result("scene.changes_since", {"cursor": cursor})
    attributed = [event for event in changes["events"] if event["source"] == "editor"]
    expect(attributed, f"an external edit produced no editor-attributed event: {changes['events']}")
    expect(
        any(target in event["ids"] for event in attributed),
        f"the editor event did not name the object that moved: {attributed}",
    )
    expect(changes["certain"] is True, "an attributable external edit should not cost certainty")


def topology_changes_are_distinguished(rv: Host) -> None:
    clean_scene()
    target = rv.result("object.create", {"kind": "cube", "name": "Topo"})["id"]
    revision = rv.result("mesh.inspect", {"object": target})["mesh_revision"]
    rv.result("scene.snapshot")
    cursor = cursor_of(rv)

    rv.call("mesh.subdivide_edges", {
        "object": target, "edge_indices": [0, 1], "expected_mesh_revision": revision,
    })

    changes = rv.result("scene.changes_since", {"cursor": cursor})
    topology = events_of(changes, "TOPOLOGY_CHANGED")
    expect(topology, f"a topology edit was not distinguished from a plain change: {changes['events']}")
    expect(target in topology[0]["ids"], "the topology event named the wrong object")


def a_missed_notification_is_discovered_at_resync(rv: Host) -> None:
    """The hazard the journal exists to survive.

    An edit lands, the notification never arrives, and the next mutation's
    authoritative resync is the first thing to see it. Advancing the revision
    and replacing the baseline without journalling anything buried a real editor
    change under the agent's own next command: the client saw the revision jump
    by two and was handed one event.
    """
    clean_scene()
    target = rv.result("object.create", {"kind": "cube", "name": "Unwatched"})["id"]
    rv.result("scene.snapshot")
    cursor = cursor_of(rv)
    revision_before = rv.result("scene.describe")["revision"]

    with missed_notification():
        bpy.data.objects["Unwatched"].location.x = 9.0

    # The premise: the host has not been told, and still thinks it knows.
    expect(rv.runtime._dirty is False, "the notification was delivered; this is not a missed one")
    expect(rv.runtime.journal.certain is True, "precondition: the journal believed itself certain")

    # A mutation, whose pre-mutation resync is the first authoritative read.
    made = rv.call("object.create", {"kind": "cube", "name": "Innocent"})
    changes = rv.result("scene.changes_since", {"cursor": cursor})

    editor_events = [event for event in changes["events"] if event["source"] == "editor"]
    resync_events = events_of(changes, "RESYNC_REQUIRED")
    expect(
        editor_events or resync_events,
        "an edit discovered at resync was absorbed into the new baseline with nothing "
        f"journalled: {changes['events']}",
    )
    if editor_events:
        expect(
            any(target in event["ids"] for event in editor_events),
            f"the discovered change did not name the object that moved: {editor_events}",
        )
        expect(changes["certain"] is True,
               "the change was attributable, so certainty should have survived")
    else:
        expect(changes["certain"] is False,
               "the host emitted RESYNC_REQUIRED and went on claiming certainty")

    # Revisions and events must agree: no revision may exist with nothing to
    # explain it.
    explained = {event["revision"] for event in changes["events"]}
    expect(
        set(range(revision_before + 1, made["revision"] + 1)) <= explained,
        f"revisions {revision_before + 1}..{made['revision']} advanced but only "
        f"{sorted(explained)} are explained by events",
    )


def an_authoritative_snapshot_rebuilds_a_wrong_baseline(rv: Host) -> None:
    """`scene.snapshot` must be authoritative even when nothing looks wrong.

    The host believes itself certain and its dirty flag is clear, so nothing
    prompts it to re-read. A snapshot that trusted that would hand back a
    baseline describing a scene which no longer exists and stamp it
    authoritative.
    """
    clean_scene()
    target = rv.result("object.create", {"kind": "cube", "name": "Drifted"})["id"]
    rv.result("scene.snapshot")
    cursor = cursor_of(rv)

    with missed_notification():
        bpy.data.objects["Drifted"].location.z = 5.0

    expect(rv.runtime._dirty is False, "precondition: the host must believe nothing happened")

    snapshot = rv.result("scene.snapshot")
    expect(
        rv.runtime._last_fingerprint == snapshot["fingerprint"],
        "an authoritative snapshot did not become the host's baseline",
    )
    expect(
        rv.runtime._last_snapshot is not None
        and rv.runtime._last_snapshot["fingerprint"] == snapshot["fingerprint"],
        "the host kept a baseline that disagrees with the snapshot it just returned",
    )

    changes = rv.result("scene.changes_since", {"cursor": cursor})
    if changes["certain"]:
        named = [event for event in changes["events"] if target in event["ids"]]
        expect(named, f"the snapshot found the change but did not journal it: {changes['events']}")
        expect(named[0]["source"] == "editor",
               f"a change the agent did not make was attributed to {named[0]['source']}")
    else:
        expect(snapshot["journal"]["opened_new_epoch"] is True,
               "certainty was lost and the authoritative snapshot did not open a new epoch")

    # And a later snapshot, with nothing having drifted, must be quiet.
    settled = rv.result("scene.snapshot")
    expect(settled["journal"]["opened_new_epoch"] is False,
           "a snapshot opened a new epoch while the journal was already certain")


def uncertainty_is_sticky(rv: Host) -> None:
    """A change the host can see but cannot attribute.

    Moving the frame moves the fingerprint without changing any object, so the
    diff has nothing to name. That is the real shape of an unattributable
    change, rather than one manufactured by deleting the host's own baseline.
    """
    clean_scene()
    rv.result("object.create", {"kind": "cube", "name": "Anchor"})
    rv.result("scene.snapshot")
    cursor = cursor_of(rv)
    expect(rv.result("scene.changes_since", {"cursor": cursor})["certain"] is True,
           "the journal did not start certain")

    with missed_notification():
        bpy.context.scene.frame_current = 42
    # Nothing so far is authoritative, so the host has not looked. A mutation's
    # resync is what finds it.
    rv.call("object.create", {"kind": "cube", "name": "Trigger"})

    lost = rv.result("scene.changes_since", {"cursor": cursor})
    expect(lost["certain"] is False,
           f"the host could not attribute a change and still claimed certainty: {lost['events']}")
    expect(events_of(lost, "RESYNC_REQUIRED"), "losing certainty was not journalled")
    epoch = lost["epoch"]

    # A later ordinary, perfectly attributable mutation must not restore trust.
    rv.call("object.create", {"kind": "cube", "name": "Innocent"})
    still = rv.result("scene.changes_since", {"cursor": cursor})
    expect(still["certain"] is False,
           "a clean-looking change after uncertainty silently restored trust; it must be sticky")
    expect(still["epoch"] == epoch, "the epoch moved without an authoritative snapshot")

    # Only an authoritative read may open a new epoch.
    snapshot = rv.result("scene.snapshot")
    expect(snapshot["journal"]["opened_new_epoch"] is True,
           "an authoritative snapshot did not open a new epoch after uncertainty")
    expect(snapshot["journal"]["certain"] is True,
           "an authoritative snapshot did not restore certainty")
    expect(snapshot["journal"]["epoch"] == epoch + 1,
           f"the epoch did not advance: {snapshot['journal']['epoch']}")

    # A cursor from the superseded epoch is refused rather than answered.
    rv.call("scene.changes_since", {"cursor": cursor}, ok=False, code="EPOCH_SUPERSEDED")


def a_noop_mutation_does_not_move_the_revision(rv: Host) -> None:
    """A command that ran is not the same as a scene that changed.

    Scene revision names authoritative scene state. Advancing it per successful
    command made it a command counter, and it then disagreed with the journal,
    which correctly had nothing to report.
    """
    clean_scene()
    target = rv.result("object.create", {"kind": "cube", "name": "Static",
                                         "location": [2.0, 0.0, 0.0]})["id"]
    rv.result("scene.snapshot")
    cursor = cursor_of(rv)
    revision = rv.result("scene.describe")["revision"]

    same = rv.call("object.transform", {"object": target, "location": [2.0, 0.0, 0.0]})
    expect(same["outcome"] == "noop", f"a transform that changed nothing reported {same.get('outcome')}")
    expect(same["revision"] == revision,
           f"a no-op advanced the scene revision from {revision} to {same['revision']}")
    expect(rv.result("scene.changes_since", {"cursor": cursor})["events"] == [],
           "a no-op produced journal events")

    moved = rv.call("object.transform", {"object": target, "location": [5.0, 0.0, 0.0]})
    expect(moved["outcome"] == "applied", f"a real transform reported {moved.get('outcome')}")
    expect(moved["revision"] == revision + 1,
           f"a real change did not advance the revision: {moved['revision']}")
    expect(len(rv.result("scene.changes_since", {"cursor": cursor})["events"]) == 1,
           "a real change did not produce exactly one event")

    # Two other shapes of the same thing: a setting written to the value it
    # already holds, and clearing a parent that was never set.
    rv.result("modifier.add", {"object": target, "type": "SUBSURF", "name": "Sub"})
    revision = rv.result("scene.describe")["revision"]
    unchanged = rv.call("modifier.set", {"object": target, "modifier": "Sub",
                                         "properties": {"levels": 1}})
    expect(unchanged["outcome"] == "noop", f"an unchanged modifier reported {unchanged.get('outcome')}")
    expect(unchanged["revision"] == revision, "an unchanged modifier advanced the revision")

    unparent = rv.call("object.parent", {"object": target, "parent": None})
    expect(unparent["outcome"] == "noop", f"clearing an absent parent reported {unparent.get('outcome')}")
    expect(unparent["revision"] == revision, "clearing an absent parent advanced the revision")

    # A rejected command must not move it either.
    rv.call("object.transform", {"object": "b3d:not-a-real-object", "location": [0.0, 0.0, 0.0]},
            ok=False)
    expect(rv.result("scene.describe")["revision"] == revision,
           "a rejected command advanced the scene revision")


SCENARIOS = (
    agent_changes_are_attributed,
    sequence_is_monotonic,
    editor_changes_are_attributed,
    topology_changes_are_distinguished,
    a_missed_notification_is_discovered_at_resync,
    an_authoritative_snapshot_rebuilds_a_wrong_baseline,
    uncertainty_is_sticky,
    a_noop_mutation_does_not_move_the_revision,
)
