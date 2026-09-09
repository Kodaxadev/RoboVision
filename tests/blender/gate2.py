from __future__ import annotations

import json
from pathlib import Path
import sys
import traceback

import bpy

ROOT = Path(__file__).resolve().parents[2]
HOST_ROOT = ROOT / "hosts" / "blender"
if str(HOST_ROOT) not in sys.path:
    sys.path.insert(0, str(HOST_ROOT))


from robovision_blender.runtime import PROTOCOL_VERSION, RoboVisionRuntime  # noqa: E402

ARTIFACT_DIR = ROOT / "artifacts" / "blender-gate2"
ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
TRACE_PATH = ARTIFACT_DIR / "trace.json"


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def clean_scene() -> None:
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for material in list(bpy.data.materials):
        if material.users == 0:
            bpy.data.materials.remove(material)


def rgba_codes(path: Path) -> set[int]:
    raw = path.read_bytes()
    expect(len(raw) % 4 == 0, f"invalid RGBA8 byte count: {path}")
    codes: set[int] = set()
    for offset in range(0, len(raw), 4):
        r, g, b, a = raw[offset : offset + 4]
        if a == 0:
            continue
        code = (r << 16) | (g << 8) | b
        if code:
            codes.add(code)
    return codes


def main() -> None:
    clean_scene()
    runtime = RoboVisionRuntime()
    # install_handlers() is what the shipped add-on calls, so the gate exercises
    # the same out-of-band change detection rather than a test-only shortcut.
    runtime.install_handlers()
    calls = []
    serial = 0

    def call(method: str, params: dict | None = None, *, if_revision: int | None = None):
        nonlocal serial
        serial += 1
        request = {"rv": PROTOCOL_VERSION, "id": f"gate2-{serial}", "method": method, "params": params or {}}
        if if_revision is not None:
            request["if_revision"] = if_revision
        response = runtime.dispatch(request)
        calls.append({"request": request, "response": response})
        expect(response.get("ok") is True, f"{method} failed: {response}")
        return response

    hello = call("system.hello")
    methods = {entry["name"] for entry in hello["result"]["capabilities"]}
    expect("perception.capture_bundle" in methods, "perception bundle is not advertised")

    left = call(
        "object.create",
        {"kind": "cube", "name": "Gate2_Left", "size": 2.0, "location": [-1.4, 0.0, 0.0]},
        if_revision=runtime.revision,
    )["result"]
    right = call(
        "object.create",
        {"kind": "cube", "name": "Gate2_Right", "size": 2.0, "location": [1.4, 0.0, 0.0]},
        if_revision=runtime.revision,
    )["result"]

    # Fixture-only material setup. The operation under test is perception; the
    # agent-facing path remains structured and contains no arbitrary-code tool.
    material_left = bpy.data.materials.new("Gate2_Red")
    material_left.diffuse_color = (0.8, 0.05, 0.02, 1.0)
    material_right = bpy.data.materials.new("Gate2_Blue")
    material_right.diffuse_color = (0.02, 0.1, 0.8, 1.0)
    bpy.data.objects["Gate2_Left"].data.materials.append(material_left)
    bpy.data.objects["Gate2_Right"].data.materials.append(material_right)
    runtime._refresh_dirty_state()

    call("viewport.focus", {"object": left["id"], "padding": 3.0})
    result = call(
        "perception.capture_bundle",
        {
            "directory": str(ARTIFACT_DIR / "bundle"),
            "width": 512,
            "height": 384,
            "passes": ["solid", "wireframe", "depth", "normals", "object_ids", "material_ids"],
        },
    )["result"]

    expect(result["view"]["capture_size"] == {"width": 512, "height": 384}, "capture size provenance is wrong")
    expected_passes = {"solid", "wireframe", "depth", "normals", "object_ids", "material_ids"}
    expect(expected_passes.issubset(result["passes"]), f"missing perception artifacts: {result['passes'].keys()}")

    object_raw = Path(result["passes"]["object_ids"]["raw"]["path"])
    material_raw = Path(result["passes"]["material_ids"]["raw"]["path"])
    normal_raw = Path(result["passes"]["normals"]["raw"]["path"])
    depth_raw = Path(result["passes"]["depth"]["raw"]["path"])
    expected_rgba_bytes = 512 * 384 * 4
    expect(object_raw.stat().st_size == expected_rgba_bytes, "object-id raw buffer size mismatch")
    expect(material_raw.stat().st_size == expected_rgba_bytes, "material-id raw buffer size mismatch")
    expect(normal_raw.stat().st_size == expected_rgba_bytes, "normal raw buffer size mismatch")
    expect(depth_raw.stat().st_size == 512 * 384 * 4, "float32 depth buffer size mismatch")

    object_codes = rgba_codes(object_raw)
    material_codes = rgba_codes(material_raw)
    object_palette = {int(code) for code in result["palettes"]["object_ids"]}
    material_palette = {int(code) for code in result["palettes"]["material_ids"]}
    expect(len(object_codes) >= 2, f"object pass did not resolve both cubes: {object_codes}")
    expect(object_codes.issubset(object_palette), f"object pixels reference codes missing from palette: {object_codes - object_palette}")
    expect(len(material_codes) >= 2, f"material pass did not resolve both materials: {material_codes}")
    expect(material_codes.issubset(material_palette), f"material pixels reference codes missing from palette: {material_codes - material_palette}")

    depth_stats = result["passes"]["depth"]["statistics"]
    expect(depth_stats["foreground_pixels"] > 100, f"depth pass found no useful geometry: {depth_stats}")
    expect(depth_stats["min"] is not None and depth_stats["max"] is not None, "depth statistics are incomplete")

    normals = normal_raw.read_bytes()
    unique_normal_rgb = {tuple(normals[offset : offset + 3]) for offset in range(0, len(normals), 4) if normals[offset + 3] != 0}
    expect(len(unique_normal_rgb) >= 3, "normal pass lacks distinct surface orientations")

    for name in ("solid", "wireframe"):
        path = Path(result["passes"][name]["path"])
        expect(path.is_file() and path.stat().st_size > 1024, f"{name} pass was not written")

    TRACE_PATH.write_text(
        json.dumps(
            {
                "blender": bpy.app.version_string,
                "objects": [left["id"], right["id"]],
                "object_codes": sorted(object_codes),
                "material_codes": sorted(material_codes),
                "depth": depth_stats,
                "calls": calls,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print("ROBOVISION_GATE2_PASS", sorted(object_codes), sorted(material_codes))


try:
    main()
except Exception:
    traceback.print_exc()
    try:
        TRACE_PATH.write_text(json.dumps({"error": traceback.format_exc()}, indent=2), encoding="utf-8")
    except Exception:
        pass
    raise
else:
    bpy.ops.wm.quit_blender()
