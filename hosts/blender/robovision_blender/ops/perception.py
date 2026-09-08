from __future__ import annotations

from array import array
from collections import defaultdict
from pathlib import Path
import os
import tempfile
import time
import uuid
from typing import Any, Iterator

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Vector

from ..context import view3d_override
from ..identity import object_id
from ..registry import HostError
from .viewport import _metadata


SUPPORTED_PASSES = {"color", "solid", "wireframe", "depth", "normals", "object_ids", "material_ids"}


def _flatten(value) -> Iterator[float | int]:
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _flatten(item)
    else:
        yield value


def _buffer_values(buffer) -> list[float | int]:
    return list(_flatten(buffer.to_list()))


def _save_png(path: Path, values, width: int, height: int, *, ubyte: bool = True, data_image: bool = True) -> None:
    flat = values if isinstance(values, list) else list(values)
    expected = width * height * 4
    if len(flat) != expected:
        raise HostError("HOST_EXCEPTION", f"pixel buffer size mismatch for {path.name}: {len(flat)} != {expected}")
    pixels = [float(value) / 255.0 for value in flat] if ubyte else [float(value) for value in flat]
    image = bpy.data.images.new(
        f"RoboVision-{uuid.uuid4().hex}",
        width=width,
        height=height,
        alpha=True,
        float_buffer=False,
    )
    try:
        if data_image:
            try:
                image.colorspace_settings.name = "Non-Color"
            except TypeError:
                pass
        image.pixels.foreach_set(pixels)
        image.filepath_raw = str(path)
        image.file_format = "PNG"
        image.save()
    finally:
        bpy.data.images.remove(image)


def _artifact(path: Path, kind: str, mime: str, **extra) -> dict[str, Any]:
    result = {"kind": kind, "mime": mime, "path": str(path), "bytes": os.path.getsize(path)}
    result.update(extra)
    return result


def _palette_color(index: int) -> tuple[float, float, float, float]:
    if index <= 0 or index > 0xFFFFFF:
        raise HostError("RESOURCE_LIMIT", "perception pass exceeded 24-bit id palette capacity")
    return (
        ((index >> 16) & 0xFF) / 255.0,
        ((index >> 8) & 0xFF) / 255.0,
        (index & 0xFF) / 255.0,
        1.0,
    )


def _palette_rgb(index: int) -> list[int]:
    return [(index >> 16) & 0xFF, (index >> 8) & 0xFF, index & 0xFF]


def _instance_key(source_id: str, is_instance: bool, persistent_id: tuple[int, ...]) -> str:
    if not is_instance:
        return source_id
    return f"b3di:{source_id.removeprefix('b3d:')}:{'.'.join(str(value) for value in persistent_id)}"


def _iter_geometry_instances(depsgraph):
    # DepsgraphObjectInstance values are iterator-owned and must not be retained.
    # Everything required by a draw call is copied before the iterator advances.
    for item in depsgraph.object_instances:
        obj_eval = item.object
        if obj_eval is None or not item.show_self:
            continue
        source = obj_eval.original
        try:
            source_id = object_id(source)
        except Exception:
            continue
        matrix = item.matrix_world.copy()
        persistent_id = tuple(int(value) for value in item.persistent_id)
        is_instance = bool(item.is_instance)
        instance_id = _instance_key(source_id, is_instance, persistent_id)

        temporary = False
        if obj_eval.type == "MESH":
            mesh = obj_eval.data
        elif obj_eval.type in {"CURVE", "SURFACE", "FONT", "META"}:
            mesh = obj_eval.to_mesh()
            temporary = True
        else:
            continue
        if mesh is None:
            continue
        try:
            mesh.calc_loop_triangles()
            yield {
                "object": obj_eval,
                "source": source,
                "source_id": source_id,
                "instance_id": instance_id,
                "is_instance": is_instance,
                "persistent_id": persistent_id,
                "matrix_world": matrix,
                "mesh": mesh,
            }
        finally:
            if temporary:
                obj_eval.to_mesh_clear()


def _mesh_batch(shader, mesh):
    if not mesh.vertices or not mesh.loop_triangles:
        return None
    positions = [tuple(vertex.co) for vertex in mesh.vertices]
    triangles = [tuple(triangle.vertices) for triangle in mesh.loop_triangles]
    return batch_for_shader(shader, "TRIS", {"pos": positions}, indices=triangles)


