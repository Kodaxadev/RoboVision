from pathlib import Path

import pytest

from robovision.mcp_server import _target


def test_host_targets_are_separate(monkeypatch):
    monkeypatch.delenv("ROBOVISION_BLENDER_PORT", raising=False)
    monkeypatch.delenv("ROBOVISION_UNITY_PORT", raising=False)
    assert _target("blender") == ("127.0.0.1", 9877)
    assert _target("unity") == ("127.0.0.1", 9878)


def test_mcp_server_constructs_when_extra_installed():
    pytest.importorskip("mcp")
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
