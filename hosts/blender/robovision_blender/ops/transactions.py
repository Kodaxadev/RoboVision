"""Transaction control: begin, adopt, finish.

Ownership is the connection the request arrived on, which is why every handler
here takes the runtime's current client id rather than trusting anything in the
parameters. A client-supplied string is a correlation label; it is never the
host's identity for a transaction.
"""
from __future__ import annotations

from ..registry import HostError


def begin(params, runtime):
    label = str(params.get("label") or "agent edit")
    # A caller-chosen `transaction` used to become the id. It is a label now:
    # the host mints the identity, scoped to the world, so nothing a client
    # names can collide with, impersonate or outlive a real transaction.
    if params.get("transaction") is not None:
        label = str(params.get("transaction"))
    return runtime.transactions.begin(
        label,
        world=runtime.world_incarnation,
        revision=runtime.revision,
        owner_client=runtime.current_client_id,
        before=runtime.current_snapshot(),
    )


def commit(params, runtime):
    tx_id = params.get("transaction")
    if not isinstance(tx_id, str) or not tx_id:
        raise HostError("INVALID_PARAMS", "transaction is required")
    return runtime.transactions.commit(
        tx_id, client_id=runtime.current_client_id, force=bool(params.get("force", False))
    )


def rollback(params, runtime):
    tx_id = params.get("transaction")
    if not isinstance(tx_id, str) or not tx_id:
        raise HostError("INVALID_PARAMS", "transaction is required")
    return runtime.transactions.rollback(
        tx_id,
        client_id=runtime.current_client_id,
        max_steps=int(params.get("max_undo_steps", 128)),
        force=bool(params.get("force", False)),
    )


def adopt(params, runtime):
    return runtime.transactions.adopt(
        params.get("transaction"),
        params.get("recovery_token"),
        client_id=runtime.current_client_id,
    )


def discard(params, runtime):
    return runtime.transactions.discard(
        params.get("transaction"), client_id=runtime.current_client_id
    )


def register(registry) -> None:
    registry.add("transaction.begin", begin, stability="alpha")
    registry.add("transaction.commit", commit, stability="alpha")
    registry.add("transaction.rollback", rollback, mutating=True, stability="alpha")
    # Adoption must reach the host without an owner, and proves authority with a
    # token rather than with the connection it arrives on.
    registry.add("transaction.adopt", adopt, stability="alpha")
    registry.add("transaction.discard", discard, stability="alpha")
