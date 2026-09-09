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
        recovery_verifier=params.get("recovery_verifier"),
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
        next_verifier=params.get("next_recovery_verifier"),
    )


def discard(params, runtime):
    return runtime.transactions.discard(
        params.get("transaction"), client_id=runtime.current_client_id
    )


def register(registry) -> None:
    # All of these are side-effecting although only rollback moves the scene:
    # "does not advance the revision" is not the same claim as "safe to execute
    # twice", and a duplicated begin, commit, adopt or discard is not free.
    #
    # They are deliberately not covered by idempotent replay yet. Each is already
    # duplicate-safe through its own state machine — a second begin is
    # TRANSACTION_ACTIVE, a second commit or rollback is TRANSACTION_FINISHED, a
    # second adopt is refused because the transaction is no longer orphaned — and
    # begin's response carries the recovery token, which must not be stored in a
    # ledger or handed back by a replay to whoever redelivers the request.
    # Replaying these needs deliberate redaction, and that is designed when
    # something actually needs it rather than now.
    registry.add("transaction.begin", begin, stability="alpha", side_effecting=True,
                 duplicate_policy="terminal_state")
    registry.add("transaction.commit", commit, stability="alpha", side_effecting=True,
                 duplicate_policy="terminal_state")
    registry.add("transaction.rollback", rollback, mutating=True, stability="alpha",
                 duplicate_policy="terminal_state")
    # Adoption must reach the host without an owner, and proves authority with a
    # token rather than with the connection it arrives on.
    registry.add("transaction.adopt", adopt, stability="alpha", side_effecting=True,
                 duplicate_policy="terminal_state")
    registry.add("transaction.discard", discard, stability="alpha", side_effecting=True,
                 duplicate_policy="terminal_state")
