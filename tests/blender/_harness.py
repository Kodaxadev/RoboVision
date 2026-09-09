"""Shared plumbing for RoboVision Blender integration gates.

Every gate drives the host exactly the way the add-on does: a real
`RoboVisionRuntime` with editor change notifications installed, addressed
through `dispatch` with protocol-shaped requests.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any

import bpy

ROOT = Path(__file__).resolve().parents[2]
HOST_ROOT = ROOT / "hosts" / "blender"
if str(HOST_ROOT) not in sys.path:
    sys.path.insert(0, str(HOST_ROOT))

from robovision_blender.runtime import PROTOCOL_VERSION, RoboVisionRuntime  # noqa: E402


class GateFailure(AssertionError):
    pass


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise GateFailure(message)


def clean_scene() -> None:
    """Reset to a deterministic baseline without depending on context operators."""
    if bpy.context.mode != "OBJECT" and bpy.context.view_layer.objects.active is not None:
        try:
            bpy.ops.object.mode_set(mode="OBJECT")
        except RuntimeError:
            pass
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for mesh in list(bpy.data.meshes):
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


class Host:
    """A gate's view of the running RoboVision host."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.serial = 0
        self.calls: list[dict[str, Any]] = []
        self.runtime = RoboVisionRuntime()
        self.runtime.install_handlers()

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        if_revision: int | None = None,
        ok: bool = True,
        code: str | None = None,
    ) -> dict[str, Any]:
        self.serial += 1
        request: dict[str, Any] = {
            "rv": PROTOCOL_VERSION,
            "id": f"{self.label}-{self.serial}",
            "method": method,
            "params": params or {},
        }
        if if_revision is not None:
            request["if_revision"] = if_revision
        response = self.runtime.dispatch(request)
        self.calls.append({"request": request, "response": response})
        if ok:
            expect(response.get("ok") is True, f"{method} failed: {response}")
        else:
            expect(response.get("ok") is False, f"{method} unexpectedly succeeded: {response}")
            if code is not None:
                actual = response.get("error", {}).get("code")
                expect(actual == code, f"{method} expected {code} but returned {actual}: {response}")
        return response

    def result(self, method: str, params: dict[str, Any] | None = None, **kwargs) -> dict[str, Any]:
        return self.call(method, params, **kwargs)["result"]

    def fingerprint(self) -> str:
        return self.result("scene.snapshot", {"level": "deep"})["fingerprint"]

    def write_trace(self, path: Path, **extra: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"blender": bpy.app.version_string, "protocol": PROTOCOL_VERSION, **extra, "calls": self.calls},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


def artifact_dir(name: str) -> Path:
    directory = ROOT / "artifacts" / name
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def run_gate(name: str, main, artifact: str) -> None:
    """Run a gate body, persist a trace either way, and exit Blender on success."""
    import traceback

    trace_path = artifact_dir(artifact) / "trace.json"
    try:
        main()
    except Exception:
        traceback.print_exc()
        try:
            trace_path.write_text(
                json.dumps({"error": traceback.format_exc()}, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass
        raise
    else:
        print(f"ROBOVISION_{name}_PASS")
        bpy.ops.wm.quit_blender()
