from __future__ import annotations

import uuid

from ..registry import HostError


def begin(params, runtime):
    tx_id = str(params.get("transaction") or ("tx:" + str(uuid.uuid4())))
    label = str(params.get("label") or "agent edit")
    return runtime.transactions.begin(tx_id, label)


def commit(params, runtime):
    tx_id = params.get("transaction")
    if not isinstance(tx_id, str) or not tx_id:
        raise HostError("INVALID_PARAMS", "transaction is required")
    return runtime.transactions.commit(tx_id, force=bool(params.get("force", False)))


def rollback(params, runtime):
    tx_id = params.get("transaction")
    if not isinstance(tx_id, str) or not tx_id:
        raise HostError("INVALID_PARAMS", "transaction is required")
    max_steps = max(1, min(512, int(params.get("max_steps", 128))))
    return runtime.transactions.rollback(
        tx_id,
        max_steps=max_steps,
        force=bool(params.get("force", False)),
    )


def register(registry) -> None:
    registry.add("transaction.begin", begin, stability="alpha")
    registry.add("transaction.commit", commit, stability="alpha")
    registry.add("transaction.rollback", rollback, mutating=True, stability="alpha")
