"""Proof that a client is the one that opened a transaction.

A dropped TCP connection is not authorization. The owner is handed one secret
when a transaction begins, and it is the only thing that can take ownership back
after a disconnect. The host keeps a salted hash and never the token itself: a
verifier that leaks proves nothing, and no call returns the secret twice.

Deliberately the same shape as the Unity host's `RoboVisionRecoveryToken`, so a
client driving both editors has one thing to store and one thing to send.
"""
from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import hmac
import secrets

TOKEN_BYTES = 32
SALT_BYTES = 16


def _encode(raw: bytes) -> str:
    # URL-safe and unpadded: a token travels in JSON and ends up in client logs
    # and shell history, where '+' and '/' are a nuisance.
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _verify(salt: str, secret: str) -> str:
    return _encode(hashlib.sha256(f"{salt}:{secret}".encode("utf-8")).digest())


@dataclass(slots=True, frozen=True)
class RecoveryToken:
    salt: str
    verifier: str

    @classmethod
    def mint(cls) -> tuple["RecoveryToken", str]:
        """Return the stored verifier and the secret, which is handed out once."""
        secret = _encode(secrets.token_bytes(TOKEN_BYTES))
        salt = _encode(secrets.token_bytes(SALT_BYTES))
        return cls(salt=salt, verifier=_verify(salt, secret)), secret

    @classmethod
    def from_stored(cls, salt: str | None, verifier: str | None) -> "RecoveryToken | None":
        if not salt or not verifier:
            return None
        return cls(salt=salt, verifier=verifier)

    def matches(self, candidate: str | None) -> bool:
        """Constant-time. Never reports which half of the answer was wrong."""
        if not candidate:
            return False
        return hmac.compare_digest(_verify(self.salt, candidate), self.verifier)
