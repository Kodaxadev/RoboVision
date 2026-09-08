from __future__ import annotations


def register_all(registry) -> None:
    from . import mesh, mesh_advanced, mesh_query, modifier, objects, perception, scene, system, transactions, viewport

    for module in (system, scene, objects, mesh, mesh_query, mesh_advanced, modifier, viewport, perception, transactions):
        module.register(registry)
