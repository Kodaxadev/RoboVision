from __future__ import annotations

import bpy
from bpy.props import IntProperty

from .runtime import RUNTIME

ADDON_ID = __package__


class RoboVisionPreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_ID

    port: IntProperty(name="Port", default=9877, min=1024, max=65535)

    def draw(self, _context):
        layout = self.layout
        layout.prop(self, "port")
        layout.label(text="RoboVision binds to 127.0.0.1 only.")


def _port(context) -> int:
    addon = context.preferences.addons.get(ADDON_ID)
    if addon and addon.preferences:
        return int(addon.preferences.port)
    return 9877


class ROBOVISION_OT_start(bpy.types.Operator):
    bl_idname = "robovision.start"
    bl_label = "Start RoboVision"
    bl_description = "Start the local RoboVision control bridge"

    def execute(self, context):
        try:
            RUNTIME.start(_port(context))
        except OSError as exc:
            self.report({"ERROR"}, f"RoboVision could not bind: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, f"RoboVision listening on 127.0.0.1:{_port(context)}")
        return {"FINISHED"}


class ROBOVISION_OT_stop(bpy.types.Operator):
    bl_idname = "robovision.stop"
    bl_label = "Stop RoboVision"

    def execute(self, _context):
        RUNTIME.stop()
        return {"FINISHED"}


class ROBOVISION_PT_panel(bpy.types.Panel):
    bl_label = "RoboVision"
    bl_idname = "ROBOVISION_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "RoboVision"

    def draw(self, context):
        layout = self.layout
        if RUNTIME.running:
            layout.label(text=f"Running — 127.0.0.1:{RUNTIME.transport.port}", icon="CHECKMARK")
            layout.label(text=f"Scene revision: {RUNTIME.revision}")
            layout.operator("robovision.stop", icon="PAUSE")
        else:
            layout.label(text="Stopped")
            layout.operator("robovision.start", icon="PLAY")
        addon = context.preferences.addons.get(ADDON_ID)
        if addon and addon.preferences:
            layout.prop(addon.preferences, "port")
        layout.separator()
        layout.label(text="Structured tools; arbitrary code disabled.")


_CLASSES = (RoboVisionPreferences, ROBOVISION_OT_start, ROBOVISION_OT_stop, ROBOVISION_PT_panel)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    RUNTIME.stop()
    for cls in reversed(_CLASSES):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
