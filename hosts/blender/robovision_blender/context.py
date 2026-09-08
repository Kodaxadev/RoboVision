from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import bpy

from .registry import HostError


def find_view3d():
    wm = bpy.context.window_manager
    for window in wm.windows:
        screen = window.screen
        if screen is None:
            continue
        for area in screen.areas:
            if area.type != "VIEW_3D":
                continue
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
            if region is None:
                continue
            return window, area, region, area.spaces.active
    raise HostError("INVALID_CONTEXT", "no open VIEW_3D area is available")


@contextmanager
def view3d_override() -> Iterator[tuple[bpy.types.Area, bpy.types.Region, bpy.types.SpaceView3D]]:
    window, area, region, space = find_view3d()
    with bpy.context.temp_override(window=window, area=area, region=region, space_data=space):
        yield area, region, space


@contextmanager
def active_object_override(obj: bpy.types.Object, *, force_object_mode: bool = True):
    """Temporarily establish explicit active/selected object state for context-bound operators."""
    view_layer = bpy.context.view_layer
    old_active = view_layer.objects.active
    old_selected = list(bpy.context.selected_objects)
    old_mode = bpy.context.mode

    try:
        if force_object_mode and old_active is not None and old_mode != "OBJECT":
            try:
                bpy.ops.object.mode_set(mode="OBJECT")
            except RuntimeError as exc:
                raise HostError("INVALID_CONTEXT", f"cannot enter Object mode: {exc}") from exc

        for selected in list(bpy.context.selected_objects):
            selected.select_set(False)
        obj.select_set(True)
        view_layer.objects.active = obj
        with bpy.context.temp_override(
            active_object=obj,
            object=obj,
            selected_objects=[obj],
            selected_editable_objects=[obj],
        ):
            yield
    finally:
        for selected in list(bpy.context.selected_objects):
            try:
                selected.select_set(False)
            except ReferenceError:
                pass
        for selected in old_selected:
            if selected.name in bpy.data.objects:
                try:
                    selected.select_set(True)
                except ReferenceError:
                    pass
        if old_active is not None and old_active.name in bpy.data.objects:
            view_layer.objects.active = old_active
            if force_object_mode and old_mode != "OBJECT":
                target_mode = old_mode.replace("_MESH", "")
                try:
                    bpy.ops.object.mode_set(mode=target_mode)
                except RuntimeError:
                    # Restoration failure is intentionally non-fatal to the completed host operation;
                    # the next inspection exposes the actual editor mode.
                    pass
