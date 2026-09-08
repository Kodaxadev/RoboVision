bl_info = {
    "name": "RoboVision",
    "author": "Kodaxa",
    "version": (0, 1, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > RoboVision",
    "description": "Structured, perception-first AI control plane for Blender",
    "category": "3D View",
}

from .addon import register, unregister

__all__ = ["register", "unregister"]
