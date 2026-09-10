"""Every model-requested mutation goes out under the strict autonomous contract.

Artist Loop v0 opened a fully pinned autonomous transaction and then sent the
correction's individual operations through plain low-level calls. The pins were
therefore checked once, at begin. A world reload, a unit-convention change or a
foreign edit landing between the second and third mutation of a correction would
have been executed straight through, and the driver would have committed a
candidate measured against a scene that no longer existed.

This gate pins the v0.1 behaviour. Two claims, and the second is the one that
matters:

- a correction of several mutations travels one operation at a time, each with
  its own `expected_world`, `expected_coordinate_contract`, current
  `if_revision`, a persisted `idempotency_key` and an `attempt`;
- a stale pin refuses **before the next mutation lands**. Each refusal below is
  followed by a fingerprint comparison, because an error that arrives after the
  side effect is not a refusal, it is a report.

The redelivery case is the reason keys are written to disk before the request
rather than after the reply: the host recognises the second delivery of a lost
acknowledgement and replays it instead of authoring it twice.
"""
from __future__ import annotations

import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _harness import Host, artifact_dir, clean_scene, expect, run_gate  # noqa: E402
from robovision.artist_loop_delivery import Delivery, Ledger, operation_key  # noqa: E402
from robovision.errors import RoboVisionError  # noqa: E402
from robovision.protocol import PROTOCOL_VERSION  # noqa: E402

FINDINGS: dict[str, object] = {}


class Wire:
    """A session-shaped adapter over in-process dispatch.

    `Delivery` is written against `HostSession.call`, and the whole point of this
    gate is to exercise the real one, so the adapter does nothing but carry the
    envelope fields through and raise the way the client does. Anything it
    smoothed over would be a guarantee the gate stopped testing.
    """

    def __init__(self, rv: Host) -> None:
        self.rv = rv
        self.sent: list[dict] = []
        self.serial = 0

    def call(self, method, params=None, **fields):
        self.serial += 1
        request = {"rv": PROTOCOL_VERSION, "id": f"wire-{self.serial}",
                   "method": method, "params": params or {}}
        request.update({k: v for k, v in fields.items() if v is not None})
        self.sent.append(request)
        response = self.rv.runtime.dispatch(request)
        if not response.get("ok"):
            error = response.get("error") or {}
            raise RoboVisionError(error.get("code", "HOST_ERROR"),
                                  error.get("message", "host error"),
                                  data=error.get("data"))
        return response


def pins(rv: Host) -> tuple[str, str]:
    identity = rv.result("system.health")["identity"]
    return identity["world_incarnation"], identity["coordinate_contract"]


def refuses(delivery: Delivery, wire: Wire, rv: Host, index: int, code: str,
            what: str) -> None:
    """The refusal, and the proof that nothing was authored by it."""
    before = rv.fingerprint()
    try:
        delivery.send(index, "object.transform",
                      {"object": "Alpha", "location": [9.0, 9.0, 9.0]})
    except RoboVisionError as exc:
        expect(exc.payload.code == code,
               f"{what}: expected {code}, got {exc.payload.code}")
    else:
        raise AssertionError(f"{what}: the mutation was accepted with a stale pin")
    expect(rv.fingerprint() == before,
           f"{what}: the scene changed despite the refusal")
    FINDINGS[what] = code
    del wire


def main() -> None:
    rv = Host("delivery")
    clean_scene()
    root = artifact_dir("artist-loop-delivery")
    wire = Wire(rv)
    world, contract = pins(rv)
    revision = int(rv.call("system.hello")["revision"])

    ledger = Ledger(Path(root))
    if ledger.path.is_file():
        ledger.path.unlink()
    delivery = Delivery(wire, ledger, world=world, contract=contract,
                        revision=revision, attempt_id="attempt:gate01")

    # --- a correction of several mutations, delivered one pin at a time -----
    operations = [
        ("object.create", {"kind": "cube", "name": "Alpha", "size": 1.0}),
        ("object.create", {"kind": "cube", "name": "Beta", "size": 1.0}),
        ("object.transform", {"object": "Beta", "location": [2.0, 0.0, 0.0]}),
    ]
    revisions = []
    for index, (method, params) in enumerate(operations):
        record = delivery.send(index, method, params)
        expect(record["ok"] is True, f"{method} failed")
        expect(record["idempotency_key"] == operation_key("attempt:gate01", index),
               f"{method} was delivered without its own idempotency identity")
        expect(record["pins"]["contract"] == "autonomous",
               f"{method} did not travel under the autonomous contract")
        expect(record["pins"]["expected_world"] == world
               and record["pins"]["expected_coordinate_contract"] == contract,
               f"{method} was not pinned to the world it was planned in")
        revisions.append(record["revision"])

    expect(len(set(revisions)) == len(revisions),
           "the revision did not advance between mutations, so `if_revision` "
           f"pinned nothing: {revisions}")
    # Each request pinned the revision the previous one reported, which is the
    # part a driver cannot fake by counting.
    sent = [entry for entry in wire.sent if entry["method"] in
            ("object.create", "object.transform")]
    expect([entry["if_revision"] for entry in sent[1:]] == revisions[:-1],
           "a mutation was pinned to a revision it did not observe")

    written = ledger.entries()
    expect(len(written) == 3, f"expected 3 reserved identities, got {len(written)}")
    expect(len({entry["idempotency_key"] for entry in written}) == 3,
           "two logical mutations shared one idempotency key")
    expect(all(entry["attempt"] == 1 for entry in written),
           "a first delivery was recorded as a retry")
    FINDINGS["strict_deliveries"] = len(written)

    # --- a stale pin refuses before the next mutation lands ----------------
    good_world, good_contract, good_revision = (
        delivery.world, delivery.contract, delivery.revision)

    delivery.revision = good_revision - 1
    refuses(delivery, wire, rv, 3, "STALE_REVISION", "stale_revision")
    delivery.revision = good_revision

    delivery.world = "rvworld:not-this-one"
    refuses(delivery, wire, rv, 4, "STALE_WORLD", "stale_world")
    delivery.world = good_world

    delivery.contract = "rvcoord:not-this-one"
    refuses(delivery, wire, rv, 5, "COORDINATE_CONTRACT_CHANGED", "stale_contract")
    delivery.contract = good_contract

    # --- a lost acknowledgement is redelivered, not reapplied ---------------
    # The same logical operation: same index, same recipe, same key, one higher
    # attempt. The host resolves the duplicate from the identity that was on
    # disk before the first delivery went out.
    before = rv.fingerprint()
    replay = delivery.send(2, *operations[2], attempt=2)
    expect(replay["idempotency_key"] == operation_key("attempt:gate01", 2),
           "a retry invented a new identity instead of reusing its own")
    expect(rv.fingerprint() == before,
           "the redelivered mutation was applied a second time")
    expect(replay["replayed"] is True,
           f"the host did not recognise the redelivery: {replay}")
    FINDINGS["redelivery"] = {"replayed": replay["replayed"],
                              "outcome": replay["outcome"]}

    retries = [entry for entry in ledger.entries() if entry["attempt"] == 2]
    expect(len(retries) == 1 and retries[0]["idempotency_key"]
           == operation_key("attempt:gate01", 2),
           "the retry was not recorded under the identity it reused")

    rv.write_trace(Path(root) / "trace.json", findings=FINDINGS)


if __name__ == "__main__":
    run_gate("ARTIST_LOOP_DELIVERY", main, "artist-loop-delivery")
