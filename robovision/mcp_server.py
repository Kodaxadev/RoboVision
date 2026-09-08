from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
from typing import Any, Literal

from .client import RoboVisionClient
from .errors import RoboVisionError

HostName = Literal["blender", "unity"]


def _target(host: HostName) -> tuple[str, int]:
    if host == "blender":
        return "127.0.0.1", int(os.environ.get("ROBOVISION_BLENDER_PORT", "9877"))
    if host == "unity":
        return "127.0.0.1", int(os.environ.get("ROBOVISION_UNITY_PORT", "9878"))
    raise ValueError(f"unsupported RoboVision host: {host}")


def _call(host: HostName, method: str, params: dict[str, Any] | None, if_revision: int | None) -> dict[str, Any]:
    address, port = _target(host)
    with RoboVisionClient(address, port) as client:
        return client.call(method, params or {}, if_revision=if_revision)


def _model_image(path: str, max_bytes: int = 1_000_000) -> tuple[bytes, str, dict[str, Any]]:
    """Return a model-friendly image while preserving the full artifact on disk.

    PNG/JPEG files already under the target size are passed through. Larger
    images are converted to JPEG and progressively resized/encoded. This keeps
    visual feedback practical for MCP clients with conservative image limits.
    """
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(path)
    raw = source.read_bytes()
    suffix = source.suffix.lower()
    mime = "image/png" if suffix == ".png" else "image/jpeg" if suffix in {".jpg", ".jpeg"} else "application/octet-stream"
    if len(raw) <= max_bytes and mime.startswith("image/"):
        return raw, mime, {"transcoded": False, "source_bytes": len(raw), "model_bytes": len(raw)}

    from PIL import Image as PILImage

    with PILImage.open(source) as image:
        image = image.convert("RGB")
        original_size = image.size
        scale = 1.0
        best = b""
        for _ in range(8):
            for quality in (88, 80, 72, 64, 56):
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=quality, optimize=True)
                best = buffer.getvalue()
                if len(best) <= max_bytes:
                    return best, "image/jpeg", {
                        "transcoded": True,
                        "source_bytes": len(raw),
                        "model_bytes": len(best),
                        "source_size": list(original_size),
                        "model_size": list(image.size),
                        "jpeg_quality": quality,
                    }
            scale *= 0.82
            next_size = (max(64, int(original_size[0] * scale)), max(64, int(original_size[1] * scale)))
            image = image.resize(next_size, PILImage.Resampling.LANCZOS)
        return best, "image/jpeg", {
            "transcoded": True,
            "source_bytes": len(raw),
            "model_bytes": len(best),
            "source_size": list(original_size),
            "model_size": list(image.size),
            "limit_exceeded": len(best) > max_bytes,
        }


def _error_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, RoboVisionError):
        return {"code": exc.payload.code, "message": str(exc), "data": exc.payload.data, "retryable": exc.payload.retryable}
    return {"code": type(exc).__name__, "message": str(exc), "retryable": False}


def build_server():
    # Imported lazily so the core RoboVision client has no mandatory MCP dependency.
    from mcp.server import MCPServer
    from mcp.types import CallToolResult, ImageContent, TextContent

    mcp = MCPServer("RoboVision")

    @mcp.tool()
    def rv_status(host: HostName = "blender") -> dict[str, Any]:
        """Discover a connected Blender or Unity RoboVision host.

        Always call this when starting work in an unfamiliar editor session. It
        returns exact editor/version information, scene revision, security facts,
        limitations and the live structured operation list. Never assume Blender
        and Unity expose identical capabilities.
        """
        try:
            return _call(host, "system.hello", {}, None)
        except Exception as exc:
            return {"ok": False, "host": host, "error": _error_payload(exc)}

    @mcp.tool()
    def rv_call(
        host: HostName,
        method: str,
        params: dict[str, Any] | None = None,
        if_revision: int | None = None,
    ) -> dict[str, Any]:
        """Call one structured RoboVision editor operation.

        Use rv_status first to discover supported methods. For mutations, pass
        the most recently observed scene revision as if_revision whenever
        possible. For visual capture use rv_capture so the model receives pixels,
        not only an artifact path.
        """
        try:
            return _call(host, method, params, if_revision)
        except Exception as exc:
            return {"ok": False, "host": host, "method": method, "error": _error_payload(exc)}

    @mcp.tool()
    def rv_capture(
        host: HostName,
        params: dict[str, Any] | None = None,
        if_revision: int | None = None,
        max_model_bytes: int = 1_000_000,
    ) -> CallToolResult:
        """Capture the current editor view and return both evidence metadata and pixels.

        The editor host records projection/camera provenance with the artifact.
        RoboVision preserves the full-resolution local artifact and only
        transcodes a model-facing copy when needed for MCP transport limits.
        """
        capture_params = dict(params or {})
        if host == "unity":
            capture_params.setdefault("width", 1280)
            capture_params.setdefault("height", 720)
        try:
            response = _call(host, "viewport.capture", capture_params, if_revision)
            artifact = response.get("result", {}).get("artifact", {})
            path = artifact.get("path")
            if not isinstance(path, str) or not path:
                raise RuntimeError("host capture did not return an image artifact path")
            image_bytes, mime, transport_meta = _model_image(path, max(100_000, min(int(max_model_bytes), 8_000_000)))
            structured = dict(response)
            structured["model_image"] = transport_meta
            summary = json.dumps(structured, ensure_ascii=False, separators=(",", ":"))
            return CallToolResult(
                content=[
                    TextContent(text=summary),
                    ImageContent(data=base64.b64encode(image_bytes).decode("ascii"), mime_type=mime),
                ],
                structured_content=structured,
            )
        except Exception as exc:
            error = {"ok": False, "host": host, "method": "viewport.capture", "error": _error_payload(exc)}
            return CallToolResult(
                is_error=True,
                content=[TextContent(text=json.dumps(error, ensure_ascii=False))],
                structured_content=error,
            )

    return mcp


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
