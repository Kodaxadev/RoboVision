"""Guards on what the two hosts publish, in the suite that always runs.

The live proof is in the gates: `tests/blender/public_flow_gate.py` and
`tests/unity/public_flow_gate.py` run the same host-agnostic claims against real
editors. Both need an editor, and one of them needs a Unity licence, so a
divergence introduced on a Tuesday could sit unnoticed until someone had a
machine to run it on.

These are the cheap half. They read the two hosts' source and hold them to the
same contract the live gates check, so a field dropped from one editor's
`system.hello`, a readiness vocabulary that grew a sixth value on one side only,
or the return of the transaction-ownership paragraph fails in ordinary CI on the
commit that caused it.

Source-level on purpose. The Blender host imports `bpy` and the Unity host is
C#, so neither can be imported here — and a test that could only run where the
editor runs would be the thing this file exists to compensate for.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import public_flow

ROOT = Path(__file__).resolve().parents[1]
BLENDER = ROOT / "hosts" / "blender" / "robovision_blender"
UNITY = ROOT / "hosts" / "unity" / "Packages" / "com.kodaxa.robovision" / "Editor"

BLENDER_HELLO = BLENDER / "ops" / "system.py"
BLENDER_HEALTH = BLENDER / "health.py"
UNITY_HELLO = UNITY / "RoboVisionHost.cs"
UNITY_HEALTH = UNITY / "RoboVisionHealth.cs"
UNITY_READINESS = UNITY / "RoboVisionHealthReadiness.cs"


def _read(path: Path) -> str:
    assert path.is_file(), f"{path} is missing"
    return path.read_text(encoding="utf-8")


def _hosts() -> dict[str, str]:
    return {"blender": _read(BLENDER_HELLO), "unity": _read(UNITY_HELLO)}


def _health_sources() -> dict[str, str]:
    return {
        "blender": _read(BLENDER_HEALTH),
        "unity": _read(UNITY_HEALTH) + _read(UNITY_READINESS),
    }


@pytest.mark.parametrize("field", public_flow.HELLO_REQUIRED)
def test_both_hosts_publish_every_pin(field: str):
    """An external agent must be able to construct a strict call from `hello` alone.

    Unity enforced its coordinate contract internally while publishing neither it
    nor its units, which left a model outside the package unable to supply a pin
    the host would then refuse it for omitting. The only way to discover the
    value was to read the package's own constant, which an external client
    cannot do.
    """
    for host, source in _hosts().items():
        assert f'"{field}"' in source, f"the {host} host's system.hello does not publish {field}"


def test_neither_host_publishes_a_coordinate_contract_a_client_must_hard_code():
    """The contract is derived and published, never a value a caller can only guess."""
    blender, unity = _hosts()["blender"], _hosts()["unity"]
    assert "coordinate_contract()" in blender
    assert "RoboVisionRecipe.CoordinateContract()" in unity
    assert "units()" in blender and "RoboVisionRecipe.Units()" in unity


def test_the_two_hosts_do_not_claim_the_same_frame():
    """Publishing the contract is only useful if the two of them actually differ.

    Blender authors Z up, right-handed; Unity Y up, left-handed. If both hosts
    ever published the same frame identity, pinning it would stop catching the
    mistake a cross-editor agent is best placed to make, and every test above
    would still pass.
    """
    blender_frame = _read(BLENDER / "recipe.py")
    unity_frame = _read(UNITY / "RoboVisionRecipe.cs")
    assert "rvframe:blender_z_up_right_handed_meters" in blender_frame
    assert "rvframe:unity_y_up_left_handed_metres" in unity_frame


@pytest.mark.parametrize("host", ["blender", "unity"])
def test_transaction_ownership_is_a_machine_readable_fact(host: str):
    source = _hosts()[host]
    assert '"connection"' in source, f"the {host} host does not state ownership as a fact"
    assert "orphan_requires_adoption" in source, (
        f"the {host} host does not state that an orphan requires adoption")


@pytest.mark.parametrize("phrase", public_flow.FORBIDDEN_OWNERSHIP_PHRASES)
def test_stale_ownership_prose_cannot_come_back(phrase: str):
    """The paragraph that outlived the policy it described.

    Unity's `system.hello` said an orphaned transaction could be committed or
    rolled back by any client, which stopped being true the moment adoption
    started requiring the recovery credential. Prose drifts; two structured facts
    cannot. This is here so the paragraph cannot be helpfully restored.
    """
    for host, source in _hosts().items():
        assert phrase not in source.lower(), (
            f"the {host} host publishes transaction ownership prose that the code no "
            f"longer implements: {phrase!r}")


@pytest.mark.parametrize("host", ["blender", "unity"])
def test_both_hosts_register_health(host: str):
    source = _hosts()[host]
    assert '"system.health"' in source, f"the {host} host does not publish system.health"
    # Authoritative, because the report's whole claim is that the world and
    # revision it carries belong to state the host has just read.
    assert "authoritative" in source.lower()


@pytest.mark.parametrize("status", sorted(public_flow.STATUSES))
def test_both_hosts_share_one_readiness_vocabulary(status: str):
    for host, source in _health_sources().items():
        assert f'"{status}"' in source, (
            f"the {host} host's health does not use the shared status {status!r}")


@pytest.mark.parametrize("name", public_flow.READY_FOR)
def test_both_hosts_answer_the_same_five_questions(name: str):
    for host, source in _health_sources().items():
        assert f'"{name}"' in source, (
            f"the {host} host's health does not answer {name}")


@pytest.mark.parametrize("name", public_flow.SUBSYSTEMS)
def test_both_hosts_expose_the_same_thin_subsystem_facts(name: str):
    for host, source in _health_sources().items():
        assert f'"{name}"' in source, (
            f"the {host} host's health omits the {name} subsystem")


@pytest.mark.parametrize("situation", [
    "none",
    "active_owned_by_this_connection",
    "active_foreign",
    "orphaned_adoption_required",
    "contaminated",
    "recovery_uncertain",
])
def test_both_hosts_name_transaction_situations_identically(situation: str):
    """One vocabulary, or a cross-editor client ends up with a per-host branch."""
    for host, source in _health_sources().items():
        assert f'"{situation}"' in source, (
            f"the {host} host's health cannot report the {situation!r} situation")


def test_health_never_claims_the_caller_holds_a_credential():
    """Only the client library knows that, and only it may say so.

    A host that reported `recoverable_by_this_session` would be reassuring
    whoever happened to reach the port, which is precisely the authority
    adoption exists to withhold.
    """
    for host, source in _health_sources().items():
        assert "recoverable_by_this_session" not in source, (
            f"the {host} host's health claims knowledge of a caller's credential")
        assert "recovery_token" not in source, (
            f"the {host} host's health mentions the recovery secret")
