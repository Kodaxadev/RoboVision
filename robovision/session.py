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

    def reconnect(self) -> int:
        """Deliberately establish a new connection, and say which one it is."""
        with self._lock:
            self.close()
            self._connect()
            return self._generation
