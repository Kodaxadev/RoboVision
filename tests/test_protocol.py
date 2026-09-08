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
