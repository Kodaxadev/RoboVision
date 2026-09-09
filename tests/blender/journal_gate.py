"""The change journal, including everything it must refuse to answer.

A journal is only useful if an agent can trust its silence as much as its
events. These assert both: that real changes appear with the right attribution,
and that when the host cannot account for something it says so, keeps saying so,
and refuses to serve a history it no longer has.

Headless: nothing here needs a viewport.
"""
from __future__ import annotations

import sys
from pathlib import Path

import bmesh
import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import Host, artifact_dir, clean_scene, expect, run_gate  # noqa: E402


def events_of(response: dict, event_type: str) -> list[dict]:
    return [event for event in response["events"] if event["type"] == event_type]


def scenario_agent_changes_are_attributed(rv: Host) -> None:
    clean_scene()
    rv.result("scene.snapshot")
    before = rv.result("scene.changes_since", {"after": 0})
    baseline = before["sequence"]

    created = rv.result("object.create", {"kind": "cube", "name": "Journalled"})
    changes = rv.result("scene.changes_since", {"after": baseline})

    creations = events_of(changes, "OBJECT_CREATED")
    expect(len(creations) == 1, f"a create produced {len(creations)} create events")
    event = creations[0]
    expect(event["ids"] == [created["id"]], f"the event named {event['ids']}")
    expect(event["source"] == "agent", f"the agent's own mutation was attributed to {event['source']}")
    expect(event.get("request"), "an agent event carried no request id")
    expect(event["sequence"] > baseline, "the sequence did not advance")
    expect(changes["certain"] is True, "an ordinary mutation cost the journal its certainty")


def scenario_sequence_is_monotonic(rv: Host) -> None:
    clean_scene()
    rv.result("scene.snapshot")
    start = rv.result("scene.changes_since", {"after": 0})["sequence"]

    ids = [rv.result("object.create", {"kind": "cube", "name": f"Seq{index}"})["id"] for index in range(4)]
    rv.call("object.transform", {"object": ids[0], "local_position": [1.0, 0.0, 0.0]}
            if False else {"object": ids[0], "location": [1.0, 0.0, 0.0]})
    rv.call("object.delete", {"object": ids[3]})

    changes = rv.result("scene.changes_since", {"after": start})
    sequences = [event["sequence"] for event in changes["events"]]
    expect(sequences == sorted(sequences), f"sequences arrived out of order: {sequences}")
    expect(len(set(sequences)) == len(sequences), "a sequence number was reused")
    expect(all(a + 1 == b for a, b in zip(sequences, sequences[1:])), f"sequences have gaps: {sequences}")

    kinds = {event["type"] for event in changes["events"]}
    expect("OBJECT_CREATED" in kinds and "OBJECT_DELETED" in kinds,
           f"create and delete were not both journalled: {kinds}")

    # Asking again from the same point is stable.
    again = rv.result("scene.changes_since", {"after": start})
    expect([event["sequence"] for event in again["events"]] == sequences,
           "re-reading the same range returned different events")
    # And asking from the end returns nothing.
    tail = rv.result("scene.changes_since", {"after": changes["sequence"]})
    expect(tail["events"] == [], f"asking past the end returned {len(tail['events'])} events")


def scenario_editor_changes_are_attributed(rv: Host) -> None:
    clean_scene()
    target = rv.result("object.create", {"kind": "cube", "name": "Bystander"})["id"]
    rv.result("scene.snapshot")
    start = rv.result("scene.changes_since", {"after": 0})["sequence"]

    # A change the host did not make, of the kind a human makes.
    bpy.data.objects["Bystander"].location.x = 4.0
    bpy.context.view_layer.update()
    rv.call("scene.describe")  # let the host notice

    changes = rv.result("scene.changes_since", {"after": start})
    attributed = [event for event in changes["events"] if event["source"] == "editor"]
    expect(attributed, f"an external edit produced no editor-attributed event: {changes['events']}")
    expect(
        any(target in event["ids"] for event in attributed),
        f"the editor event did not name the object that moved: {attributed}",
    )
    expect(changes["certain"] is True, "an attributable external edit should not cost certainty")


def scenario_topology_changes_are_distinguished(rv: Host) -> None:
    clean_scene()
    target = rv.result("object.create", {"kind": "cube", "name": "Topo"})["id"]
    revision = rv.result("mesh.inspect", {"object": target})["mesh_revision"]
    rv.result("scene.snapshot")
    start = rv.result("scene.changes_since", {"after": 0})["sequence"]

    rv.call("mesh.subdivide_edges", {
        "object": target, "edge_indices": [0, 1], "expected_mesh_revision": revision,
    })

    changes = rv.result("scene.changes_since", {"after": start})
    topology = events_of(changes, "TOPOLOGY_CHANGED")
    expect(topology, f"a topology edit was not distinguished from a plain change: {changes['events']}")
    expect(target in topology[0]["ids"], "the topology event named the wrong object")


