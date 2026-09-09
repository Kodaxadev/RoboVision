from __future__ import annotations

from dataclasses import dataclass, field
import json
import socket
from typing import Any, Callable

MAX_MESSAGE_BYTES = 4 * 1024 * 1024


@dataclass(slots=True)
class _Client:
    sock: socket.socket
    # Which connection this is. A transaction belongs to the connection that
    # opened it, and a file descriptor number is reused by the OS as soon as it
    # is closed — so the id a transaction remembers must not be one.
    id: int
    recv_buffer: bytearray = field(default_factory=bytearray)
    send_buffer: bytearray = field(default_factory=bytearray)


class NonBlockingJsonServer:
    """Loopback JSON transport polled from Blender's main-thread timer.

    No Python worker thread is created. `poll()` accepts sockets, reads requests,
    invokes dispatch, and writes responses without blocking.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9877,
                 on_client_closed: Callable[[int], None] | None = None) -> None:
        self.host = host
        self.port = port
        self.listener: socket.socket | None = None
        self.clients: dict[int, _Client] = {}
        self.on_client_closed = on_client_closed
        self._next_client_id = 0

    @property
    def running(self) -> bool:
        return self.listener is not None

    def start(self) -> None:
        if self.listener is not None:
            return
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(16)
        listener.setblocking(False)
        self.listener = listener

    def stop(self) -> None:
        for client in list(self.clients.values()):
            self._close(client)
        if self.listener is not None:
            try:
                self.listener.close()
            finally:
                self.listener = None

    def poll(self, dispatch: Callable[[dict[str, Any], int], dict[str, Any]], *,
             command_budget: int = 8) -> None:
        if self.listener is None:
            return
        self._accept_pending()
        remaining = command_budget
        for client in list(self.clients.values()):
            if remaining <= 0:
                break
            remaining -= self._read_client(client, dispatch, remaining)
            self._flush_client(client)

    def _accept_pending(self) -> None:
        assert self.listener is not None
        for _ in range(8):
            try:
                sock, _address = self.listener.accept()
            except BlockingIOError:
                return
            sock.setblocking(False)
            self._next_client_id += 1
            self.clients[sock.fileno()] = _Client(sock, self._next_client_id)

    def _read_client(self, client: _Client,
                     dispatch: Callable[[dict[str, Any], int], dict[str, Any]], budget: int) -> int:
        try:
            while True:
                chunk = client.sock.recv(65536)
                if not chunk:
                    self._close(client)
                    return 0
                client.recv_buffer.extend(chunk)
                if len(client.recv_buffer) > MAX_MESSAGE_BYTES and b"\n" not in client.recv_buffer:
                    self._queue(client, self._transport_error("MESSAGE_TOO_LARGE", "request exceeds transport limit"))
                    self._close_after_flush(client)
                    return 0
        except BlockingIOError:
            pass
        except OSError:
            self._close(client)
            return 0

        processed = 0
        while processed < budget and b"\n" in client.recv_buffer:
            raw, _, rest = client.recv_buffer.partition(b"\n")
            client.recv_buffer[:] = rest
            if not raw.strip():
                continue
            if len(raw) > MAX_MESSAGE_BYTES:
                self._queue(client, self._transport_error("MESSAGE_TOO_LARGE", "request exceeds transport limit"))
                processed += 1
                continue
            try:
                request = json.loads(raw.decode("utf-8"))
                if not isinstance(request, dict):
                    raise ValueError("request must be a JSON object")
                response = dispatch(request, client.id)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                response = self._transport_error("INVALID_REQUEST", str(exc))
            self._queue(client, response)
            processed += 1
        return processed

    def _queue(self, client: _Client, response: dict[str, Any]) -> None:
        payload = (json.dumps(response, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        client.send_buffer.extend(payload)

    def _flush_client(self, client: _Client) -> None:
        if not client.send_buffer:
            return
        try:
            sent = client.sock.send(client.send_buffer)
            del client.send_buffer[:sent]
        except BlockingIOError:
            return
        except OSError:
            self._close(client)

    def _close_after_flush(self, client: _Client) -> None:
        """Deliver a refusal, then half-close so the peer can still read it.

        Closing outright while the peer is mid-send resets the connection and
        the client sees a transport reset instead of the reason it was refused.
        Shutting down the write side first gives the error its best chance of
        arriving; a peer that keeps writing can still force a reset, which is a
        property of TCP rather than something the host can prevent.
        """
        self._flush_client(client)
        try:
            client.sock.shutdown(socket.SHUT_WR)
        except OSError:
            pass
        if not client.send_buffer:
            self._close(client)

    def _close(self, client: _Client) -> None:
        fileno = client.sock.fileno()
        try:
            client.sock.close()
        finally:
            self.clients.pop(fileno, None)
            # A transaction whose owner has gone must find out, or it stays
            # active forever with nobody able to prove they may finish it.
            if self.on_client_closed is not None:
                try:
                    self.on_client_closed(client.id)
                except Exception:
                    pass

    @staticmethod
    def _transport_error(code: str, message: str) -> dict[str, Any]:
        return {
            "rv": "1.0",
            "id": "invalid",
            "ok": False,
            "revision": 0,
            "error": {"code": code, "message": message, "retryable": False},
        }
