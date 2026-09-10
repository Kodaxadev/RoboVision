"""Delivering a model's mutations under the strict contract, and failing closed.

Two things the v0 driver did not do, both of which only matter when something
goes wrong — which is exactly when a benchmark harness is least able to notice
that it did the wrong thing.

**Every mutation is delivered strictly.** v0 opened a fully pinned autonomous
transaction and then sent the individual operations through plain `session.call`.
That is a real gap rather than a stylistic one: the transaction's pins were
checked once, at begin, and a world reload, a unit change or a foreign edit
between the second and third operation of a correction would have been executed
straight through. Each mutation now carries its own `expected_world`,
`expected_coordinate_contract`, current `if_revision`, a persisted
`idempotency_key` and an `attempt`, so a stale pin refuses *before* the next
mutation lands rather than after all of them have.

The identity is written to disk before the request is sent, because the window
worth protecting is the one where the host applied the operation and the reply
never came back. A key held only in memory is no key at all in that window.

**An orchestration failure never leaves a transaction open.** If Q1, locality,
the evaluator or anything else in the driver raises after a transaction has
begun, the safe outcome is verified rollback — never a commit, because "I could
not finish evaluating" is not evidence that the candidate was good. Where the
connection state is ambiguous the existing session facilities settle it:
reconnect, `recoverable_transaction()`, adopt with the credential this session
already holds, then roll back. No new transaction machinery is introduced here;
this is a caller of the one that already exists.

Mesh revision pins are deliberately absent. A topology-indexed operation that
needs `expected_mesh_revision` states it in the model's own typed parameters,
from the model's own observation. A driver that derived one would be inventing
the very agreement the pin exists to prove.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .errors import RoboVisionError
from .session import HostSession

# Errors that mean "the host may or may not have applied this". Only these are
# eligible for redelivery under the same key: a refusal on a stale pin is a
# correct answer about a plan that is no longer valid, and resending it would be
# asking the same question louder.
AMBIGUOUS = ("SESSION_LOST", "CONNECTION_CLOSED")


class Ledger:
    """Idempotency identities, on disk, before the side effect they name.

    Append-only and boring on purpose. Its whole job is to survive the process
    that wrote it, so that a redelivery after a lost reply can present the same
    key and the same recipe and be recognised rather than repeated.
    """

    def __init__(self, root: Path) -> None:
        self.path = root / "delivery-ledger.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def reserve(self, key: str, method: str, params: dict[str, Any],
                attempt: int, pins: dict[str, Any]) -> None:
        entry = {"at": round(time.time(), 3), "idempotency_key": key, "method": method,
                 "params": params, "attempt": attempt, "pins": pins}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")
            handle.flush()

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in
                self.path.read_text(encoding="utf-8").splitlines() if line.strip()]


def operation_key(attempt_id: str, index: int) -> str:
    """One logical mutation, one stable name, for as long as it is being tried.

    Derived from the attempt and the operation's position in the correction,
    which is what makes a redelivery of *that* operation recognisable while two
    identical operations in the same correction stay distinct.
    """
    return f"rvidem:{attempt_id.split(':')[-1]}:{index:02d}"


class Delivery:
    """Sends one correction's operations, strictly, tracking the revision.

    The revision is re-read from every response envelope rather than assumed to
    advance by one: a typed operation may author several changes, and a driver
    that guessed the next pin would send a stale one and be refused for its own
    arithmetic rather than for anything that happened in the scene.
    """

    def __init__(self, session: HostSession, ledger: Ledger, *, world: str,
                 contract: str, revision: int, attempt_id: str) -> None:
        self.session = session
        self.ledger = ledger
        self.world = world
        self.contract = contract
        self.revision = revision
        self.attempt_id = attempt_id
        self.calls = 0
        self._mutating: dict[str, bool] = {}

    def mutating(self, method: str) -> bool:
        """Ask the host, rather than pattern-matching the method name.

        Whether an operation needs an idempotency identity is the host's
        declaration about that operation, and a driver meant to work against a
        second editor cannot hold a list of which Blender methods author.
        """
        if method not in self._mutating:
            described = self.session.call("system.method", {"method": method})
            self.calls += 1
            self._mutating[method] = bool((described.get("result") or {}).get("mutating"))
        return self._mutating[method]

    def pins(self, key: str | None, attempt: int) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "contract": "autonomous",
            "expected_world": self.world,
            "expected_coordinate_contract": self.contract,
            "if_revision": self.revision,
        }
        if key is not None:
            fields["idempotency_key"] = key
            fields["attempt"] = attempt
        return fields

    def send(self, index: int, method: str, params: dict[str, Any],
             attempt: int = 1) -> dict[str, Any]:
        """One logical operation, identified before it is sent.

        A retry of the same logical operation calls this again with the same
        index and params and a higher `attempt`: the key and the recipe are
        therefore identical and the host resolves the duplicate rather than
        applying it twice.
        """
        key = operation_key(self.attempt_id, index) if self.mutating(method) else None
        fields = self.pins(key, attempt)
        if key is not None:
            # Durable first. The reply is the part that can be lost.
            self.ledger.reserve(key, method, params, attempt, dict(fields))
        response = self.session.call(method, params, **fields)
        self.calls += 1
        revision = response.get("revision")
        if isinstance(revision, int):
            self.revision = revision
        return {"method": method, "params": params, "ok": response.get("ok"),
                "outcome": response.get("outcome"), "request_id": response.get("id"),
                "revision": response.get("revision"), "idempotency_key": key,
                # The host's own word for "I have seen this delivery before".
                # Recorded rather than inferred from an unchanged revision, which
                # a genuine no-op would also produce.
                "replayed": bool(response.get("replayed")),
                "delivery_attempt": attempt, "pins": fields}


def _rollback(session: HostSession, transaction: str) -> dict[str, Any]:
    return session.end_transaction("transaction.rollback", transaction).get("result") or {}


def fail_closed(session: HostSession, transaction: str,
                cause: BaseException) -> dict[str, Any]:
    """Put the scene back after the driver itself failed. Never commit.

    Three outcomes, all of them honest. A plain rollback where the connection is
    still good. An adopt-then-rollback where the connection was lost and this
    session still holds the credential for the transaction it opened. And, when
    neither is possible, a *read* of what became of the transaction — because
    the one thing worse than an unrecovered transaction is a harness that
    reports a recovery it did not perform.
    """
    record: dict[str, Any] = {"cause": f"{type(cause).__name__}: {cause}",
                              "transaction": transaction, "steps": []}

    def step(name: str, **fields: Any) -> None:
        record["steps"].append({"step": name, **fields})

    try:
        proof = _rollback(session, transaction)
        step("rollback", ok=True)
        record.update(recovery="rolled_back", proof=proof)
        return record
    except RoboVisionError as exc:
        step("rollback", ok=False, code=exc.payload.code, message=str(exc))
        if exc.payload.code not in AMBIGUOUS:
            # The host answered, and its answer was not "I lost you". Ask it what
            # the transaction's state actually is rather than inventing one.
            return _resolve_by_reading(session, transaction, record, step)

    # The connection went away. The host has orphaned whatever this session
    # owned, and adoption is the mechanism that exists for exactly this.
    try:
        step("reconnect", generation=session.reconnect())
        recoverable = session.recoverable_transaction()
        step("recoverable", state=recoverable)
        if recoverable and recoverable.get("recoverable_by_this_session"):
            session.adopt_transaction(transaction)
            step("adopt", ok=True)
            proof = _rollback(session, transaction)
            step("rollback", ok=True)
            record.update(recovery="adopted_and_rolled_back", proof=proof)
            return record
    except RoboVisionError as exc:
        step("recover", ok=False, code=exc.payload.code, message=str(exc))
    return _resolve_by_reading(session, transaction, record, step)


def _resolve_by_reading(session: HostSession, transaction: str,
                        record: dict[str, Any], step) -> dict[str, Any]:
    """What became of it, established by asking rather than by assuming."""
    try:
        terminal = session.terminal_state(transaction)
        step("terminal_state", state=terminal)
        outcome = terminal.get("outcome")
        record.update(
            recovery="already_" + outcome if outcome else "unresolved",
            terminal=terminal)
    except RoboVisionError as exc:
        step("terminal_state", ok=False, code=exc.payload.code, message=str(exc))
        record.update(recovery="unresolved")
    if record["recovery"] == "unresolved":
        record["note"] = ("the transaction could not be proved rolled back or "
                          "finished; it is reported as unresolved rather than "
                          "assumed safe")
    return record
