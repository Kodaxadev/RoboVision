"""The participant boundary is enforced, not requested.

`CHALLENGE.md` telling a model "you never mutate the editor" is documentation,
and documentation is not a control. If the boundary lived only there, every
result would need the caveat "assuming the participant followed instructions" —
and the one participant most likely not to is the one worth studying.

Two rules, and the second exists because the first will eventually be out of date:
a method must be on the read allowlist, *and* the host must declare it
non-mutating. An operation added to RoboVision tomorrow is denied until someone
decides it belongs in a benchmark, and an operation that becomes mutating is
denied whether or not anyone remembers to edit the allowlist.
"""
from __future__ import annotations

import pytest

from benchmarks.facade import BenchmarkFacade, Denied


class Session:
    """A host that answers `system.method` honestly and records what it was asked."""

    def __init__(self, mutating=("object.transform", "object.create",
                                 "object.delete", "mesh.extrude_faces",
                                 "modifier.add")):
        self.mutating = set(mutating)
        self.calls: list[str] = []

    def call(self, method, params=None, **_fields):
        self.calls.append(method)
        if method == "system.method":
            return {"ok": True, "result": {"name": params["method"],
                                           "mutating": params["method"] in self.mutating}}
        return {"ok": True, "result": {"method": method}}


class Runner:
    def __init__(self):
        self.submitted: list[dict] = []
        self.stopped: tuple | None = None

    def observe(self):
        return {"packet": True}

    def take_observation_calls(self):
        return 4

    def attempt(self, correction):
        self.submitted.append(correction)
        return {"decision": "accept"}

    def stop(self, reason, note):
        self.stopped = (reason, note)
        return {"stopped": reason}


def facade():
    session = Session()
    return BenchmarkFacade(session, Runner()), session


@pytest.mark.parametrize("method", [
    "scene.describe", "scene.snapshot", "truth.geometry", "truth.reference",
    "truth.silhouette", "viewport.capture", "system.health", "mesh.inspect",
])
def test_reads_are_allowed(method):
    surface, _ = facade()
    assert surface.call(method)["ok"] is True


@pytest.mark.parametrize("method", [
    "object.transform", "object.create", "object.delete",
    "mesh.extrude_faces", "modifier.add",
])
def test_mutations_are_denied(method):
    surface, _ = facade()
    with pytest.raises(Denied):
        surface.call(method)
    assert surface.denied_calls[0]["method"] == method


@pytest.mark.parametrize("method", [
    "transaction.begin", "transaction.commit", "transaction.rollback",
    "transaction.adopt", "transaction.discard", "transaction.status",
])
def test_transaction_control_is_denied(method):
    """Even `transaction.status`, which only reads.

    Not because reading it is dangerous, but because a participant that can
    observe transaction state is a participant reasoning about the harness
    rather than about the asset.
    """
    surface, _ = facade()
    with pytest.raises(Denied):
        surface.call(method)


def test_an_unknown_method_is_denied_by_default():
    """Allowlist-first: a future operation is refused until someone decides."""
    surface, _ = facade()
    with pytest.raises(Denied):
        surface.call("script.run_python")


def test_a_method_that_becomes_mutating_is_denied_without_touching_the_allowlist():
    """The host's declaration overrules the list, which is the point of asking it."""
    session = Session(mutating=("truth.silhouette",))
    surface = BenchmarkFacade(session, Runner())
    with pytest.raises(Denied):
        surface.call("truth.silhouette")


def test_reading_a_mutations_schema_stays_available():
    """Writing a correction requires the parameters of the operations it names."""
    surface, _ = facade()
    described = surface.schema("object.transform")
    assert described["mutating"] is True


def test_the_only_write_path_delegates_rather_than_reimplementing():
    surface, session = facade()
    correction = {"objective": "narrow the mast", "operations": []}
    assert surface.submit_correction(correction) == {"decision": "accept"}
    assert surface._runner.submitted == [correction]
    # Nothing was sent to the host by the facade itself: the harness owns the
    # transaction, the delivery and the evaluation, and a second implementation
    # here would let the benchmark's loop drift from the real one.
    assert session.calls == []


def test_stopping_is_a_first_class_action():
    surface, _ = facade()
    surface.stop("evidence_insufficient", "the packet does not localise it")
    assert surface._runner.stopped == ("evidence_insufficient",
                                       "the packet does not localise it")


def test_observation_is_counted_separately_from_correction():
    surface, _ = facade()
    surface.call("scene.describe")
    surface.observe()
    counts = surface.counts()
    # One read, plus its system.method lookup, plus the packet's own calls.
    assert counts["participant_observation_tool_calls"] == 6
    assert counts["observation_budgeted"] is False
