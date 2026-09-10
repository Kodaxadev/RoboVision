"""Read consistency: what a response's revision and cursor are allowed to mean.

The missed-notification fix reached mutations and `scene.snapshot`, but an
ordinary read still began from the dirty flag alone. Measured before this
module existed: with a notification missed, `scene.describe` returned the
object at its new position — x = 12.0, genuinely current — stamped with the
scene revision and journal cursor of the state *before* it, and journalled
nothing. Either half being stale would be survivable. The pair is not: it
tells a client that the state it is looking at is already accounted for.

So consistency is a declared property of each tool rather than something a
handler remembers to arrange, and the response says which one it got. The three
classes are genuinely different and the tests below prove the difference rather
than assuming it — a policy that reconciled everywhere would pass an
"authoritative reads are fresh" test while destroying the point of cheap
polling.

Run through `journal_gate.py`; this module only supplies scenarios.
"""
from __future__ import annotations

import bpy

from _harness import Host, clean_scene, cursor_of, expect, missed_notification

# Every tool that is deliberately not authoritative, and why it is allowed to be.
NOT_AUTHORITATIVE = {
    # Reports the host's position; it must not be the thing that establishes it,
    # and a client polls it in a loop.
    "scene.changes_since": "notified",
    # Report the revision and journal position without reporting scene state, so
    # they cannot pair current geometry with a stale revision.
    "system.ping": "notified",
    "system.hello": "notified",
    # Reports what became of a transaction, which is host state rather than
    # scene state. Cheap on purpose: resolving a lost acknowledgement must not
    # cost an authoritative read, and must never be the thing that changes what
    # it is reporting on.
    "transaction.status": "notified",
    # Protocol metadata: the catalog does not depend on the scene at all.
    "system.capabilities": "independent",
    "system.method": "independent",
    # Arithmetic over two certificates the caller already holds. It must not
    # touch the scene: a comparison that re-read anything would be measuring a
    # third moment while claiming to compare two, which is the one way this
    # operation could produce a confident wrong answer.
    "truth.compare": "independent",
}


def read_consistency_is_declared_for_every_tool(rv: Host) -> None:
    """The classification is auditable, and drifting is deliberate.

    A new tool defaults to authoritative — correct and slow rather than fast and
    wrong — so the only way into the cheap classes is to name it here as well as
    at its registration.
    """
    catalog = rv.result("system.capabilities", {"limit": 500})["methods"]
    expect(catalog, "the capability catalog is empty")

    declared = {}
    for entry in catalog:
        expect("reads" in entry, f"{entry['name']} does not publish its read consistency")
        expect(
            entry["reads"] in ("authoritative", "notified", "independent"),
            f"{entry['name']} declares an unknown read consistency: {entry['reads']}",
        )
        declared[entry["name"]] = entry

    cheap = {name: spec["reads"] for name, spec in declared.items()
             if spec["reads"] != "authoritative"}
    expect(
        cheap == NOT_AUTHORITATIVE,
        f"the set of non-authoritative tools changed without review: {cheap} != {NOT_AUTHORITATIVE}",
    )
    for name, spec in declared.items():
        if spec["mutating"]:
            expect(
                spec["reads"] == "authoritative",
                f"{name} mutates but does not re-read first: {spec['reads']}",
            )

    # `evidence` is a different axis and must not be mistaken for this one: it
    # says a call produces a durable artifact, not that it re-reads the scene.
    evidence_tools = {name for name, spec in declared.items() if spec["evidence"]}
    authoritative = {name for name, spec in declared.items() if spec["reads"] == "authoritative"}
    expect(
        evidence_tools < authoritative,
        f"evidence tools are a strict subset of authoritative ones; got {evidence_tools}",
    )


def a_state_bearing_read_reconciles(rv: Host) -> None:
    clean_scene()
    target = rv.result("object.create", {"kind": "cube", "name": "Read"})["id"]
    rv.result("scene.snapshot")
    cursor = cursor_of(rv)
    revision_before = rv.result("scene.describe")["revision"]

    with missed_notification():
        bpy.data.objects["Read"].location.x = 12.0

    expect(rv.runtime._dirty is False, "precondition: the host must not have been told")

    response = rv.call("scene.describe", {"level": "deep"})
    described = response["result"]
    expect(response["consistency"] == "authoritative",
           f"a state-bearing read reported consistency {response.get('consistency')}")

    entry = next(item for item in described["objects"] if item["id"] == target)
    expect(entry["matrix_world"][0][3] == 12.0,
           f"the read did not return current state: {entry['matrix_world'][0][3]}")
    expect(
        described["revision"] != revision_before,
        "current state was returned stamped with the revision of the state before it",
    )
    changes = rv.result("scene.changes_since", {"cursor": cursor})
    named = [event for event in changes["events"] if target in event["ids"]]
    expect(named, f"the read found the change but journalled nothing: {changes['events']}")
    expect(named[0]["source"] == "editor",
           f"a change the agent did not make was attributed to {named[0]['source']}")
    expect(
        named[0]["revision"] == described["revision"],
        "the journal and the response disagree about which revision the change produced",
    )


