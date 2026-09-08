from __future__ import annotations


def register_all(registry) -> None:
    from . import mesh, mesh_advanced, modifier, objects, scene, system, transactions, viewport

    for module in (system, scene, objects, mesh, mesh_advanced, modifier, viewport, transactions):
        module.register(registry)
