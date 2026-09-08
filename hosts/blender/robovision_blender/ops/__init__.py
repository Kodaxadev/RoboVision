from __future__ import annotations


def register_all(registry) -> None:
    from . import mesh, modifier, objects, scene, system, transactions, viewport

    for module in (system, scene, objects, mesh, modifier, viewport, transactions):
        module.register(registry)
