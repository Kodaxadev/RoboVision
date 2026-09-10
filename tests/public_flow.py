"""The agent-facing path, driven identically against both editors.

One module, run against Blender and against Unity through the same public
`HostSession` — the object the MCP adapter holds, unchanged. That is the whole
point: nothing here imports a host module, reads a host constant, or branches on
which editor answered. If a claim needs a `if host == "unity"` to stay true, it
is not a cross-editor claim and this file is where that shows up.

Three things are proved, in this order because each depends on the one before:

- **discovery.** Everything a strict autonomous invocation must pin is published
  by `system.hello` on both hosts. Unity enforced its coordinate contract
  internally while publishing nothing, so an external model could not construct
  a pinned call without reaching into the package for the constant.
- **health.** `system.health` is a structured readiness report in one shared
  vocabulary, with independent answers, and it authors nothing to produce them.
- **the loop.** health → world → coordinate contract → authoritative
  observation → fully pinned begin → fully pinned mutation, with every pin taken
  from what the host published rather than from anything this file knows.
"""
from __future__ import annotations

import uuid
from typing import Any, Callable

from robovision.errors import RoboVisionError

STATUSES = {"ready", "degraded", "blocked", "unknown", "not_applicable"}

READY_FOR = (
    "semantic_observation",
    "visual_observation",
    "begin_correction",
    "mutate",
    "semantic_verify",
    "visual_verify",
    "finish_or_recover",
)

SUBSYSTEMS = ("journal", "transaction", "operations", "executor", "perception")

# What an external agent must be able to read out of `system.hello` in order to
# construct a strict autonomous invocation. Every one of these is a pin, an
# identity a pin is checked against, or the state machine a pin can be refused
# by — which is why the list is exactly this and not "everything useful".
HELLO_REQUIRED = (
    "protocol",
    "host",
    "editor",
    "bridge",
    "world_incarnation",
    "revision",
    "state_domain",
    "coordinate_contract",
    "units",
    "journal",
    "transaction",
)

# Prose that described a policy the code stopped implementing: an orphan has
# needed the recovery credential since adoption existed. Structured facts
# replaced it, and this is here so the paragraph cannot come back.
FORBIDDEN_OWNERSHIP_PHRASES = (
    "any client may commit",
    "any client may roll",
)


class Findings:
    """What was observed, and what did not hold. Never one without the other."""

    def __init__(self, host: str) -> None:
        self.host = host
        self.values: dict[str, Any] = {}
        self.failures: list[str] = []

    def record(self, key: str, value: Any) -> Any:
        self.values[key] = value
        return value

    def expect(self, condition: bool, message: str) -> bool:
        if not condition:
            self.failures.append(f"[{self.host}] {message}")
        return bool(condition)

    def report(self) -> dict[str, Any]:
        return {"ok": not self.failures, "host": self.host,
                "failures": self.failures, "findings": self.values}


def _result(response: dict[str, Any]) -> dict[str, Any]:
    return response.get("result") or {}


def _refused(call: Callable[[], Any]) -> str:
    """Run something that must be refused, and name the refusal.

    The public client raises on a refused call rather than handing back an error
    envelope, which is the right shape for a caller and the wrong one for an
    assertion about which refusal arrived.
    """
    try:
        call()
    except RoboVisionError as exc:
        return exc.payload.code
    return "SUCCEEDED"


def _observe(session) -> tuple[int, str]:
    """One authoritative observation, and the two things a pin is made of.

    The revision comes off the envelope rather than out of the result: every
    response carries it, on both hosts, which is precisely the field a
    host-agnostic caller is meant to use.
    """
    response = session.call("scene.snapshot")
    return int(response["revision"]), _result(response)["fingerprint"]


# ----------------------------------------------------------------- discovery


def discovery_publishes_every_pin(session, found: Findings) -> dict[str, Any]:
    """Everything a pinned autonomous call needs, read from the host itself."""
    hello = _result(session.call("system.hello"))
    missing = [field for field in HELLO_REQUIRED if field not in hello]
    found.record("hello_missing", missing)
    found.expect(not missing, f"system.hello does not publish {missing}")

    contract = hello.get("coordinate_contract")
    found.record("coordinate_contract", contract)
    found.expect(isinstance(contract, str) and contract.startswith("rvcoord:"),
                 f"the coordinate contract is not publicly discoverable: {contract!r}")

    units = hello.get("units")
    found.record("units_keys", sorted(units) if isinstance(units, dict) else None)
    found.expect(isinstance(units, dict) and "canonical_unit" in units,
                 "the unit convention was not published as a structured description")

    found.expect(hello.get("state_domain") in ("authored", "play_runtime"),
                 f"state_domain is not one of the two domains: {hello.get('state_domain')!r}")
    found.expect(isinstance(hello.get("world_incarnation"), str)
                 and hello["world_incarnation"].startswith("rvworld:"),
                 "the world incarnation is not publicly discoverable")
    found.expect(isinstance(hello.get("bridge"), str) and hello["bridge"].startswith("rvbridge:"),
                 "the bridge incarnation is not publicly discoverable")
    found.expect(isinstance(hello.get("revision"), int),
                 "the authored revision is not published as an integer")
    journal = hello.get("journal")
    found.expect(isinstance(journal, dict) and {"epoch", "certain", "cursor"} <= set(journal),
                 "the journal state is not published in enough detail to resume from")

    ownership = hello.get("transaction", {})
    found.record("ownership", ownership.get("ownership"))
    found.record("orphan_requires_adoption", ownership.get("orphan_requires_adoption"))
    found.expect(ownership.get("ownership") == "connection",
                 f"ownership is not a machine-readable fact: {ownership.get('ownership')!r}")
    found.expect(ownership.get("orphan_requires_adoption") is True,
                 "the host does not state that an orphan requires adoption")

    # The policy paragraph that drifted, wherever it might have gone.
    prose = " ".join(str(value) for value in ownership.values()).lower()
    stale = [phrase for phrase in FORBIDDEN_OWNERSHIP_PHRASES if phrase in prose]
    found.record("stale_ownership_prose", stale)
    found.expect(not stale, f"stale transaction ownership prose is still published: {stale}")
    return hello


