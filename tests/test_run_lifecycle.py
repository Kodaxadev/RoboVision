"""A benchmark run is one experiment, and its identity has to be as strict as a transaction.

On 2026-09-10 a desktop MCP client kept an old shim alive for a finished run and
routed a new participant session to it. Nothing refused the session. It observed
under the wrong identity, and its `stop` overwrote the finished run's sealed stop
reason with its own. Transactions in this project are strict; the run lifecycle
around them was not. These pin the three properties that would have stopped it:

- a stopped run is immutable — a second `stop` changes nothing;
- a used run identity cannot be restored over;
- the scene a run acts on must be the one that run left behind.
"""
from __future__ import annotations

import json

import pytest

from benchmarks import runner as runner_module
from benchmarks.runner import LIFECYCLE_FILES, Runner, expected_state

BENCH = "correction-transfer-v3-feedback"


class Idle:
    """A session that must never be called by the paths under test."""

    def call(self, *_args, **_kwargs):
        raise AssertionError("lifecycle checks must not touch the editor")


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_module, "RUNS", tmp_path)
    return Runner(Idle(), BENCH, "lifecycle-test")


# ------------------------------------------------------------- immutable stop

def test_a_first_stop_is_recorded(run):
    run.stop("evidence_insufficient", "first")
    stopped = json.loads((run.root / "stop.json").read_text(encoding="utf-8"))
    assert stopped["reason"] == "evidence_insufficient"


def test_a_second_stop_changes_nothing(run):
    """The exact overwrite that happened to a sealed run."""
    run.stop("no_worthwhile_correction_remains", "the real reason")
    before = {name: (run.root / name).read_bytes()
              for name in ("stop.json", "trajectory.json")}
    with pytest.raises(SystemExit, match="RUN_STOPPED no_worthwhile_correction_remains"):
        run.stop("evidence_insufficient", "a misrouted session")
    for name, data in before.items():
        assert (run.root / name).read_bytes() == data


# ------------------------------------------------------- run identity reuse

@pytest.mark.parametrize("used", LIFECYCLE_FILES)
def test_restore_refuses_a_used_run_identity(run, used):
    (run.root / used).write_text("{}", encoding="utf-8")
    with pytest.raises(SystemExit, match="RUN_ID_IN_USE"):
        run.restore()   # Idle raises if the editor is touched: it is not.


def test_restore_artifacts_alone_do_not_count_as_use(run, tmp_path, monkeypatch):
    """Re-restoring a run no participant touched is harmless and allowed.

    A throwaway private recipe stands in for the sealed one, so the test proves
    restore got past the identity check without ever reading real answer-key
    material.
    """
    private = tmp_path / "private"
    (private / "correction-transfer-v2").mkdir(parents=True)
    (private / "correction-transfer-v2" / "restore.json").write_text(
        '{"objects": []}', encoding="utf-8")
    monkeypatch.setenv("RVBENCH_PRIVATE", str(private))
    for name in ("brief.json", "packet-q0.json", "restore-verification.json"):
        (run.root / name).write_text("{}", encoding="utf-8")
    with pytest.raises(AssertionError, match="must not touch the editor"):
        run.restore()   # got past the identity check and reached the editor


# ------------------------------------------------------------ scene binding

def test_before_any_attempt_the_scene_must_be_a0():
    assert expected_state([], "rvsig:a0") == {"kind": "signature", "value": "rvsig:a0"}


def test_after_a_committed_attempt_the_scene_must_be_what_it_left():
    attempts = [{"attempt": "a1", "final_fingerprint": "fp-after", "restored": False}]
    assert expected_state(attempts, "rvsig:a0") == {"kind": "fingerprint",
                                                    "value": "fp-after"}


def test_after_a_verified_rollback_the_scene_must_be_where_it_began():
    """An orchestration failure records no final fingerprint but a proven restore."""
    attempts = [{"attempt": "a1", "final_fingerprint": None, "restored": True,
                 "begin_fingerprint": "fp-before"}]
    assert expected_state(attempts, "rvsig:a0") == {"kind": "fingerprint",
                                                    "value": "fp-before"}


def test_an_attempt_with_lost_closing_evidence_is_not_guessed():
    attempts = [{"attempt": "a1", "final_fingerprint": None, "restored": None,
                 "begin_fingerprint": "fp-before", "phase": "post_terminal"}]
    assert expected_state(attempts, "rvsig:a0")["kind"] == "unknown"


def test_a_package_without_a_signature_cannot_bind_before_attempts():
    assert expected_state([], None)["kind"] == "unknown"


# -------------------------------------------------------------- identity

def test_identity_names_the_run_and_its_state(run):
    identity = run.identity()
    assert identity == {"benchmark": "rvbench:correction-transfer/v3-feedback",
                        "run": "lifecycle-test", "attempts_used": 0,
                        "stopped": False}
    run.stop("other", "done")
    assert run.identity()["stopped"] is True
