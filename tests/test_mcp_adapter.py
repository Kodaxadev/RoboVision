from pathlib import Path

import pytest

from robovision.errors import RoboVisionError
from robovision.mcp_server import _error_payload, _preview_path, _target


def test_host_targets_are_separate(monkeypatch):
    monkeypatch.delenv("ROBOVISION_BLENDER_PORT", raising=False)
    monkeypatch.delenv("ROBOVISION_UNITY_PORT", raising=False)
    assert _target("blender") == ("127.0.0.1", 9877)
    assert _target("unity") == ("127.0.0.1", 9878)


def test_unknown_host_is_rejected():
    with pytest.raises(ValueError):
        _target("maya")  # type: ignore[arg-type]


def test_mcp_server_constructs_when_extra_installed():
    mcp = pytest.importorskip("mcp")
    if not hasattr(pytest.importorskip("mcp.server"), "MCPServer"):
        # The adapter targets the mcp 2.x server API. An older SDK on the path
        # is an environment mismatch, not an adapter defect.
        pytest.skip(f"mcp {getattr(mcp, '__version__', 'unknown')} predates the MCPServer API")
    from robovision.mcp_server import build_server

    assert build_server() is not None


def test_model_image_preserves_small_png_when_pillow_available(tmp_path: Path):
    pytest.importorskip("PIL")
    from PIL import Image

    from robovision.mcp_server import _model_image

    path = tmp_path / "tiny.png"
    Image.new("RGB", (4, 4), (1, 2, 3)).save(path)
    data, mime, meta = _model_image(str(path), max_bytes=100_000)
    assert data == path.read_bytes()
    assert mime == "image/png"
    assert meta["transcoded"] is False
    assert meta["model_bytes"] == meta["source_bytes"]


def test_model_image_transcodes_when_over_the_limit(tmp_path: Path):
    """A capture too large for a client must still reach the model as pixels."""
    pytest.importorskip("PIL")
    import random

    from PIL import Image

    from robovision.mcp_server import _model_image

    # Noise resists compression, so the PNG genuinely exceeds the limit.
    rng = random.Random(1234)
    image = Image.new("RGB", (900, 900))
    image.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(900 * 900)])
    path = tmp_path / "big.png"
    image.save(path)
    source_bytes = path.stat().st_size
    limit = 120_000
    assert source_bytes > limit, "fixture is not large enough to exercise transcoding"

    data, mime, meta = _model_image(str(path), max_bytes=limit)

    assert mime == "image/jpeg"
    assert meta["transcoded"] is True
    assert meta["source_bytes"] == source_bytes
    assert meta["model_bytes"] == len(data)
    assert len(data) <= limit, "transcoded image still exceeds the client limit"
    assert data[:2] == b"\xff\xd8", "payload is not JPEG data"
    # The full-resolution artifact must survive untouched on the host.
    assert path.stat().st_size == source_bytes


def test_model_image_reports_a_missing_artifact(tmp_path: Path):
    from robovision.mcp_server import _model_image

    with pytest.raises(FileNotFoundError):
        _model_image(str(tmp_path / "absent.png"))


def test_preview_path_reads_both_artifact_shapes():
    assert _preview_path({"preview": {"path": "/tmp/a.png"}}) == "/tmp/a.png"
    assert _preview_path({"kind": "image", "path": "/tmp/b.png"}) == "/tmp/b.png"
    assert _preview_path({"raw": {"path": "/tmp/c.rgba8"}}) is None
    assert _preview_path(None) is None
    assert _preview_path("not-a-dict") is None


def test_error_payload_preserves_host_error_contract():
    payload = _error_payload(
        RoboVisionError("STALE_REVISION", "scene revision changed", data={"expected": 4}, retryable=True)
    )
    assert payload == {
        "code": "STALE_REVISION",
        "message": "scene revision changed",
        "data": {"expected": 4},
        "retryable": True,
    }


def test_error_payload_wraps_unexpected_exceptions():
    payload = _error_payload(TimeoutError("host did not answer"))
    assert payload["code"] == "TimeoutError"
    assert payload["retryable"] is False