# -------------------------------------------------------------------- health


def health_is_a_structured_readiness_report(session, found: Findings) -> dict[str, Any]:
    """A report, in one vocabulary, whose every answer stands on its own."""
    response = session.health()
    report = _result(response)
    found.record("health_ok", response.get("ok"))
    found.expect(response.get("ok") is True, f"system.health failed: {response.get('error')}")

    ready_for = report.get("ready_for") or {}
    found.record("ready_for", {name: entry.get("status") for name, entry in ready_for.items()})
    for name in READY_FOR:
        entry = ready_for.get(name)
        if not found.expect(isinstance(entry, dict), f"health does not answer {name}"):
            continue
        found.expect(entry.get("status") in STATUSES,
                     f"{name} used a status outside the vocabulary: {entry.get('status')!r}")
        # Health must not become evidence it did not earn: every answer says what
        # it was decided on, so a caller can tell a measurement from a default.
        found.expect(any(key in entry for key in ("basis", "reason", "situation")),
                     f"{name} reported {entry.get('status')!r} with no basis or reason")

    subsystems = report.get("subsystems") or {}
    found.record("subsystems", {name: (subsystems.get(name) or {}).get("status")
                                for name in SUBSYSTEMS})
    for name in SUBSYSTEMS:
        found.expect(isinstance(subsystems.get(name), dict), f"health omits the {name} subsystem")

    identity = report.get("identity") or {}
    found.record("identity_keys", sorted(identity))
    for field in ("bridge", "world_incarnation", "revision", "state_domain",
                  "coordinate_contract", "units", "journal_cursor", "journal_epoch",
                  "journal_certain", "world_resumption"):
        found.expect(field in identity, f"the health identity block omits {field}")

    # Host truth and session truth stay visibly separate: the library may enrich
    # the view with what it alone knows, and must never present that as the
    # host's claim — nor ever expose the credential it is talking about.
    session_view = response.get("session") or {}
    found.record("session_view", sorted(session_view))
    found.expect("recoverable_by_this_session" in session_view,
                 "the session did not contribute what only it can know")
    found.expect("recoverable_by_this_session" not in report,
                 "a session-only fact was presented as the host's claim")
    found.expect("recovery_token" not in str(response),
                 "a recovery credential appeared in a health response")

    # An orphan-free, readable scene is the baseline every other case is a
    # deviation from, and background/batch editors have no interactive view.
    found.expect(ready_for["semantic_observation"]["status"] == "ready",
                 "a readable authored scene did not report semantic observation as ready")
    found.expect(ready_for["semantic_observation"].get("basis")
                 == "authoritative_resync_succeeded",
                 "semantic readiness was claimed without the resync that earns it")
    found.record("visual_status", ready_for["visual_observation"]["status"])
    found.record("visual_reason", ready_for["visual_observation"].get("reason"))
    return report


def health_observes_without_perturbing(session, found: Findings) -> None:
    """Health looks. A report that had to author something to exist is not one."""
    before_revision, before_fingerprint = _observe(session)
    session.health()
    after_revision, after_fingerprint = _observe(session)
    found.record("health_moved_revision", before_revision != after_revision)
    found.record("health_moved_fingerprint", before_fingerprint != after_fingerprint)
    found.expect(before_fingerprint == after_fingerprint, "system.health changed the scene")
    found.expect(before_revision == after_revision,
                 "system.health advanced the authored revision")


