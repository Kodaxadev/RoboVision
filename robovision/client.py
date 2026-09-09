from __future__ import annotations

import json
import socket
from typing import Any

from .errors import RoboVisionError
from .protocol import Request


class RoboVisionClient:
    """Minimal synchronous client for the local newline-delimited RoboVision transport."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9877, timeout: float = 30.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._buffer = bytearray()

    def connect(self) -> "RoboVisionClient":
        if self._socket is None:
            self._socket = socket.create_connection((self.host, self.port), timeout=self.timeout)
            self._socket.settimeout(self.timeout)
        return self

    def close(self) -> None:
        if self._socket is not None:
            try:
                self._socket.close()
            finally:
                self._socket = None
                self._buffer.clear()

    def __enter__(self) -> "RoboVisionClient":
        return self.connect()

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        if_revision: int | None = None,
        idempotency_key: str | None = None,
        attempt: int | None = None,
        expected_world: str | None = None,
        expected_coordinate_contract: str | None = None,
        contract: str | None = None,
    ) -> dict[str, Any]:
        """Send one request and return the whole response envelope.

        Every guarantee the host offers is reachable from here. It was not: these
        fields existed on the wire and not in this client, so an agent wanting
        safe retries had to build its own dicts and bypass the library shipped
        for exactly that purpose.
        """
        self.connect()
        assert self._socket is not None
        request = Request(
            method=method, params=params or {}, if_revision=if_revision,
            idempotency_key=idempotency_key, attempt=attempt,
            expected_world=expected_world,
            expected_coordinate_contract=expected_coordinate_contract,
            contract=contract,
        )
        self._socket.sendall((json.dumps(request.to_dict(), separators=(",", ":")) + "\n").encode("utf-8"))
        while b"\n" not in self._buffer:
            chunk = self._socket.recv(65536)
            if not chunk:
                raise RoboVisionError("CONNECTION_CLOSED", "host closed the connection", retryable=True)
            self._buffer.extend(chunk)
        raw, _, rest = self._buffer.partition(b"\n")
        self._buffer[:] = rest
        response = json.loads(raw.decode("utf-8"))
        if response.get("id") != request.id:
            raise RoboVisionError("RESPONSE_MISMATCH", "response id did not match request")
        if not response.get("ok"):
            err = response.get("error") or {}
            raise RoboVisionError(
                err.get("code", "HOST_ERROR"),
                err.get("message", "host error"),
                data=err.get("data"),
                retryable=bool(err.get("retryable")),
            )
        return response
