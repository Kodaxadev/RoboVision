"""What a journal cursor must refuse to mean.

A cursor is a claim about a position in a history. Sequence numbers restart at
every new certainty epoch and at every new document, so the same integer names
different moments in different worlds. A cursor that carries only the integer is
therefore ambiguous, and the ambiguity resolves the worst possible way: as an
empty event list, which a client reads as "nothing changed".

Every scenario here establishes that the collision is real before asserting the
refusal, so a passing run cannot be an accident of numbering.

Run through `journal_gate.py`; this module only supplies scenarios.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import bpy

from _harness import Host, clean_scene, cursor_of, events_of, expect, missed_notification

WORK = Path(tempfile.gettempdir()) / "robovision-journal-gate"


def a_bootstrap_claims_no_history(rv: Host) -> None:
    clean_scene()
    rv.result("scene.snapshot")
    rv.result("object.create", {"kind": "cube", "name": "Preexisting"})

    start = rv.result("scene.changes_since")
    expect(start["bootstrap"] is True, "a cursorless read did not report itself as a bootstrap")
    expect(start["events"] == [], "a bootstrap handed back history it cannot prove is complete")
    expect(start["cursor"].startswith("rvcursor:"), f"no cursor was issued: {start['cursor']}")

    # And the cursor it issued is immediately usable.
    expect(rv.result("scene.changes_since", {"cursor": start["cursor"]})["events"] == [],
           "the cursor a bootstrap issued did not point at the present")


def a_superseded_epoch_cursor_is_refused(rv: Host) -> None:
    """The ambiguity a bare sequence number creates.

    Sequence restarts at every new epoch, so an old, larger number read against
    a fresh, smaller one looks like a client that is already up to date. The
    answer was an empty event list while real changes sat unreported.
    """
    clean_scene()
    rv.result("scene.snapshot")
    for index in range(5):
        rv.call("object.create", {"kind": "cube", "name": f"Old{index}"})
    stale = cursor_of(rv)
    stale_sequence = int(stale.split(":")[-1])

    # Lose certainty the honest way, then resync to open the next epoch.
    with missed_notification():
        bpy.context.scene.frame_current = 7
    rv.call("object.create", {"kind": "cube", "name": "Trigger"})
    opened = rv.result("scene.snapshot")
    expect(opened["journal"]["opened_new_epoch"] is True, "the epoch did not advance")

    rv.call("object.create", {"kind": "cube", "name": "BrandNew"})
    current = rv.result("scene.changes_since")
    expect(
        current["sequence"] < stale_sequence,
        f"precondition: the new epoch's sequence ({current['sequence']}) must be behind the "
        f"old one ({stale_sequence}) for this to be the dangerous case",
    )

    refused = rv.call("scene.changes_since", {"cursor": stale}, ok=False, code="EPOCH_SUPERSEDED")
    expect(
        refused["error"]["data"].get("current_cursor"),
        "the refusal did not say where the client should resume from",
    )


def a_replaced_document_cursor_is_refused(rv: Host) -> None:
    """Epoch numbers collide across documents; both start at 1.

    A cursor carrying only a sequence and an epoch therefore validates cleanly
    against a document it has nothing to do with.
    """
    WORK.mkdir(parents=True, exist_ok=True)
    document = WORK / "cursor.blend"

    clean_scene()
    rv.result("object.create", {"kind": "cube", "name": "Resident"})
    bpy.ops.wm.save_as_mainfile(filepath=str(document))
    # Load once so the journal sits at a freshly reset epoch. Earlier scenarios
    # may have advanced it, and the case worth proving is the ordinary one where
    # neither side ever lost certainty.
    bpy.ops.wm.open_mainfile(filepath=str(document))
    for index in range(4):
        rv.call("object.create", {"kind": "cube", "name": f"Before{index}"})
    stale = cursor_of(rv)
    stale_epoch = rv.runtime.journal.epoch
    expect(stale_epoch == 1, f"precondition: the first document should sit at epoch 1, got {stale_epoch}")

    bpy.ops.wm.open_mainfile(filepath=str(document))
    expect(
        rv.runtime.journal.epoch == stale_epoch,
        "precondition: both sides must sit at the same epoch for this to be the dangerous "
        f"case, got {stale_epoch} then {rv.runtime.journal.epoch}",
    )

    # Two real changes in the new document, which the stale cursor must not hide.
    rv.call("object.create", {"kind": "cube", "name": "New1"})
    rv.call("object.create", {"kind": "cube", "name": "New2"})

    refused = rv.call("scene.changes_since", {"cursor": stale}, ok=False, code="STALE_DOCUMENT")
    data = refused["error"]["data"]
    expect(
        data.get("current_document_incarnation")
        == rv.result("scene.describe")["document_incarnation"],
        f"the refusal did not name the document that is actually loaded: {refused['error']}",
    )
    # The cursor it offers instead works, so the refusal is about identity
    # rather than the journal being broken.
    expect(rv.result("scene.changes_since", {"cursor": data["current_cursor"]})["events"] == [],
           "the cursor offered by the refusal did not point at the present")


def impossible_cursors_are_refused(rv: Host) -> None:
    clean_scene()
    rv.result("scene.snapshot")
    prefix, document, epoch, sequence = rv.result("scene.changes_since")["cursor"].split(":")

    # Same document, same epoch, ahead of everything that has happened: nothing
    # this host issued could say that.
    ahead = f"{prefix}:{document}:{epoch}:{int(sequence) + 500}"
    rv.call("scene.changes_since", {"cursor": ahead}, ok=False, code="INVALID_PARAMS")

    for malformed in ("", "nonsense", "rvcursor:a:b", "rvcursor:a:b:c", f"{prefix}:{document}:0:1"):
        rv.call("scene.changes_since", {"cursor": malformed}, ok=False, code="INVALID_PARAMS")

    # The parameters this replaced must be refused, not quietly ignored: a
    # client still sending `after` would otherwise be served an answer to a
    # question it did not ask.
    rejected = rv.call("scene.changes_since", {"after": 0}, ok=False, code="INVALID_PARAMS")
    expect(rejected["error"]["data"].get("current_cursor"),
           "the refusal did not offer the cursor to use instead")
    rv.call("scene.changes_since", {"epoch": 1}, ok=False, code="INVALID_PARAMS")


def forgotten_history_is_refused(rv: Host) -> None:
    clean_scene()
    rv.result("scene.snapshot")
    from robovision_blender.journal import RETAINED_EVENTS

    aged = cursor_of(rv)

    # Overrun the retention window so the earliest events are genuinely gone.
    for index in range(RETAINED_EVENTS // 2 + 8):
        target = rv.result("object.create", {"kind": "cube", "name": f"Churn{index}"})["id"]
        rv.call("object.delete", {"object": target})

    # A cursor issued before the window slid past is refused, not answered with
    # the fragment that survived.
    refused = rv.call("scene.changes_since", {"cursor": aged}, ok=False, code="SEQUENCE_TOO_OLD")
    expect(refused["error"]["data"].get("current_cursor"),
           "the refusal did not say where to resume from")

    # A current cursor still works, and so does one exactly at the retention
    # edge: the boundary must not swallow a client that is only just keeping up.
    head = rv.result("scene.changes_since")["cursor"]
    expect(rv.result("scene.changes_since", {"cursor": head})["events"] == [],
           "asking from the end should be empty")

    oldest = rv.runtime.journal._events[0]["sequence"]
    prefix, document, epoch, _ = head.split(":")
    served = rv.result("scene.changes_since", {"cursor": f"{prefix}:{document}:{epoch}:{oldest - 1}"})
    expect(len(served["events"]) == RETAINED_EVENTS,
           f"the retention edge served {len(served['events'])} of {RETAINED_EVENTS} events")


def a_document_load_resets_the_journal(rv: Host) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    document = WORK / "journal.blend"

    clean_scene()
    rv.result("object.create", {"kind": "cube", "name": "Persisted"})
    bpy.ops.wm.save_as_mainfile(filepath=str(document))
    before = rv.result("system.hello")["journal"]
    rv.call("object.create", {"kind": "cube", "name": "Churned"})
    advanced = rv.result("system.hello")["journal"]["sequence"]
    expect(advanced > 0, "the journal recorded nothing before the load")

    bpy.ops.wm.open_mainfile(filepath=str(document))

    reset = rv.result("scene.changes_since")
    expect(reset["epoch"] == 1, f"the journal epoch did not reset on document load: {reset['epoch']}")
    expect(reset["certain"] is True, "the journal did not start the new document certain")
    expect(
        reset["document_incarnation"] == rv.result("scene.describe")["document_incarnation"],
        "the journal is not bound to the current document incarnation",
    )
    expect(reset["sequence"] <= advanced, "the sequence did not restart with the new document")
    expect(before["epoch"] >= 1, "precondition: the journal had an epoch before the load")

    # The load itself is journalled, and readable from the start of the new
    # document's history.
    prefix, incarnation, epoch, _ = reset["cursor"].split(":")
    from_start = rv.result("scene.changes_since", {"cursor": f"{prefix}:{incarnation}:{epoch}:0"})
    expect(events_of(from_start, "DOCUMENT_OPENED"),
           f"the load itself was not journalled: {from_start['events']}")


SCENARIOS = (
    a_bootstrap_claims_no_history,
    a_superseded_epoch_cursor_is_refused,
    a_replaced_document_cursor_is_refused,
    impossible_cursors_are_refused,
    forgotten_history_is_refused,
    a_document_load_resets_the_journal,
)