def _draw_id_pass(width: int, height: int, view_matrix: Matrix, projection_matrix: Matrix, depsgraph, mode: str):
    shader = gpu.shader.from_builtin("UNIFORM_COLOR")
    offscreen = gpu.types.GPUOffScreen(width, height, format="RGBA8")
    palette: dict[str, Any] = {}
    material_codes: dict[tuple[str, str], int] = {}
    next_code = 1
    color_values = None
    depth_values = None
    try:
        with offscreen.bind():
            framebuffer = gpu.state.active_framebuffer_get()
            framebuffer.viewport_set(0, 0, width, height)
            framebuffer.clear(color=(0.0, 0.0, 0.0, 0.0), depth=1.0)
            gpu.state.blend_set("NONE")
            gpu.state.depth_test_set("LESS_EQUAL")
            gpu.state.depth_mask_set(True)
            gpu.state.face_culling_set("NONE")
            with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                gpu.matrix.load_matrix(view_matrix)
                gpu.matrix.load_projection_matrix(projection_matrix)
                for entry in _iter_geometry_instances(depsgraph):
                    mesh = entry["mesh"]
                    matrix = entry["matrix_world"]
                    with gpu.matrix.push_pop():
                        gpu.matrix.multiply_matrix(matrix)
                        if mode == "object":
                            batch = _mesh_batch(shader, mesh)
                            if batch is None:
                                continue
                            code = next_code
                            next_code += 1
                            shader.bind()
                            shader.uniform_float("color", _palette_color(code))
                            batch.draw(shader)
                            palette[str(code)] = {
                                "rgb": _palette_rgb(code),
                                "instance": entry["instance_id"],
                                "object": entry["source_id"],
                                "object_name": entry["source"].name_full,
                                "is_instance": entry["is_instance"],
                                "persistent_id": list(entry["persistent_id"]),
                            }
                        else:
                            grouped: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
                            for triangle in mesh.loop_triangles:
                                slot = int(mesh.polygons[triangle.polygon_index].material_index)
                                grouped[slot].append(tuple(triangle.vertices))
                            positions = [tuple(vertex.co) for vertex in mesh.vertices]
                            for slot, triangles in grouped.items():
                                material = mesh.materials[slot] if slot < len(mesh.materials) else None
                                material_name = material.name_full if material is not None else "<none>"
                                library = material.library.filepath if material is not None and material.library is not None else ""
                                material_key = (library, material_name)
                                code = material_codes.get(material_key)
                                if code is None:
                                    code = next_code
                                    next_code += 1
                                    material_codes[material_key] = code
                                    palette[str(code)] = {
                                        "rgb": _palette_rgb(code),
                                        "material": material_name,
                                        "library": library or None,
                                    }
                                batch = batch_for_shader(shader, "TRIS", {"pos": positions}, indices=triangles)
                                shader.bind()
                                shader.uniform_float("color", _palette_color(code))
                                batch.draw(shader)
            color_values = _buffer_values(framebuffer.read_color(0, 0, width, height, 4, 0, "UBYTE"))
            if mode == "object":
                depth_values = [float(value) for value in _buffer_values(framebuffer.read_depth(0, 0, width, height))]
    finally:
        gpu.state.depth_mask_set(False)
        gpu.state.depth_test_set("NONE")
        gpu.state.blend_set("NONE")
        offscreen.free()
    return color_values, depth_values, palette


def _normal_shader():
    interface = gpu.types.GPUStageInterfaceInfo("robovision_normal_iface")
    interface.smooth("VEC3", "normal_interp")
    info = gpu.types.GPUShaderCreateInfo()
    info.push_constant("MAT4", "view_projection")
    info.vertex_in(0, "VEC3", "position")
    info.vertex_in(1, "VEC3", "normal")
    info.vertex_out(interface)
    info.fragment_out(0, "VEC4", "FragColor")
    info.vertex_source(
        "void main() {"
        "normal_interp = normal;"
        "gl_Position = view_projection * vec4(position, 1.0);"
        "}"
    )
    info.fragment_source(
        "void main() {"
        "vec3 n = normalize(normal_interp);"
        "FragColor = vec4(n * 0.5 + vec3(0.5), 1.0);"
        "}"
    )
    shader = gpu.shader.create_from_info(info)
    return shader


