"""One connection per host, and a credential state machine that survives a lost reply.

The MCP adapter opened a client per operation, and measured through the real
path that meant a transaction was orphaned by the time the next tool call
arrived. The session keeps the *actual* connection alive, which is the only thing
the host counts as ownership — nothing here invents a client id.

The harder half is what happens when an acknowledgement never arrives. A
recovery credential is only worth anything if it exists before the side effect it
protects, and if it can still be associated with what the host actually did. Two
windows made that untrue and are closed here:

- **the begin acknowledgement.** The secret used to be stored under the
  host-issued transaction id, which only arrives in the reply. Lose the reply and
  the secret was gone, leaving an orphan nobody could reclaim. The secret and a
  non-secret correlation handle are now persisted *before* the request is sent,
  and the orphan reports that handle, so the association survives.
- **the adoption acknowledgement.** Adoption rotates, so a lost reply left the
  client presenting the old credential while the host expected the new one, with
  no way to tell which. The host counts credential generations, so after an
  ambiguous adoption the client reads the generation and knows which of the two
  it holds is current.

Neither mechanism authenticates anything. The handle grants nothing and the
generation identifies no verifier; only the secret proves authority.
"""
from __future__ import annotations

import threading
import uuid
from typing import Any

from .client import RoboVisionClient
from .errors import RoboVisionError
from .recovery import new_secret, verifier_for


class _Credential:
    """What this session holds for one transaction, and which generation it is."""

    __slots__ = ("generation", "secret", "next_generation", "next_secret", "ambiguous")

    def __init__(self, generation: int, secret: str):
        self.generation = generation
        self.secret = secret
        # Generated before the adoption that would rotate to it, so a lost
        # adoption reply cannot take the replacement with it.
        self.next_generation: int | None = None
        self.next_secret: str | None = None
        # An adoption was sent and its outcome never came back.
        self.ambiguous = False