def an_owned_transaction_is_represented(session, found: Findings, hello: dict[str, Any]) -> None:
    """The connection that opened it is told so, in the shared vocabulary."""
    idle_report = _result(session.health())["ready_for"]
    idle = idle_report["finish_or_recover"]
    found.record("idle_situation", idle.get("situation"))
    found.expect(idle["status"] == "not_applicable" and idle.get("situation") == "none",
                 f"an idle host reported a transaction situation: {idle}")

    # A correction is only offered where both of its branches exist. Whichever
    # answer this host gives, it has to be decided by the mechanism rather than
    # by an overall mood: ready states the rollback is available, blocked names
    # the mechanism that is missing.
    idle_begin = idle_report["begin_correction"]
    found.record("idle_begin", idle_begin["status"])
    found.record("idle_begin_verified_rollback", idle_begin.get("verified_rollback"))
    found.record("idle_begin_reason", idle_begin.get("reason"))
    if idle_begin["status"] == "ready":
        found.expect(idle_begin.get("verified_rollback") is True,
                     "a correction was offered without stating that a rollback is provable")
    else:
        found.expect(idle_begin["status"] == "blocked",
                     f"a correction with a missing mechanism was reported as "
                     f"{idle_begin['status']!r} rather than blocked")
        found.expect(bool(idle_begin.get("reason")),
                     "a blocked correction did not name the mechanism it lacks")

    revision, _ = _observe(session)
    begun = session.begin_transaction(
        "health owned", if_revision=revision,
        expected_world=hello["world_incarnation"],
        expected_coordinate_contract=hello["coordinate_contract"],
        contract="autonomous")
    transaction = _result(begun)["transaction"]
    try:
        report = _result(session.health())
        finish = report["ready_for"]["finish_or_recover"]
        found.record("owned_situation", finish.get("situation"))
        found.record("owned_mutate", report["ready_for"]["mutate"]["status"])
        found.record("owned_begin", report["ready_for"]["begin_correction"]["status"])
        found.expect(finish.get("situation") == "active_owned_by_this_connection",
                     f"an owned transaction was misrepresented: {finish.get('situation')}")
        found.expect(report["ready_for"]["mutate"]["status"] == "ready",
                     "the transaction's own owner was told it could not mutate")
        found.expect(report["ready_for"]["begin_correction"]["status"] == "blocked",
                     "a second correction was offered while one was already open")
        found.expect(
            report["ready_for"]["begin_correction"].get("reason")
            == "transaction_active_owned_by_this_connection",
            "the blocking state was not named exactly")
        found.expect("recoverable_by_this_session" not in str(finish),
                     "the host claimed knowledge of the caller's recovery credential")
    finally:
        session.end_transaction("transaction.discard", transaction)


# ------------------------------------------------------------------ the loop


def the_artist_loop_can_discover_its_own_pins(session, found: Findings) -> None:
    """health → world → contract → observation → pinned begin → pinned mutation.

    Every value below comes out of a response. Nothing is imported, and no
    host-specific constant appears — which is exactly the difference between what
    the in-process Unity tests can do and what an external model can.
    """
    identity = _result(session.health())["identity"]
    world = identity["world_incarnation"]
    contract = identity["coordinate_contract"]

    # The pin is taken from an authoritative observation, not from the health
    # report: health is preflight, and an artistic decision is made against a
    # snapshot it does not claim to be.
    revision, _ = _observe(session)
    found.record("pins_from_health", {"world": world is not None, "contract": contract is not None})

    begun = session.begin_transaction(
        "artist loop pins", if_revision=revision, expected_world=world,
        expected_coordinate_contract=contract, contract="autonomous")
    found.record("pinned_begin_ok", begun.get("ok"))
    found.expect(begun.get("ok") is True, f"a fully pinned autonomous begin was refused: {begun}")
    transaction = _result(begun)["transaction"]

    try:
        key = "health-loop-" + uuid.uuid4().hex
        mutated = session.call(
            "object.create", {"name": "HealthLoop"},
            if_revision=_observe(session)[0],
            idempotency_key=key, attempt=1, expected_world=world,
            expected_coordinate_contract=contract, contract="autonomous")
        found.record("pinned_mutation_ok", mutated.get("ok"))
        found.record("pinned_mutation_outcome", mutated.get("outcome"))
        found.expect(mutated.get("ok") is True,
                     f"a fully pinned autonomous mutation was refused: {mutated.get('error')}")
        found.expect(mutated.get("outcome") == "applied",
                     f"the pinned mutation did not author anything: {mutated.get('outcome')}")

        # And a contract the client invented rather than read is refused, which
        # is what makes reading the published one worth anything.
        revision = _observe(session)[0]
        code = _refused(lambda: session.call(
            "object.create", {"name": "HealthLoopWrong"},
            if_revision=revision,
            idempotency_key="health-loop-wrong-" + uuid.uuid4().hex, attempt=1,
            expected_world=world, expected_coordinate_contract="rvcoord:0000000000000000",
            contract="autonomous"))
        found.record("invented_contract_code", code)
        found.expect(code == "COORDINATE_CONTRACT_CHANGED",
                     f"a guessed coordinate contract was not refused as such: {code}")
    finally:
        session.end_transaction("transaction.discard", transaction)


# ---------------------------------------------------------------------- run


def run(session, host: str, extra: Callable[[Any, Findings], None] | None = None) -> dict[str, Any]:
    """Everything, against one host, through the public client only."""
    found = Findings(host)
    hello = discovery_publishes_every_pin(session, found)
    health_is_a_structured_readiness_report(session, found)
    health_observes_without_perturbing(session, found)
    an_owned_transaction_is_represented(session, found, hello)
    the_artist_loop_can_discover_its_own_pins(session, found)
    if extra is not None:
        extra(session, found)
    return found.report()
