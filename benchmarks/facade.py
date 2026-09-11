"""The participant's whole surface. Reads, one write path, and a stop.

`CHALLENGE.md` says "you never call the editor to mutate it". That sentence is
not a control. A benchmark whose isolation is a request in a document measures
how obedient the participant felt like being, and every result afterwards has to
be caveated with "assuming it followed the instructions". So the boundary is
enforced here instead, and the document merely describes what the facade already
guarantees.

The rule is allowlist-first. A method is permitted only if it is on the read
allowlist *and* the host itself declares it non-mutating. Both halves earn their
place: the allowlist means an operation added to RoboVision tomorrow is denied
until someone decides it belongs in a benchmark, and the host's own declaration
means the facade cannot be wrong about which operations author — that is not a
fact the benchmark harness gets to have an opinion about.

There is exactly one write path, `submit_correction`. It consumes an attempt and
hands the correction to the existing Artist Loop harness. No transaction is
opened here, no operation is delivered here, no evaluation happens here — this
file would be the worst possible place for a second implementation of any of it,
because a divergence between the benchmark's loop and the real one would make
every result meaningless.

This is benchmark isolation, not a security architecture. It is a facade in one
Python process; it is not a sandbox, and it does not defend against a participant
that is handed the underlying session instead of this object.
"""
from __future__ import annotations

from typing import Any

from robovision.errors import RoboVisionError

# Read-only surfaces a participant needs to diagnose an asset. Prefix match.
READABLE = (
    "system.health", "system.hello", "system.ping",
    "system.capabilities", "system.method",
    "scene.describe", "scene.snapshot", "scene.search", "scene.diff",
    "scene.raycast", "scene.changes_since",
    "object.inspect", "mesh.inspect", "mesh.query", "mesh.components",
    "mesh.elements", "modifier.list", "viewport.inspect",
    # Evidence production that does not author the asset: measurements, and the
    # shaded captures that are the only thing here a human can judge by eye.
    "truth.", "viewport.focus", "viewport.axis", "viewport.capture",
    "perception.capture_bundle",
)

# Named rather than merely absent from the allowlist, so the refusal can say why.
FORBIDDEN = {
    "transaction.": "transaction control belongs to the harness; a correction is "
                    "submitted, not driven",
}


class Denied(RoboVisionError):
    """A participant reached past the benchmark boundary."""


class BenchmarkFacade:
    """One participant, one asset, one budget.

    Call counts are kept in two separate places on purpose. Reporting a single
    `tool_calls` figure that silently excluded observation would describe a model
    as more efficient than it was, and observation is the larger half.
    """

    def __init__(self, session, runner, *, observation_budgeted: bool = False) -> None:
        self._session = session
        self._runner = runner
        self.observation_calls = 0
        self.denied_calls: list[dict[str, Any]] = []
        self.observation_budgeted = observation_budgeted
        self._mutating: dict[str, bool] = {}

    # ------------------------------------------------------------------ reads

    def _spend(self, count: int) -> None:
        """Count a participant-caused call, and persist it if the runner can.

        Persisted rather than held in memory because each operator subcommand is
        its own process: a count that lived only here would be reported as zero
        by the `finalize` that runs afterwards, which is worse than not counting.
        """
        self.observation_calls += count
        persist = getattr(self._runner, "spend_observation", None)
        if persist is not None:
            persist(count)

    def _mutates(self, method: str) -> bool:
        if method not in self._mutating:
            described = self._session.call("system.method", {"method": method})
            # Counted: the participant caused it, even though the facade asked.
            self._spend(1)
            self._mutating[method] = bool((described.get("result") or {}).get("mutating"))
        return self._mutating[method]

    def _refuse(self, method: str, why: str):
        self.denied_calls.append({"method": method, "reason": why})
        return Denied("BENCHMARK_METHOD_DENIED", why,
                      data={"method": method,
                            "allowed": "read-only observation, plus "
                                       "submit_correction() for any change"})

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Any read the participant wants. Refused if it could author anything."""
        for prefix, why in FORBIDDEN.items():
            if method.startswith(prefix):
                raise self._refuse(method, why)
        if not any(method == entry or method.startswith(entry) for entry in READABLE):
            raise self._refuse(
                method, "not on the benchmark's read allowlist; the participant "
                        "surface is observation plus submit_correction()")
        if self._mutates(method):
            # Belt and braces: the host's own declaration overrules the
            # allowlist, so a method that becomes mutating is denied without
            # anyone remembering to update this file.
            raise self._refuse(method, "the host declares this operation mutating; "
                                       "authored change goes through submit_correction()")
        response = self._session.call(method, params or {})
        self._spend(1)
        return response

    def health(self) -> dict[str, Any]:
        response = self.call("system.health")
        identity = getattr(self._runner, "identity", None)
        if identity is not None:
            # Beside the host's report, never inside it: the host's readiness
            # and this benchmark run's identity are separate layers, and it was
            # exactly their being conflated — "begin_correction: ready" while the
            # run was sealed — that made the 2026-09-10 misroute hard to read.
            response = {**response, "benchmark_run": identity()}
        return response

    def schema(self, method: str) -> dict[str, Any]:
        """The exact parameters of any operation, including mutating ones.

        Reading a mutation's schema is how a correction gets written, so it stays
        available. Reading it is not performing it.
        """
        response = self._session.call("system.method", {"method": method})
        self._spend(1)
        return response.get("result") or {}

    def observe(self) -> dict[str, Any]:
        """The discrepancy packet: the participant's primary evidence."""
        evidence = self._runner.observe()
        # Added to the local total only: the runner persisted these itself while
        # taking the measurement, and spending them again here would double.
        self.observation_calls += self._runner.take_observation_calls()
        return evidence

    # ------------------------------------------------------------------ write

    def submit_correction(self, correction: dict[str, Any]) -> dict[str, Any]:
        """The only way anything changes. Consumes one attempt.

        Delegates straight to the Artist Loop harness: transaction, strict
        autonomous delivery, Q1, evaluation, commit or verified rollback. The
        refreshed evidence comes back with the result so the participant sees
        what its correction did without spending another round trip.
        """
        return self._runner.attempt(correction)

    def stop(self, reason: str, note: str = "") -> dict[str, Any]:
        """End the run deliberately, and say why."""
        return self._runner.stop(reason, note)

    # ----------------------------------------------------------------- counts

    def counts(self) -> dict[str, Any]:
        return {
            "participant_observation_tool_calls": self.observation_calls,
            "denied_calls": len(self.denied_calls),
            "denied": self.denied_calls,
            "observation_budgeted": self.observation_budgeted,
        }