def _draw_normals(width: int, height: int, view_projection: Matrix, depsgraph):
    shader = _normal_shader()
    offscreen = gpu.types.GPUOffScreen(width, height, format="RGBA8")
    values = None
    try:
        with offscreen.bind():
            framebuffer = gpu.state.active_framebuffer_get()
            framebuffer.viewport_set(0, 0, width, height)
            framebuffer.clear(color=(0.5, 0.5, 0.5, 0.0), depth=1.0)
            gpu.state.blend_set("NONE")
            gpu.state.depth_test_set("LESS_EQUAL")
            gpu.state.depth_mask_set(True)
            gpu.state.face_culling_set("NONE")
            shader.bind()
            shader.uniform_float("view_projection", view_projection)
            for entry in _iter_geometry_instances(depsgraph):
                mesh = entry["mesh"]
                matrix = entry["matrix_world"]
                normal_matrix = matrix.to_3x3().inverted_safe().transposed()
                positions: list[tuple[float, float, float]] = []
                normals: list[tuple[float, float, float]] = []
                for triangle in mesh.loop_triangles:
                    polygon = mesh.polygons[triangle.polygon_index]
                    normal = normal_matrix @ polygon.normal
                    if normal.length_squared:
                        normal.normalize()
                    world_vertices = [matrix @ mesh.vertices[index].co for index in triangle.vertices]
                    positions.extend(tuple(point) for point in world_vertices)
                    normals.extend((tuple(normal), tuple(normal), tuple(normal)))
                if not positions:
                    continue
                batch = batch_for_shader(shader, "TRIS", {"position": positions, "normal": normals})
                batch.draw(shader)
            values = _buffer_values(framebuffer.read_color(0, 0, width, height, 4, 0, "UBYTE"))
    finally:
        gpu.state.depth_mask_set(False)
        gpu.state.depth_test_set("NONE")
        gpu.state.blend_set("NONE")
        offscreen.free()
    return values


def _draw_viewport_pass(width: int, height: int, scene, view_layer, space, region, view_matrix, projection_matrix, shading: str):
    offscreen = gpu.types.GPUOffScreen(width, height, format="RGBA8")
    old_shading = space.shading.type
    old_overlays = space.overlay.show_overlays
    try:
        space.shading.type = shading
        space.overlay.show_overlays = False
        offscreen.draw_view3d(
            scene,
            view_layer,
            space,
            region,
            view_matrix,
            projection_matrix,
            do_color_management=True,
            draw_background=True,
        )
        with offscreen.bind():
            framebuffer = gpu.state.active_framebuffer_get()
            return _buffer_values(framebuffer.read_color(0, 0, width, height, 4, 0, "UBYTE"))
    finally:
        space.shading.type = old_shading
        space.overlay.show_overlays = old_overlays
        offscreen.free()


def _depth_preview(depth: list[float]) -> tuple[list[float], dict[str, Any]]:
    foreground = [value for value in depth if value < 0.999999]
    if not foreground:
        rgba = [0.0, 0.0, 0.0, 1.0] * len(depth)
        return rgba, {"foreground_pixels": 0, "min": None, "max": None}
    minimum = min(foreground)
    maximum = max(foreground)
    span = max(maximum - minimum, 1e-12)
    rgba: list[float] = []
    for value in depth:
        intensity = 0.0 if value >= 0.999999 else 1.0 - ((value - minimum) / span)
        rgba.extend((intensity, intensity, intensity, 1.0))
    return rgba, {"foreground_pixels": len(foreground), "min": minimum, "max": maximum}


