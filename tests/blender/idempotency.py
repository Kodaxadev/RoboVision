"""A lost reply must not become a second bevel.

Measured before any of this existed: a resent `object.create` made two objects, a
resent `object.delete` reported `NOT_FOUND` for an operation that had succeeded,
and a resent absolute transform reported `noop` — which looked like protection
and was only an operation whose second application happened not to move
anything. Three different accidents, none of them a guarantee.

These assert the guarantee, and the boundaries where the host must say it does
not know rather than guess.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bpy  # noqa: E402

from _harness import Host, artifact_dir, clean_scene, expect, run_gate  # noqa: E402
from robovision_blender.idempotency import IdempotencyLedger  # noqa: E402
from robovision_blender.ledger import OperationLedger  # noqa: E402
from robovision_blender.registry import EXACT, SEEDED, HostError  # noqa: E402

SEEN: dict[str, object] = {}


def send(rv: Host, method: str, params: dict, *, key: str | None = None, attempt: int = 1,
         world: str | None = None, request_id: str | None = None) -> dict:
    """Deliver a request the way a retried packet would arrive."""
    raw = {"rv": "1.0", "id": request_id or f"idem-{len(SEEN)}", "method": method, "params": params}
    SEEN[raw["id"]] = True
    if key is not None:
        raw["idempotency_key"] = key
    if attempt != 1:
        raw["attempt"] = attempt
    if world is not None:
        raw["expected_world"] = world
    return rv.runtime.dispatch(raw)


def code(response: dict) -> str | None:
    return (response.get("error") or {}).get("code")


def names() -> list[str]:
    return sorted(obj.name for obj in bpy.data.objects)


def a_lost_reply_does_not_create_twice(rv: Host) -> None:
    clean_scene()
    rv.result("scene.snapshot")
    first = send(rv, "object.create", {"kind": "cube", "name": "Once"}, key="k-create")
    expect(first["ok"] and first["outcome"] == "applied", f"the first create failed: {first}")
    expect(names() == ["Once"], f"the first create made {names()}")

    # The reply never arrived; the client resends the identical request.
    again = send(rv, "object.create", {"kind": "cube", "name": "Once"}, key="k-create", attempt=2)
    expect(again["ok"], f"the retry was refused: {again}")
    expect(again.get("replayed") is True, f"the retry was not reported as a replay: {again}")
    expect(names() == ["Once"], f"a lost reply became a second object: {names()}")
    expect(again["result"]["id"] == first["result"]["id"],
           "the replay returned a different object than the original")


def a_lost_reply_does_not_report_a_delete_as_failed(rv: Host) -> None:
    """The retry that used to be told its successful delete had failed."""
    clean_scene()
    target = rv.result("object.create", {"kind": "cube", "name": "Doomed"})["id"]
    first = send(rv, "object.delete", {"object": target}, key="k-delete")
    expect(first["ok"], f"the delete failed: {first}")
    expect(names() == [], f"the delete left {names()}")

    again = send(rv, "object.delete", {"object": target}, key="k-delete", attempt=2)
    expect(again["ok"], f"a retried delete reported failure for work that succeeded: {again}")
    expect(code(again) is None, f"the retry returned an error: {code(again)}")
    expect(again.get("replayed") is True, "the retry was not reported as a replay")


def an_absolute_transform_replays_rather_than_looking_idempotent(rv: Host) -> None:
    """Not `noop` by luck: `replayed`, because the host knows it is the same call."""
    clean_scene()
    created = rv.result("object.create", {"kind": "cube", "name": "Moved"})["id"]
    first = send(rv, "object.transform", {"object": created, "location": [1, 0, 0]}, key="k-move")
    expect(first["outcome"] == "applied", f"the move did not apply: {first}")
    again = send(rv, "object.transform", {"object": created, "location": [1, 0, 0]},
                 key="k-move", attempt=2)
    expect(again.get("replayed") is True,
           f"the duplicate was answered by re-executing and happening not to move: {again}")
    expect(again.get("outcome") == "applied",
           "the replay reported the second execution's outcome rather than the original's")


def a_different_recipe_under_one_key_is_refused(rv: Host) -> None:
    clean_scene()
    created = rv.result("object.create", {"kind": "cube", "name": "Keyed"})["id"]
    send(rv, "object.transform", {"object": created, "location": [1, 0, 0]}, key="k-mismatch")
    refused = send(rv, "object.transform", {"object": created, "location": [9, 0, 0]},
                   key="k-mismatch", attempt=2)
    expect(code(refused) == "IDEMPOTENCY_MISMATCH",
           f"a key was reused for a different operation and executed: {refused}")
    data = refused["error"]["data"]
    expect(data.get("recorded_recipe") and data.get("requested_recipe"),
           "the mismatch did not say which two computations disagreed")
    expect(data["recorded_recipe"] != data["requested_recipe"],
           "the mismatch reported two identical recipes")


def the_same_recipe_under_a_new_key_executes_again(rv: Host) -> None:
    """Deliberate re-execution must survive. Two candidates, one recipe."""
    clean_scene()
    rv.result("scene.snapshot")
    first = send(rv, "object.create", {"kind": "cube", "name": "Candidate"}, key="k-candidate-a")
    second = send(rv, "object.create", {"kind": "cube", "name": "Candidate"}, key="k-candidate-b")
    expect(first["ok"] and second["ok"], "a deliberate second execution was refused")
    expect(second.get("replayed") is not True,
           "an identical recipe with a fresh key was mistaken for a network duplicate")
    expect(len(names()) == 2, f"the second deliberate execution did not happen: {names()}")


def a_retry_with_no_record_is_indeterminate(rv: Host) -> None:
    """A forgotten key and a never-seen key look the same, so neither executes."""
    clean_scene()
    rv.result("scene.snapshot")
    refused = send(rv, "object.create", {"kind": "cube", "name": "Unknown"},
                   key="k-never-seen", attempt=3)
    expect(code(refused) == "INDETERMINATE",
           f"a retry the host had no record of was executed: {refused}")
    expect(names() == [], f"an indeterminate retry still changed the scene: {names()}")
    expect(refused["error"]["data"].get("remedy"), "INDETERMINATE offered no way forward")


def an_interrupted_operation_is_indeterminate(rv: Host) -> None:
    """An intent with no terminal record is evidence, not an invitation to re-run."""
    clean_scene()
    rv.result("scene.snapshot")
    # The operation really happens, and then its terminal record does not — which
    # is what a crash between the side effect and the result looks like from the
    # outside. Reaching into the record is the only way to produce that here; the
    # alternative is killing Blender mid-dispatch.
    send(rv, "object.create", {"kind": "cube", "name": "Crashed"}, key="k-crashed")
    expect(names() == ["Crashed"], f"the operation did not apply: {names()}")
    rv.runtime.invocations.records["k-crashed"].state = "reserved"
    rv.runtime.invocations.release("k-crashed")

    refused = send(rv, "object.create", {"kind": "cube", "name": "Crashed"},
                   key="k-crashed", attempt=2)
    expect(code(refused) == "INDETERMINATE",
           f"an interrupted operation was silently re-run: {refused}")
    expect(names() == ["Crashed"],
           f"an operation whose outcome was unknown was applied a second time: {names()}")


def a_reserved_key_is_in_progress(rv: Host) -> None:
    """Asserted at the state machine, because a single-threaded host cannot race itself.

    Blender dispatches one request at a time on the main thread, so a second
    delivery cannot arrive *during* the first. The reserved state is still real —
    it is what exists between the durable intent and the result — and this pins
    what it answers, rather than writing a transport test that could only ever
    pass by pretending.
    """
    ledger = IdempotencyLedger()
    ledger.reserve("k-busy", "rvrecipe:x", "rvworld:w", None, 1)
    try:
        ledger.check("k-busy", "rvrecipe:x", 2, "rvworld:w")
    except HostError as exc:
        expect(exc.code == "IN_PROGRESS", f"a reserved key answered {exc.code}")
    else:
        raise AssertionError("a reserved key was answered by executing")


def a_replaced_world_cannot_be_retried_into(rv: Host) -> None:
    clean_scene()
    rv.result("scene.snapshot")
    world = rv.result("scene.describe")["world_incarnation"]
    send(rv, "object.create", {"kind": "cube", "name": "InOldWorld"}, key="k-world", world=world)

    # A different world identity, as a file load produces. The scene itself is
    # untouched here, so what is being asserted is that the retry changed
    # nothing — not that the old object vanished, which it would not.
    rv.runtime.document_opened()
    before = names()
    refused = send(rv, "object.create", {"kind": "cube", "name": "InOldWorld"},
                   key="k-world", attempt=2, world=world)
    expect(code(refused) == "STALE_WORLD",
           f"a retry was reinterpreted in a world it was never planned against: {refused}")
    expect(names() == before,
           f"the stale retry executed in the new world: {before} became {names()}")


def a_rolled_back_operation_is_not_resurrected_by_a_retry(rv: Host) -> None:
    clean_scene()
    rv.result("scene.snapshot")
    tx = rv.result("transaction.begin", {"label": "rolled back"})["transaction"]
    send(rv, "object.create", {"kind": "cube", "name": "Undone"}, key="k-in-tx")
    rv.result("transaction.discard", {"transaction": tx})
    rv.runtime.invocations.note_transaction_outcome(tx, "abandoned")

    again = send(rv, "object.create", {"kind": "cube", "name": "Undone"}, key="k-in-tx", attempt=2)
    expect(again.get("replayed") is True,
           f"a retry after the transaction ended executed as new work: {again}")
    expect(again.get("transaction_outcome") == "abandoned",
           f"the replay did not say what became of the transaction: {again}")


def a_stochastic_tool_refuses_to_invent_its_own_randomness(rv: Host) -> None:
    """The host must never pick a seed. A retry could not describe what happened."""
    clean_scene()
    rv.result("scene.snapshot")

    def scatter(params, runtime):
        rng = random.Random(params["seed"])
        placed = []
        for index in range(3):
            bpy.ops.mesh.primitive_cube_add(location=(rng.random(), rng.random(), 0.0))
            obj = bpy.context.active_object
            obj.name = f"Scatter{index}"
            placed.append([round(value, 6) for value in obj.location])
        return {"placed": placed}

    if "test.scatter" not in [tool["name"] for tool in rv.runtime.registry.capabilities()]:
        rv.runtime.registry.add("test.scatter", scatter, mutating=True,
                                seeds=("seed",), determinism=SEEDED,
                                summary="Test-only stochastic placement.")

    refused = send(rv, "test.scatter", {}, key="k-noseed")
    expect(code(refused) == "SEED_REQUIRED",
           f"a stochastic tool chose its own randomness: {refused}")
    expect(names() == [], "a refused stochastic call still changed the scene")
    expect(refused["error"]["data"]["seed_channels"] == ["seed"],
           "the refusal did not name the randomness channel it needed")

    seeded = send(rv, "test.scatter", {"seed": 7}, key="k-seeded")
    expect(seeded["ok"], f"a seeded call failed: {seeded}")
    replay = send(rv, "test.scatter", {"seed": 7}, key="k-seeded", attempt=2)
    expect(replay.get("replayed") is True, "a seeded retry re-executed")
    expect(replay["result"]["placed"] == seeded["result"]["placed"],
           "the replay reported different randomness than the original")

    changed = send(rv, "test.scatter", {"seed": 8}, key="k-seeded", attempt=2)
    expect(code(changed) == "IDEMPOTENCY_MISMATCH",
           f"the same key with a different seed was accepted: {changed}")


def determinism_metadata_cannot_be_omitted(rv: Host) -> None:
    """A stochastic tool cannot be registered as if it were reproducible."""
    for seeds, determinism, why in [
        (("seed",), EXACT, "seeds on a tool claiming exactness"),
        ((), SEEDED, "seeded with no randomness channel named"),
        ((), "invented", "an undeclared determinism class"),
    ]:
        try:
            rv.runtime.registry.add(f"test.bad-{determinism}-{len(seeds)}", lambda p, r: {},
                                    seeds=seeds, determinism=determinism)
        except RuntimeError:
            continue
        raise AssertionError(f"the registry accepted {why}")


def the_ledger_records_intent_before_the_side_effect(rv: Host) -> None:
    """Two record types, because a crash does not wait for a convenient moment."""
    clean_scene()
    rv.result("scene.snapshot")
    send(rv, "object.create", {"kind": "cube", "name": "Recorded"}, key="k-ledger")

    records = list(OperationLedger.open(rv.runtime.world_incarnation).read())
    intents = [r for r in records if r["type"] == "OP_INTENT" and r.get("idempotency_key") == "k-ledger"]
    expect(len(intents) == 1, f"the operation wrote {len(intents)} intents")
    intent = intents[0]
    results = [r for r in records if r["type"] == "OP_RESULT" and r.get("intent") == intent["sequence"]]
    expect(len(results) == 1, f"the operation wrote {len(results)} results")

    for field in ("recipe_hash", "frame", "pre_revision", "method", "attempt", "state_domain"):
        expect(field in intent, f"the intent did not record {field}: {sorted(intent)}")
    expect(intent["frame"].startswith("rvframe:"),
           f"the intent did not record the coordinate frame it executed in: {intent['frame']}")
    for field in ("post_revision", "post_fingerprint", "journal_cursor", "outcome"):
        expect(field in results[0], f"the result did not record {field}: {sorted(results[0])}")
    expect(records.index(intent) < records.index(results[0]),
           "the result was written before the intent it belongs to")


def a_resumed_ledger_replays_a_terminal_record(rv: Host) -> None:
    """What a rebuilt bridge can recover from the file, in a world that survived.

    The Blender runtime never resumes a world — an add-on reload takes its memory
    with it and it cannot verify continuity, so it rotates — which is why this
    exercises the resume path directly rather than pretending otherwise. The
    Unity host does resume a verified world and will use this against a real
    reload.
    """
    world = rv.runtime.world_incarnation
    resumed = OperationLedger.open(world).resume()
    expect("k-ledger" in resumed, f"the ledger lost a completed operation: {sorted(resumed)}")
    expect(resumed["k-ledger"]["result"] is not None, "a completed operation resumed with no result")

    rebuilt = IdempotencyLedger()
    rebuilt.adopt(resumed, world)
    recipe = resumed["k-ledger"]["intent"]["recipe_hash"]
    replay = rebuilt.check("k-ledger", recipe, 2, world)
    expect(replay is not None and replay.get("replayed") is True,
           "a durable terminal record did not replay after the bridge was rebuilt")


SCENARIOS = (
    a_lost_reply_does_not_create_twice,
    a_lost_reply_does_not_report_a_delete_as_failed,
    an_absolute_transform_replays_rather_than_looking_idempotent,
    a_different_recipe_under_one_key_is_refused,
    the_same_recipe_under_a_new_key_executes_again,
    a_retry_with_no_record_is_indeterminate,
    an_interrupted_operation_is_indeterminate,
    a_reserved_key_is_in_progress,
    a_replaced_world_cannot_be_retried_into,
    a_rolled_back_operation_is_not_resurrected_by_a_retry,
    a_stochastic_tool_refuses_to_invent_its_own_randomness,
    determinism_metadata_cannot_be_omitted,
    the_ledger_records_intent_before_the_side_effect,
    a_resumed_ledger_replays_a_terminal_record,
)


def main() -> None:
    rv = Host("idempotency")
    for scenario in SCENARIOS:
        scenario(rv)
        print(f"  ok {scenario.__name__}", flush=True)
    (artifact_dir("blender-idempotency") / "scenarios.txt").write_text(
        "\n".join(scenario.__name__ for scenario in SCENARIOS) + "\n", encoding="utf-8")


run_gate("BLENDER_IDEMPOTENCY", main, "blender-idempotency")
