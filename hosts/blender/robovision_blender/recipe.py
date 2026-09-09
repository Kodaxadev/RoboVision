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

# The frame every operation on this host executes in, recorded with every recipe.
#
# Not a shared cross-editor space: this is Blender's native authoring frame, and
# saying so is the point. An operation may not casually change forward, up or
# handedness, and converting to some other convention is a later, explicit
# export operation with a recipe and a proof of its own. Recording the frame is
# what makes "the mesh came out rotated but the task said success" detectable
# rather than a story about someone's export settings.
CANONICAL_FRAME = "rvframe:blender_z_up_right_handed_meters"

RECIPE_SCHEMA = 1

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
        "frame": CANONICAL_FRAME,
        "params": {key: value for key, value in sorted(params.items())
                   if key not in TRANSPORT_KEYS},
        "seeds": dict(sorted((seeds or {}).items())),
        "targets": dict(sorted((targets or {}).items())),
        "inputs": dict(sorted((inputs or {}).items())),
    }
    if model_version is not None:
        body["model_version"] = model_version
    return "rvrecipe:" + hashlib.sha256(canonical(body).encode("utf-8")).hexdigest()
