"""The credential state machine, exercised without a host.

Reconciliation after an ambiguous adoption has exactly two legitimate answers,
and the value that distinguishes them arrives from the host. These construct that
value directly, including the one that means the two histories have diverged —
which no live host will produce on demand, and which is precisely the case where
guessing would hand over a secret the host has no verifier for.
"""
from __future__ import annotations

import pytest

from robovision.errors import RoboVisionError
from robovision.session import HostSession, _Credential


def ambiguous_adoption() -> _Credential:
    """A credential mid-rotation: sent, unacknowledged, both secrets held."""
    credential = _Credential(generation=0, secret="secret-a")
    credential.next_secret = "secret-b"
    credential.next_generation = 1
    credential.ambiguous = True
    return credential


def test_rotation_that_did_not_happen_keeps_the_current_secret():
    credential = ambiguous_adoption()
    HostSession._reconcile(credential, host_generation=0, transaction="rvtx:t")
    assert credential.secret == "secret-a"
    assert credential.generation == 0
    # Still held: the adoption can simply be sent again.
    assert credential.next_secret == "secret-b"
    assert credential.ambiguous is False


def test_rotation_that_happened_promotes_the_pending_secret():
    credential = ambiguous_adoption()
    HostSession._reconcile(credential, host_generation=1, transaction="rvtx:t")
    assert credential.secret == "secret-b"
    assert credential.generation == 1
    assert credential.next_secret is None
    assert credential.ambiguous is False


@pytest.mark.parametrize("host_generation", [2, 5, 99])
def test_a_generation_beyond_the_pending_one_is_not_promoted(host_generation):
    """`>=` would have promoted here, handing over a secret with no basis.

    A host further ahead than anything this session pended means someone else
    rotated in between, or state was lost. There is no safe guess: the pending
    secret's verifier is not what the host holds, and neither is the current
    one's.
    """
    credential = ambiguous_adoption()
    with pytest.raises(RoboVisionError) as caught:
        HostSession._reconcile(credential, host_generation=host_generation, transaction="rvtx:t")
    assert caught.value.payload.code == "CREDENTIAL_STATE_DIVERGED"
    # Nothing was promoted, and nothing was discarded either.
    assert credential.secret == "secret-a"
    assert credential.next_secret == "secret-b"
    assert caught.value.payload.data["host_generation"] == host_generation
    assert caught.value.payload.data["session_generation"] == 0


def test_a_generation_behind_the_session_is_also_divergence():
    """The host cannot un-rotate. A lower number is not an older truth."""
    credential = _Credential(generation=2, secret="secret-c")
    with pytest.raises(RoboVisionError) as caught:
        HostSession._reconcile(credential, host_generation=1, transaction="rvtx:t")
    assert caught.value.payload.code == "CREDENTIAL_STATE_DIVERGED"
    assert credential.secret == "secret-c"


def test_a_settled_credential_reconciles_to_itself():
    credential = _Credential(generation=3, secret="secret-d")
    HostSession._reconcile(credential, host_generation=3, transaction="rvtx:t")
    assert credential.secret == "secret-d"
    assert credential.generation == 3
