"""One connection per host, kept open across calls.

The MCP adapter opened a client per operation, and measured through the real
path that meant a transaction was orphaned by the time the next tool call
arrived: `transaction.begin` succeeded, `system.hello` already reported
`owner_disconnected`, and both the mutation and the rollback were refused
`TRANSACTION_ORPHANED`. The host's ownership guarantee is correct; the adapter
simply could not hold a transaction long enough to use one.

Connection ownership stays host truth. Nothing here invents a client id or
smuggles one through parameters — the session keeps the *actual* connection
alive, which is the only thing the host counts.
"""
from __future__ import annotations

import threading
from typing import Any

from .client import RoboVisionClient
from .errors import RoboVisionError
from .recovery import new_secret, verifier_for


class HostSession:
    """A serialized, reconnectable connection to one editor host."""

    def __init__(self, address: str, port: int, timeout: float = 30.0):
        self.address = address
        self.port = port
        self.timeout = timeout
        # The transport is synchronous and one connection is one ordering, so
        # concurrent MCP calls are serialized rather than interleaved onto a
        # socket that cannot tell two conversations apart.
        self._lock = threading.RLock()
        self._client: RoboVisionClient | None = None
        self._generation = 0
        # Recovery secrets for transactions this session opened, by id. Private
        # on purpose: nothing reads them out, and nothing writes them anywhere a
        # diagnostic, a log, a journal or the operation ledger can reach.
        self._secrets: dict[str, str] = {}
        # The next secret for a transaction, generated *before* the adoption that
        # will rotate to it, so a lost adoption reply cannot take the replacement
        # with it.
        self._pending: dict[str, str] = {}

    @property
    def generation(self) -> int:
        """How many times this session has had to reconnect.

        Exposed because a reconnect is a lifecycle event, not an implementation
        detail: the host will have orphaned any transaction the previous socket
        owned, and a caller that cannot see the reconnect cannot know to adopt.
        """
        return self._generation

    @property
    def connected(self) -> bool:
        return self._client is not None

    def _connect(self) -> RoboVisionClient:
        if self._client is None:
            self._client = RoboVisionClient(self.address, self.port, self.timeout).connect()
            self._generation += 1
        return self._client

    def close(self) -> None:
        # Credentials deliberately survive a closed socket: the transaction the
        # host orphaned is exactly the one this session may still adopt.
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                finally:
                    self._client = None

    def call(self, method: str, params: dict[str, Any] | None = None, **fields: Any) -> dict[str, Any]:
        """Send one request on the session's connection.

        A dead socket is surfaced, never papered over. Reconnecting silently and
        retrying would be the worst of both worlds: the host has already orphaned
        whatever transaction the old connection owned, so the retry would arrive
        as a stranger holding no authority over the work in progress, and the
        caller would never learn that ownership had to be reclaimed.
        """
        with self._lock:
            client = self._connect()
            try:
                return client.call(method, params or {}, **fields)
            except (OSError, RoboVisionError) as exc:
                if isinstance(exc, RoboVisionError) and exc.payload.code != "CONNECTION_CLOSED":
                    raise
                self.close()
                raise RoboVisionError(
                    "SESSION_LOST",
                    "the connection to the editor host was lost; a transaction it owned is now "
                    "orphaned and must be adopted rather than assumed",
                    data={
                        "method": method,
                        "host": f"{self.address}:{self.port}",
                        "generation": self._generation,
                        "remedy": "Reconnect, read system.hello, and transaction.adopt with the "
                                  "recovery secret if a transaction is waiting.",
                    },
                    retryable=True,
                ) from exc

    # ------------------------------------------------------ transactions

    def begin_transaction(self, label: str, **fields: Any) -> dict[str, Any]:
        """Open a transaction whose recovery credential this session already holds.

        The secret is generated here and never sent: only its verifier goes to
        the host. An agent driving this does not have to know that a credential
        exists, which is the point — a caller that has to remember to generate
        and retain a CSPRNG secret will eventually not.
        """
        secret = new_secret()
        params = dict(fields.pop("params", {}) or {})
        params["label"] = label
        params["recovery_verifier"] = verifier_for(secret)
        response = self.call("transaction.begin", params, **fields)
        transaction = (response.get("result") or {}).get("transaction")
        if transaction:
            self._secrets[transaction] = secret
        return response

    def adopt_transaction(self, transaction: str, **fields: Any) -> dict[str, Any]:
        """Reclaim a transaction this session opened, and pre-hold the next secret.

        The replacement is generated and retained before the call, so a lost
        adoption reply costs nothing: the old secret is dead either way, and the
        new one is already here rather than only inside the reply that vanished.
        """
        secret = self._secrets.get(transaction)
        if secret is None:
            raise RoboVisionError(
                "NO_RECOVERY_CREDENTIAL",
                "this session did not open that transaction and holds no credential for it",
                data={"transaction": transaction},
            )
        following = self._pending.setdefault(transaction, new_secret())
        params = dict(fields.pop("params", {}) or {})
        params.update(transaction=transaction, recovery_token=secret,
                      next_recovery_verifier=verifier_for(following))
        response = self.call("transaction.adopt", params, **fields)
        # Only on success, and only from what was already held.
        self._secrets[transaction] = self._pending.pop(transaction)
        return response

    def end_transaction(self, method: str, transaction: str, **fields: Any) -> dict[str, Any]:
        """Commit, roll back or discard, and forget the credential afterwards."""
        params = dict(fields.pop("params", {}) or {})
        params["transaction"] = transaction
        response = self.call(method, params, **fields)
        # Only once it actually ended. A refused commit — contaminated, or
        # needing force — is still a transaction this session may have to
        # reclaim, and dropping its credential would strand it.
        if response.get("ok"):
            self._secrets.pop(transaction, None)
            self._pending.pop(transaction, None)
        return response

    def recoverable_transaction(self) -> dict[str, Any] | None:
        """Whether a transaction this session opened is waiting to be adopted.

        Reads `system.hello` rather than assuming: after a lost connection the
        host is the only thing that knows what became of the work, and a session
        that guessed would be inventing the answer it exists to look up.
        """
        state = ((self.call("system.hello").get("result") or {})
                 .get("transaction") or {}).get("state")
        if not state:
            return None
        transaction = state.get("transaction")
        if transaction not in self._secrets:
            return None
        return {
            "transaction": transaction,
            "state": state.get("state"),
            "adoption_required": bool(state.get("adoption_required")),
            "recoverable_by_this_session": bool(state.get("adoption_required")),
            "contaminated": bool(state.get("contaminated")),
            "world_incarnation": state.get("world_incarnation"),
        }

    def holds_credential_for(self, transaction: str) -> bool:
        """Whether this session can still reclaim that transaction. Never the secret."""
        return transaction in self._secrets

    def reconnect(self) -> int:
        """Deliberately establish a new connection, and say which one it is."""
        with self._lock:
            self.close()
            self._connect()
            return self._generation