class HostSession:
    """A serialized, reconnectable connection to one editor host."""

    def __init__(self, address: str, port: int, timeout: float = 30.0):
        self.address = address
        self.port = port
        self.timeout = timeout
        # The transport is synchronous and one connection is one ordering, so
        # concurrent calls are serialized rather than interleaved onto a socket
        # that cannot tell two conversations apart. Reentrant because the
        # transaction helpers hold it across their own `call()`.
        self._lock = threading.RLock()
        self._client: RoboVisionClient | None = None
        self._generation = 0
        # Credentials for transactions this session opened, by transaction id.
        self._credentials: dict[str, _Credential] = {}
        # Credentials for begins whose acknowledgement has not arrived, by the
        # non-secret handle that will identify them if it never does.
        self._pending: dict[str, str] = {}

    @property
    def generation(self) -> int:
        """How many times this session has had to connect.

        A reconnect is a lifecycle event, not an implementation detail: the host
        will have orphaned any transaction the previous socket owned, and a
        caller that cannot see the reconnect cannot know to adopt.
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
        # host just orphaned is exactly the one this session may still reclaim.
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
                    "orphaned and must be reclaimed rather than assumed",
                    data={
                        "method": method,
                        "host": f"{self.address}:{self.port}",
                        "generation": self._generation,
                        "remedy": "Reconnect and call recoverable_transaction(); the credential "
                                  "for anything this session opened is still held.",
                    },
                    retryable=True,
                ) from exc

    # ------------------------------------------------------------ transactions

    def begin_transaction(self, label: str, **fields: Any) -> dict[str, Any]:
        """Open a transaction whose credential this session holds beforehand.

        The secret and its correlation handle are persisted before the request
        goes out, because the window that matters is the one where the host
        opened the transaction and the reply never came back.
        """
        with self._lock:
            secret = new_secret()
            handle = "rvh:" + uuid.uuid4().hex
            # Durable in this session *before* the side effect it protects.
            self._pending[handle] = secret

            params = dict(fields.pop("params", {}) or {})
            params["label"] = label
            params["recovery_verifier"] = verifier_for(secret)
            params["recovery_handle"] = handle
            response = self.call("transaction.begin", params, **fields)

            transaction = (response.get("result") or {}).get("transaction")
            if transaction:
                self._bind(handle, transaction, generation=0)
            return response

    def _bind(self, handle: str, transaction: str, generation: int) -> None:
        secret = self._pending.pop(handle, None)
        if secret is None:
            return
        self._credentials[transaction] = _Credential(generation, secret)

    def adopt_transaction(self, transaction: str, **fields: Any) -> dict[str, Any]:
        """Reclaim a transaction this session opened, holding the next credential first.

        The replacement is generated and retained before the call that rotates to
        it, and promoted only when the host is known to have rotated. On an
        ambiguous reply both are kept and `reconcile` decides between them from
        the host's generation counter — never by guessing.
        """
        with self._lock:
            credential = self._credentials.get(transaction)
            if credential is None:
                raise RoboVisionError(
                    "NO_RECOVERY_CREDENTIAL",
                    "this session did not open that transaction and holds no credential for it",
                    data={"transaction": transaction},
                )
            if credential.next_secret is None:
                credential.next_secret = new_secret()
                credential.next_generation = credential.generation + 1

            params = dict(fields.pop("params", {}) or {})
            params.update(transaction=transaction, recovery_token=credential.secret,
                          next_recovery_verifier=verifier_for(credential.next_secret))
            try:
                response = self.call("transaction.adopt", params, **fields)
            except RoboVisionError as exc:
                if exc.payload.code == "SESSION_LOST":
                    # The host may or may not have rotated. Both credentials are
                    # kept; which one is current is a question for the host.
                    credential.ambiguous = True
                raise
            self._promote(credential)
            return response

    @staticmethod
    def _promote(credential: _Credential) -> None:
        if credential.next_secret is None:
            return
        credential.secret = credential.next_secret
        credential.generation = credential.next_generation or credential.generation + 1
        credential.next_secret = None
        credential.next_generation = None
        credential.ambiguous = False

    def end_transaction(self, method: str, transaction: str, **fields: Any) -> dict[str, Any]:
        """Commit, roll back or discard, and forget the credential afterwards."""
        with self._lock:
            params = dict(fields.pop("params", {}) or {})
            params["transaction"] = transaction
            response = self.call(method, params, **fields)
            # Only once it actually ended. A commit refused for contamination is
            # still a transaction this session may have to reclaim, and dropping
            # its credential would strand it.
            if response.get("ok"):
                self._credentials.pop(transaction, None)
            return response

    # ---------------------------------------------------------------- health

    def health(self, **fields: Any) -> dict[str, Any]:
        """The host's readiness report, plus the part only this side can know.

        Two blocks, deliberately never merged. `host` is exactly what the editor
        said, untouched, so a caller comparing two editors is comparing their
        answers rather than this library's opinion of them. `session` is what the
        host is structurally incapable of knowing: whether the connection that
        owns a transaction is this one, and whether this session still holds the
        credential that could reclaim it.

        The credential itself never appears here, or anywhere else. Only the fact
        that one is held — which is a fact about this process, not a permission,
        and cannot be used by anyone who reads it.

        Sent on the session's own connection, because ownership *is* the
        connection: a health report fetched over a second socket would describe a
        caller that does not exist by the time anyone acts on it.
        """
        with self._lock:
            response = self.call("system.health", {}, **fields)
            report = response.get("result") or {}
            situation = ((report.get("subsystems") or {}).get("transaction") or {})
            state = situation.get("state") or {}
            transaction = state.get("transaction")
            response["session"] = {
                "connection_generation": self._generation,
                "holds_recovery_credential": bool(
                    transaction is not None and transaction in self._credentials),
                # Only meaningful for a transaction waiting to be adopted; a
                # session that says it could recover an active one it does not own
                # would be claiming an authority adoption exists to withhold.
                "recoverable_by_this_session": bool(
                    transaction is not None
                    and transaction in self._credentials
                    and bool(state.get("adoption_required"))),
                "pending_begins": len(self._pending),
                "note": "session-side facts; the host cannot observe any of them",
            }
            return response

    # -------------------------------------------------------------- recovery

    def recoverable_transaction(self) -> dict[str, Any] | None:
        """What this session can still do about a transaction it opened.

        Reads the host rather than assuming: after a lost connection the host is
        the only thing that knows what became of the work, and reconciles the
        credential state at the same time, because both questions have the same
        answer and asking them separately invites them to disagree.
        """
        with self._lock:
            state = ((self.call("system.hello").get("result") or {})
                     .get("transaction") or {}).get("state")
            if not state:
                return None
            transaction = state.get("transaction")

            # A begin whose acknowledgement never arrived: the orphan carries the
            # handle this session chose, which is how the secret finds its way to
            # the transaction the host actually opened.
            handle = state.get("recovery_handle")
            if transaction not in self._credentials and handle in self._pending:
                self._bind(handle, transaction, generation=int(state.get("recovery_generation", 0)))

            credential = self._credentials.get(transaction)
            if credential is None:
                return None
            self._reconcile(credential, int(state.get("recovery_generation", 0)), transaction)
            return {
                "transaction": transaction,
                "state": state.get("state"),
                "adoption_required": bool(state.get("adoption_required")),
                "recoverable_by_this_session": bool(state.get("adoption_required")),
                "contaminated": bool(state.get("contaminated")),
                "world_incarnation": state.get("world_incarnation"),
                "credential_generation": credential.generation,
                "credential_reconciled": not credential.ambiguous,
            }

    @staticmethod
    def _reconcile(credential: _Credential, host_generation: int, transaction: str) -> None:
        """Decide which held credential is current, from a number rather than a guess.

        After an adoption whose reply was lost the client holds two secrets and
        the host holds one verifier. Exactly two answers are legitimate for one
        ambiguous rotation: the generation is where it was, so the rotation never
        happened and the current secret still is; or it is the pending one, so it
        did and the replacement — already in hand — is now current.

        Matched exactly rather than with an inequality. A host generation ahead
        of anything this session pended means the two credential histories have
        diverged — another session adopted in between, or state was lost — and
        promoting on `>=` would hand over a secret with no reason to believe the
        host has its verifier. There is no safe guess there, so it is reported.
        """
        if host_generation == credential.generation:
            # Whether or not an adoption was in flight, the host is where this
            # session last knew it to be.
            credential.ambiguous = False
            return
        if credential.next_secret is not None and host_generation == credential.next_generation:
            HostSession._promote(credential)
            return
        raise RoboVisionError(
            "CREDENTIAL_STATE_DIVERGED",
            "the host's recovery generation is not one this session can account for; "
            "its credential history and this session's have diverged",
            data={
                "transaction": transaction,
                "host_generation": host_generation,
                "session_generation": credential.generation,
                "session_pending_generation": credential.next_generation,
                "remedy": "This session can no longer prove ownership of that transaction. "
                          "Discarding it is the only outcome it can honestly offer.",
            },
        )

    def terminal_state(self, transaction: str) -> dict[str, Any]:
        """What became of a transaction, without repeating its side effect.

        A commit whose acknowledgement was lost must be resolvable by reading.
        Re-sending it to find out would be the exact mistake idempotency exists
        to prevent, one level up.
        """
        with self._lock:
            response = self.call("transaction.status", {"transaction": transaction})
            result = response.get("result") or {}
            finished = result.get("finished")
            if finished:
                self._credentials.pop(transaction, None)
            return {
                "transaction": transaction,
                "finished": finished,
                "active": result.get("active"),
                "outcome": (finished or {}).get("state"),
            }

    def holds_credential_for(self, transaction: str) -> bool:
        """Whether this session can still reclaim that transaction. Never the secret."""
        with self._lock:
            return transaction in self._credentials

    def pending_begins(self) -> int:
        """How many begins are awaiting an acknowledgement that may never come."""
        with self._lock:
            return len(self._pending)

    def reconnect(self) -> int:
        """Deliberately establish a new connection, and say which one it is."""
        with self._lock:
            self.close()
            self._connect()
            return self._generation
