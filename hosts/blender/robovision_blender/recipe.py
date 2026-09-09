"""What computation was requested, hashed so two of them can be compared.

Four identifiers answer four different questions, and collapsing any pair of them
loses something:

- `request_id` — which wire request is this?
- `idempotency_key` — is this another delivery of the same intended side effect?
- `attempt` — which delivery of that logical invocation?
- `recipe_hash` — what deterministic computation was asked for?
- seeds — which stochastic branch of that recipe?

The recipe hash deliberately excludes transport identity. Two deliveries of one
logical invocation share a recipe hash *and* a key; two deliberate executions of
the same computation share a recipe hash and must have different keys, because an
artist loop running the same recipe in two candidate branches must get two
results rather than watching the second vanish as a network duplicate.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .protocol import HOST_VERSION

# The frame every operation on this host executes in, recorded with every recipe.
#
# Not a shared cross-editor space: this is Blender's native authoring frame, and
# saying so is the point. An operation may not casually change forward, up or
# handedness, and converting to some other convention is a later, explicit
# export operation with a recipe and a proof of its own. Recording the frame is
# what makes "the mesh came out rotated but the task said success" detectable
# rather than a story about someone's export settings.
CANONICAL_FRAME = "rvframe:blender_z_up_right_handed_meters"

RECIPE_SCHEMA = 2


def environment() -> dict[str, Any]:
    """What was running, in enough detail to tell two executions apart.

    A host version alone is only implementation identity if it is guaranteed to
    change whenever execution semantics change, and nothing guarantees that. The
    editor doing the work is part of the environment too: the same RoboVision
    build against two Blender versions is not the same computation, and a recipe
    that could not say so would claim a reproducibility it has no basis for.

    This is deliberately versioned build identity rather than source hashing.
    Neither proves identical output — only an output hash does, and recording
    those is later work — so nothing here should be read as more than "the same
    environment asked for the same thing".
    """
    import bpy

    return {
        "robovision": HOST_VERSION,
        "recipe_schema": RECIPE_SCHEMA,
        "editor": "blender",
        "editor_version": bpy.app.version_string,
        "frame": CANONICAL_FRAME,
        **units(),
    }


def units() -> dict[str, Any]:
    """What one unit of this scene actually is.

    RoboVision's canonical frame means **one Blender unit is one metre**. Blender
    keeps a `scale_length` that says how many metres one unit represents, and it
    is a scene property a user can change: at 0.01, geometry that measures 2
    units is 2cm, and an export that resolves units differently from the host
    would rescale the asset while every operation still reported success. That is
    exactly the class of silent failure this project exists to catch, so the
    value is recorded in every recipe and reported rather than assumed.
    """
    import bpy

    scene = getattr(bpy.context, "scene", None)
    settings = getattr(scene, "unit_settings", None) if scene else None
    scale = float(getattr(settings, "scale_length", 1.0) or 1.0)
    return {
        "unit_scale_length": round(scale, 9),
        "unit_system": str(getattr(settings, "system", "NONE")),
        # The canonical invariant, stated rather than silently assumed.
        "canonical_unit": "1 blender unit == 1 robovision metre",
        "unit_scale_is_canonical": abs(scale - 1.0) < 1e-9,
    }

# Never part of a recipe: these say which delivery this is, not what was asked
# for. Hashing them would make every retry a different computation.
TRANSPORT_KEYS = ("idempotency_key", "attempt", "request_id", "id", "rv", "bridge")


def canonical(value: Any) -> str:
    """The one serialization a hash is taken over.

    Defined before it is hashed, and boring on purpose: sorted keys, no
    whitespace, no ASCII escaping. Anything that changes this changes every
    recipe hash the project has ever recorded, which is why it is a named
    constant with a schema version rather than an inline `json.dumps`.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def recipe_hash(
    *,
    method: str,
    params: dict[str, Any],
    tool_version: str,
    determinism: str,
    seeds: dict[str, Any] | None = None,
    targets: dict[str, Any] | None = None,
    inputs: dict[str, str] | None = None,
    model_version: str | None = None,
) -> str:
    """Hash the semantic inputs of one execution.

    `targets` carries the identities an operation acts on together with the
    revisions it required of them, because "bevel face 7" is a different
    computation at a different mesh revision even though the parameters read the
    same. `inputs` is for content hashes of references an operation consumed, and
    `model_version` for an external generator, so a recipe never claims a
    determinism its backend does not provide.
    """
    body = {
        "schema": RECIPE_SCHEMA,
        "method": method,
        "tool_version": tool_version,
        "determinism": determinism,
        "environment": environment(),
        "params": {key: value for key, value in sorted(params.items())
                   if key not in TRANSPORT_KEYS},
        "seeds": dict(sorted((seeds or {}).items())),
        "targets": dict(sorted((targets or {}).items())),
        "inputs": dict(sorted((inputs or {}).items())),
    }
    if model_version is not None:
        body["model_version"] = model_version
    return "rvrecipe:" + hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()
