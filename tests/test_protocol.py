import pytest

from robovision.errors import RoboVisionError
from robovision.protocol import PROTOCOL_VERSION, Request, Response


def test_request_round_trip():
    request = Request(method="scene.describe", params={"deep": True}, id="r1", if_revision=4)
    assert Request.from_dict(request.to_dict()) == request


def test_rejects_stale_protocol():
    with pytest.raises(RoboVisionError) as exc:
        Request.from_dict({"rv": "9.9", "id": "x", "method": "system.ping", "params": {}})
    assert exc.value.payload.code == "PROTOCOL_MISMATCH"


def test_response_shape():
    data = Response(id="r1", ok=True, revision=2, result={"pong": True}, timing_ms=1.23456).to_dict()
    assert data == {
        "rv": PROTOCOL_VERSION,
        "id": "r1",
        "ok": True,
        "revision": 2,
        "result": {"pong": True},
        "timing_ms": 1.235,
    }

def test_every_modelled_request_field_survives_a_round_trip():
    """`to_dict` emitted five fields `from_dict` did not reconstruct.

    Silently, and only on the parsing side — the half a proxy or a test harness
    uses — so a request that passed through a dict came back stripped of exactly
    the fields that make a retry safe.
    """
    full = Request(
        method="object.create",
        params={"kind": "cube", "seed": 7},
        if_revision=4,
        idempotency_key="k-1",
        attempt=3,
        expected_world="rvworld:abc",
        expected_coordinate_contract="rvcoord:def",
        contract="autonomous",
    )
    once = full.to_dict()
    twice = Request.from_dict(once)
    assert twice.to_dict() == once
    for name in ("idempotency_key", "attempt", "expected_world",
                 "expected_coordinate_contract", "contract"):
        assert getattr(twice, name) == getattr(full, name), name


def test_absent_optional_request_fields_stay_absent():
    plain = Request(method="system.ping").to_dict()
    for name in ("idempotency_key", "attempt", "expected_world",
                 "expected_coordinate_contract", "contract"):
        assert name not in plain
    assert Request.from_dict(plain).idempotency_key is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("attempt", True),          # bool is an int in Python; attempt: true is a mistake
        ("attempt", 0),
        ("attempt", -1),
        ("attempt", "2"),
        ("idempotency_key", ""),
        ("idempotency_key", 5),
        ("expected_world", ""),
        ("expected_world", 5),
        ("expected_coordinate_contract", ""),
        ("contract", "supervisor"),
        ("contract", ""),
    ],
)
def test_request_validates_rather_than_copying(field, value):
    raw = Request(method="object.create").to_dict()
    raw[field] = value
    with pytest.raises(RoboVisionError) as caught:
        Request.from_dict(raw)
    assert caught.value.payload.code == "INVALID_REQUEST"


def test_response_models_what_the_hosts_emit_and_keeps_the_rest():
    """A replay's envelope is about now; its original execution is stated."""
    raw = {
        "rv": "1.0", "id": "r-1", "ok": True, "revision": 12,
        "result": {"id": "b3d:x"}, "outcome": "applied",
        "consistency": "authoritative", "state_domain": "authored",
        "replayed": True,
        "original_execution": {"post_revision": 4, "recipe_hash": "rvrecipe:aa"},
        "some_future_field": {"kept": True},
    }
    response = Response.from_dict(raw)
    assert response.consistency == "authoritative"
    assert response.state_domain == "authored"
    assert response.replayed is True
    assert response.original_execution["post_revision"] == 4
    # The current revision is not the revision it ran at, and both are readable.
    assert response.revision == 12
    # Unknown fields are kept: a response is still useful when it carries
    # something newer than the library reading it.
    assert response.extra["some_future_field"] == {"kept": True}
    assert response.to_dict()["some_future_field"] == {"kept": True}
