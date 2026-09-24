"""Wallet sign-in (Sign-In With Solana / Ethereum style) and server-side sessions.

Flow: the client asks for a challenge for its address, the wallet signs the exact
message the server issued, and the server verifies the signature, burns the nonce
and sets an HttpOnly session cookie.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from eth_account import Account
from eth_account.messages import encode_defunct

from .store import _connect

NONCE_TTL = 5 * 60
SESSION_TTL = 7 * 24 * 3600
COOKIE = "xs_session"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_nonces (
    nonce      TEXT PRIMARY KEY,
    address    TEXT NOT NULL,
    chain      TEXT NOT NULL,
    message    TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    account    TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
"""

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_EVM_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class AuthError(Exception):
    pass


def init() -> None:
    with closing(_connect()) as conn, conn:
        conn.executescript(_SCHEMA)


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        i = _B58.find(ch)
        if i < 0:
            raise AuthError("Invalid base58 string")
        n = n * 58 + i
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\x00" * (len(s) - len(s.lstrip("1"))) + raw


def normalize(chain: str, address: str) -> str:
    """Validate an address and return the canonical account id, e.g. 'solana:ABC...'."""
    if chain == "solana":
        if not 32 <= len(address) <= 44 or len(b58decode(address)) != 32:
            raise AuthError("Invalid Solana address")
        return f"solana:{address}"
    if chain == "ethereum":
        if not _EVM_RE.match(address):
            raise AuthError("Invalid Ethereum address")
        return f"ethereum:{address.lower()}"
    raise AuthError("Unsupported chain")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_challenge(chain: str, address: str, domain: str, origin: str) -> dict[str, str]:
    normalize(chain, address)
    nonce = secrets.token_hex(16)
    now = time.time()
    label = "Solana" if chain == "solana" else "Ethereum"
    message = (
        f"{domain} wants you to sign in with your {label} account:\n"
        f"{address}\n\n"
        "Sign in to Xerien Scout. This request will not trigger a blockchain "
        "transaction or cost any gas fees.\n\n"
        f"URI: {origin}\n"
        "Version: 1\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {_iso(now)}\n"
        f"Expiration Time: {_iso(now + NONCE_TTL)}"
    )
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM auth_nonces WHERE expires_at < ?", (now,))
        conn.execute(
            "INSERT INTO auth_nonces VALUES (?,?,?,?,?)",
            (nonce, address, chain, message, now + NONCE_TTL),
        )
    return {"nonce": nonce, "message": message}


def _verify_signature(chain: str, address: str, message: str, signature: str) -> None:
    if chain == "solana":
        import base64

        try:
            sig = base64.b64decode(signature, validate=True)
            Ed25519PublicKey.from_public_bytes(b58decode(address)).verify(sig, message.encode())
        except (InvalidSignature, ValueError) as e:
            raise AuthError("Signature verification failed") from e
        return
    try:
        recovered = Account.recover_message(encode_defunct(text=message), signature=signature)
    except Exception as e:  # noqa: BLE001 - malformed signature bytes
        raise AuthError("Signature verification failed") from e
    if recovered.lower() != address.lower():
        raise AuthError("Signature verification failed")


def verify_and_create_session(nonce: str, signature: str) -> tuple[str, str]:
    """Burn the nonce, verify the signature, return (session_token, account)."""
    with closing(_connect()) as conn, conn:
        row = conn.execute("SELECT * FROM auth_nonces WHERE nonce = ?", (nonce,)).fetchone()
        conn.execute("DELETE FROM auth_nonces WHERE nonce = ?", (nonce,))  # single use
    if not row or row["expires_at"] < time.time():
        raise AuthError("Sign-in request expired. Please try again.")

    _verify_signature(row["chain"], row["address"], row["message"], signature)
    account = normalize(row["chain"], row["address"])

    token = secrets.token_urlsafe(32)
    now = time.time()
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        conn.execute("INSERT INTO sessions VALUES (?,?,?,?)", (_hash(token), account, now, now + SESSION_TTL))
    return token, account


def session_account(token: str | None) -> str | None:
    if not token:
        return None
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT account, expires_at FROM sessions WHERE token_hash = ?", (_hash(token),)
        ).fetchone()
    if not row or row["expires_at"] < time.time():
        return None
    return row["account"]


def end_session(token: str | None) -> None:
    if token:
        with closing(_connect()) as conn, conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash(token),))


def describe(account: str) -> dict[str, Any]:
    chain, address = account.split(":", 1)
    return {"account": account, "chain": chain, "address": address}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