def scenario_uncertainty_is_sticky(rv: Host) -> None:
    clean_scene()
    rv.result("object.create", {"kind": "cube", "name": "Anchor"})
    rv.result("scene.snapshot")
    expect(rv.result("scene.changes_since", {"after": 0})["certain"] is True,
           "the journal did not start certain")

    # Simulate the real hazard: the host observes that something changed but has
    # no prior snapshot to attribute it against, which is what a missed
    # notification looks like from the inside.
    rv.runtime._last_snapshot = None
    bpy.data.objects["Anchor"].location.y = 3.0
    bpy.context.view_layer.update()
    rv.call("scene.describe")

    lost = rv.result("scene.changes_since", {"after": 0})
    expect(lost["certain"] is False, "the host could not attribute a change and still claimed certainty")
    expect(events_of(lost, "RESYNC_REQUIRED"), "losing certainty was not journalled")
    epoch = lost["epoch"]

    # A later ordinary, perfectly attributable mutation must not restore trust.
    rv.call("object.create", {"kind": "cube", "name": "Innocent"})
    still = rv.result("scene.changes_since", {"after": 0})
    expect(still["certain"] is False,
           "a clean-looking change after uncertainty silently restored trust; uncertainty must be sticky")
    expect(still["epoch"] == epoch, "the epoch moved without an authoritative snapshot")

    # Only an authoritative read may open a new epoch.
    snapshot = rv.result("scene.snapshot")
    expect(snapshot["journal"]["opened_new_epoch"] is True,
           "an authoritative snapshot did not open a new epoch after uncertainty")
    restored = rv.result("scene.changes_since", {"after": 0})
    expect(restored["certain"] is True, "an authoritative snapshot did not restore certainty")
    expect(restored["epoch"] == epoch + 1, f"the epoch did not advance: {restored['epoch']}")

    # A sequence from the superseded epoch is refused rather than answered.
    rv.call("scene.changes_since", {"after": 0, "epoch": epoch}, ok=False, code="EPOCH_SUPERSEDED")

    # A second snapshot while certain must not churn the epoch.
    again = rv.result("scene.snapshot")
    expect(again["journal"]["opened_new_epoch"] is False,
           "a snapshot opened a new epoch while the journal was already certain")


def scenario_forgotten_history_is_refused(rv: Host) -> None:
    clean_scene()
    rv.result("scene.snapshot")
    from robovision_blender.journal import RETAINED_EVENTS

    # Overrun the retention window so the earliest events are genuinely gone.
    for index in range(RETAINED_EVENTS // 2 + 8):
        target = rv.result("object.create", {"kind": "cube", "name": f"Churn{index}"})["id"]
        rv.call("object.delete", {"object": target})

    # Read the cursor from a source that does not itself need a valid cursor:
    # once history has been dropped, changes_since(0) is exactly the call that
    # must fail, so it cannot be used to discover where the end is.
    head = rv.result("system.hello")["journal"]["sequence"]
    current = rv.result("scene.changes_since", {"after": head})
    expect(current["events"] == [], "asking from the end should be empty")

    # Sequence 0 is now far behind what the journal still holds.
    rv.call("scene.changes_since", {"after": 0}, ok=False, code="SEQUENCE_TOO_OLD")


def scenario_document_load_resets_the_journal(rv: Host) -> None:
    import tempfile

    work = Path(tempfile.gettempdir()) / "robovision-journal-gate"
    work.mkdir(parents=True, exist_ok=True)
    document = work / "journal.blend"

    clean_scene()
    rv.result("object.create", {"kind": "cube", "name": "Persisted"})
    bpy.ops.wm.save_as_mainfile(filepath=str(document))
    # Read through hello: earlier scenarios may have aged sequence 0 out, and
    # that refusal is correct behaviour rather than something to work around.
    before = rv.result("system.hello")["journal"]
    rv.call("object.create", {"kind": "cube", "name": "Churned"})
    advanced = rv.result("system.hello")["journal"]["sequence"]
    expect(advanced > 0, "the journal recorded nothing before the load")

    bpy.ops.wm.open_mainfile(filepath=str(document))

    reset = rv.result("scene.changes_since", {"after": 0})
    expect(reset["epoch"] == 1, f"the journal epoch did not reset on document load: {reset['epoch']}")
    expect(reset["certain"] is True, "the journal did not start the new document certain")
    expect(
        reset["document_incarnation"] == rv.result("scene.describe")["document_incarnation"],
        "the journal is not bound to the current document incarnation",
    )
    opened = events_of(reset, "DOCUMENT_OPENED")
    expect(opened, f"the load itself was not journalled: {reset['events']}")
    expect(reset["sequence"] <= advanced, "the sequence did not restart with the new document")
    expect(before["epoch"] >= 1, "precondition: the journal had an epoch before the load")


def main() -> None:
    rv = Host("journal")
    scenarios = (
        scenario_agent_changes_are_attributed,
        scenario_sequence_is_monotonic,
        scenario_editor_changes_are_attributed,
        scenario_topology_changes_are_distinguished,
        scenario_uncertainty_is_sticky,
        scenario_forgotten_history_is_refused,
        scenario_document_load_resets_the_journal,
    )
    for scenario in scenarios:
        scenario(rv)
        print(f"  ok {scenario.__name__}", flush=True)

    (artifact_dir("blender-journal") / "scenarios.txt").write_text(
        "\n".join(scenario.__name__ for scenario in scenarios) + "\n", encoding="utf-8"
    )


run_gate("BLENDER_JOURNAL", main, "blender-journal")