def capture_bundle(params, runtime):
    requested = params.get("passes", ["color", "solid", "wireframe", "depth", "normals", "object_ids", "material_ids"])
    if not isinstance(requested, list) or not requested or any(not isinstance(item, str) for item in requested):
        raise HostError("INVALID_PARAMS", "passes must be a non-empty array of strings")
    passes = []
    for item in requested:
        normalized = item.lower()
        if normalized not in SUPPORTED_PASSES:
            raise HostError("INVALID_PARAMS", f"unsupported perception pass: {item}")
        if normalized not in passes:
            passes.append(normalized)

    output = params.get("directory")
    if output is None:
        directory = Path(tempfile.gettempdir()) / "robovision" / f"bundle-{time.time_ns()}-{uuid.uuid4().hex[:8]}"
    else:
        directory = Path(str(output)).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)

    with view3d_override() as (area, region, space):
        base_width = max(1, int(region.width))
        base_height = max(1, int(region.height))
        width_raw = params.get("width")
        height_raw = params.get("height")
        if width_raw is None and height_raw is None:
            width, height = base_width, base_height
        elif width_raw is not None and height_raw is None:
            width = int(width_raw)
            height = max(1, round(width * base_height / base_width))
        elif height_raw is not None and width_raw is None:
            height = int(height_raw)
            width = max(1, round(height * base_width / base_height))
        else:
            width, height = int(width_raw), int(height_raw)
        if width < 16 or height < 16 or width > 4096 or height > 4096:
            raise HostError("INVALID_PARAMS", "perception width/height must be between 16 and 4096")

        rv3d = space.region_3d
        view_matrix = rv3d.view_matrix.copy()
        projection_matrix = rv3d.window_matrix.copy()
        view_projection = projection_matrix @ view_matrix
        metadata = _metadata(area, region, space, runtime)
        metadata["capture_size"] = {"width": width, "height": height}
        metadata["alignment"] = "all passes use the same view_matrix and projection_matrix"
        metadata["projection_matrix"] = [[float(value) for value in row] for row in projection_matrix]

        depsgraph = bpy.context.evaluated_depsgraph_get()
        artifacts: dict[str, Any] = {}
        palettes: dict[str, Any] = {}
        object_depth: list[float] | None = None

        viewport_passes = {
            "color": space.shading.type,
            "solid": "SOLID",
            "wireframe": "WIREFRAME",
        }
        for name, shading in viewport_passes.items():
            if name not in passes:
                continue
            values = _draw_viewport_pass(width, height, bpy.context.scene, bpy.context.view_layer, space, region, view_matrix, projection_matrix, shading)
            path = directory / f"{name}.png"
            _save_png(path, values, width, height, ubyte=True, data_image=False)
            artifacts[name] = _artifact(path, "image", "image/png", encoding="rgba8")

        if "object_ids" in passes or "depth" in passes:
            object_values, object_depth, object_palette = _draw_id_pass(width, height, view_matrix, projection_matrix, depsgraph, "object")
            if "object_ids" in passes:
                preview = directory / "object_ids.png"
                raw = directory / "object_ids.rgba8"
                _save_png(preview, object_values, width, height, ubyte=True, data_image=True)
                raw.write_bytes(bytes(int(value) & 0xFF for value in object_values))
                artifacts["object_ids"] = {
                    "preview": _artifact(preview, "image", "image/png", encoding="rgb24-id"),
                    "raw": _artifact(raw, "data", "application/octet-stream", encoding="rgba8", width=width, height=height, origin="lower-left"),
                }
                palettes["object_ids"] = object_palette

        if "material_ids" in passes:
            material_values, _, material_palette = _draw_id_pass(width, height, view_matrix, projection_matrix, depsgraph, "material")
            preview = directory / "material_ids.png"
            raw = directory / "material_ids.rgba8"
            _save_png(preview, material_values, width, height, ubyte=True, data_image=True)
            raw.write_bytes(bytes(int(value) & 0xFF for value in material_values))
            artifacts["material_ids"] = {
                "preview": _artifact(preview, "image", "image/png", encoding="rgb24-id"),
                "raw": _artifact(raw, "data", "application/octet-stream", encoding="rgba8", width=width, height=height, origin="lower-left"),
            }
            palettes["material_ids"] = material_palette

        if "normals" in passes:
            normal_values = _draw_normals(width, height, view_projection, depsgraph)
            preview = directory / "normals.png"
            raw = directory / "normals.rgba8"
            _save_png(preview, normal_values, width, height, ubyte=True, data_image=True)
            raw.write_bytes(bytes(int(value) & 0xFF for value in normal_values))
            artifacts["normals"] = {
                "preview": _artifact(preview, "image", "image/png", encoding="world-normal-rgb", decode="normal = rgb/255*2-1"),
                "raw": _artifact(raw, "data", "application/octet-stream", encoding="rgba8", width=width, height=height, origin="lower-left"),
            }

        if "depth" in passes:
            if object_depth is None:
                _, object_depth, _ = _draw_id_pass(width, height, view_matrix, projection_matrix, depsgraph, "object")
            raw = directory / "depth.f32"
            raw.write_bytes(array("f", object_depth).tobytes())
            preview_values, statistics = _depth_preview(object_depth)
            preview = directory / "depth.png"
            _save_png(preview, preview_values, width, height, ubyte=False, data_image=True)
            artifacts["depth"] = {
                "preview": _artifact(preview, "image", "image/png", encoding="normalized-preview-only"),
                "raw": _artifact(
                    raw,
                    "data",
                    "application/octet-stream",
                    encoding="little-endian-float32-window-depth-0-1",
                    width=width,
                    height=height,
                    origin="lower-left",
                    background=1.0,
                ),
                "statistics": statistics,
            }

    return {
        "bundle": str(directory),
        "passes": artifacts,
        "palettes": palettes,
        "view": metadata,
        "scene_revision": runtime.revision,
    }


def register(registry) -> None:
    registry.add("perception.capture_bundle", capture_bundle, evidence=True, requires_ui=True, stability="alpha")