def journal_polling_stays_cheap(rv: Host) -> None:
    """The cheap class has to actually be cheap, or the policy is decoration.

    Reconciling here would make every poll a deep read and would make polling
    itself the thing that discovers changes — which is precisely the confusion
    between reporting a position and establishing one.
    """
    clean_scene()
    rv.result("object.create", {"kind": "cube", "name": "Polled"})
    rv.result("scene.snapshot")
    cursor = cursor_of(rv)
    revision_before = rv.result("scene.describe")["revision"]

    with missed_notification():
        bpy.data.objects["Polled"].location.y = 3.0

    polled = rv.call("scene.changes_since", {"cursor": cursor})
    expect(polled["consistency"] == "notified",
           f"journal polling reported consistency {polled.get('consistency')}")
    expect(polled["result"]["events"] == [],
           "polling the journal performed an authoritative read and discovered the change")
    expect(polled["revision"] == revision_before,
           "polling the journal advanced the scene revision")

    # And the same for pure metadata, which must not touch the scene at all.
    metadata = rv.call("system.capabilities", {"limit": 1})
    expect(metadata["consistency"] == "independent",
           f"the capability catalog reported consistency {metadata.get('consistency')}")
    expect(metadata["revision"] == revision_before, "reading the catalog advanced the revision")

    # The change is still there, and the next state-bearing read must find it.
    rv.call("scene.describe")
    caught = rv.result("scene.changes_since", {"cursor": cursor})
    expect(caught["events"], "no read ever discovered the change")


def bookkeeping_properties_stay_out_of_authored_state(rv: Host) -> None:
    """RoboVision's own storage must not read as something a user authored.

    The identity and topology properties live on the datablock, so without the
    exclusion a snapshot would list them as authored custom properties and the
    host's own bookkeeping would look like a scene edit. What is excluded is the
    *storage*: the values still reach the fingerprint through the canonical
    fields, deliberately, because an object whose identity changed is not the
    same object.
    """
    clean_scene()
    rv.result("object.create", {"kind": "cube", "name": "Bookkeeping"})
    snapshot = rv.result("scene.snapshot")
    entry = snapshot["state"]["objects"][0]

    obj = bpy.data.objects["Bookkeeping"]
    stored = sorted(key for key in obj.keys() if key.startswith("_robovision"))
    stored += sorted(key for key in obj.data.keys() if key.startswith("_robovision"))
    expect(stored, "precondition: the host stores its bookkeeping on the datablock")
    expect(
        not any(key.startswith("_robovision") for key in (entry.get("custom_properties") or {})),
        f"control-plane storage leaked into authored state: {entry.get('custom_properties')}",
    )

    # The values themselves are canonical fields and do belong to the identity
    # of the object, so they are not hidden — only their storage is.
    expect(entry["id"].startswith("b3d:"), "the object identity is not reported canonically")
    expect("revision" in (entry.get("mesh") or {}), "the mesh revision is not reported canonically")

    # Writing a bookkeeping property to the value it already holds is a genuine
    # no-op and must not look like an edit.
    revision = rv.result("scene.describe")["revision"]
    cursor = cursor_of(rv)
    obj["_robovision_id"] = obj["_robovision_id"]
    bpy.context.view_layer.update()
    expect(rv.result("scene.describe")["revision"] == revision,
           "rewriting the host's own bookkeeping looked like a scene change")
    expect(rv.result("scene.changes_since", {"cursor": cursor})["events"] == [],
           "rewriting the host's own bookkeeping produced journal events")

    # An authored property, by contrast, is authored state.
    obj["author_note"] = "hello"
    bpy.context.view_layer.update()
    expect(rv.result("scene.describe")["revision"] != revision,
           "an authored custom property did not register as a change")


def every_response_carries_the_envelope_fields(rv: Host) -> None:
    """The envelope is the same shape whether the call worked or not.

    PROTOCOL.md said every response carries `state_domain` and `consistency`.
    Measured before this scenario existed: no error response carried either, on
    either host — the contract was true of the success path and written as if it
    were true of all of them. An audit rather than a spot check, because the
    failure mode is a field that quietly exists in only half the answers.
    """
    clean_scene()
    created = rv.result("object.create", {"kind": "cube", "name": "Envelope"})["id"]
    probes = [
        ("scene.describe", None, None),
        ("scene.changes_since", None, None),
        ("system.capabilities", None, None),
        ("nope.nope", None, None),
        ("object.transform", {"object": ""}, None),
        ("object.transform", {"object": created, "location": [1, 0, 0]}, 0),
        ("transaction.commit", {"transaction": "rvtx:nope:nope"}, None),
        ("transaction.adopt", {"transaction": "rvtx:nope:nope", "recovery_token": "nope"}, None),
    ]
    for method, params, if_revision in probes:
        response = rv.runtime.dispatch({
            "rv": "1.0",
            "id": f"envelope-{method}",
            "method": method,
            "params": params or {},
            **({"if_revision": if_revision} if if_revision is not None else {}),
        })
        expect(bool(response.get("state_domain")),
               f"{method} answered without saying which universe it read: {response}")
        expect(bool(response.get("consistency")),
               f"{method} answered without saying what its revision is worth: {response}")

    # A failure before any method resolves has no class to report, and says so
    # rather than claiming the strongest one.
    malformed = rv.runtime.dispatch({"rv": "1.0", "id": "", "method": "system.ping", "params": {}})
    expect(malformed.get("ok") is False, f"a malformed request succeeded: {malformed}")
    expect(malformed.get("consistency") == "unknown",
           f"a failure with no resolved tool claimed a consistency class: {malformed}")
    expect(bool(malformed.get("state_domain")), f"no state domain on a protocol failure: {malformed}")


SCENARIOS = (
    every_response_carries_the_envelope_fields,
    read_consistency_is_declared_for_every_tool,
    a_state_bearing_read_reconciles,
    journal_polling_stays_cheap,
    bookkeeping_properties_stay_out_of_authored_state,
)
