"""Encrypted local installation keys and environment-only Market credentials."""

from __future__ import annotations

import base64
import os
import secrets
from hashlib import sha256

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def market_encryption_key(value: str | None = None) -> bytes:
    raw = str(value or os.getenv("PORTHOUSE_MARKET_KEY_ENCRYPTION_KEY") or "").strip()
    if not raw:
        raise ValueError(
            "PORTHOUSE_MARKET_KEY_ENCRYPTION_KEY is required for Market installation keys"
        )
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (ValueError, TypeError) as exc:
        raise ValueError(
            "PORTHOUSE_MARKET_KEY_ENCRYPTION_KEY must be URL-safe base64"
        ) from exc
    if len(decoded) != 32:
        raise ValueError("PORTHOUSE_MARKET_KEY_ENCRYPTION_KEY must decode to 32 bytes")
    return decoded


def encrypt_installation_private_key(
    private_key: str, *, master_key: bytes, registry_id: str, user_id: str
) -> str:
    nonce = secrets.token_bytes(12)
    aad = f"porthouse-market-installation-key:v1:{registry_id}:{user_id}".encode()
    ciphertext = AESGCM(master_key).encrypt(nonce, private_key.encode("ascii"), aad)
    return "v1." + ".".join(
        base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
        for value in (nonce, ciphertext)
    )


def decrypt_installation_private_key(
    value: str, *, master_key: bytes, registry_id: str, user_id: str
) -> str:
    try:
        version, raw_nonce, raw_ciphertext = str(value).split(".", 2)
        if version != "v1":
            raise ValueError("unsupported installation key encryption version")
        nonce = base64.urlsafe_b64decode(raw_nonce + "=" * (-len(raw_nonce) % 4))
        ciphertext = base64.urlsafe_b64decode(
            raw_ciphertext + "=" * (-len(raw_ciphertext) % 4)
        )
        aad = f"porthouse-market-installation-key:v1:{registry_id}:{user_id}".encode()
        return AESGCM(master_key).decrypt(nonce, ciphertext, aad).decode("ascii")
    except Exception as exc:
        raise ValueError("unable to decrypt Market installation key") from exc


def installation_key_thumbprint(public_key_bytes: bytes) -> str:
    return f"sha256:{sha256(public_key_bytes).hexdigest()}"


def resolve_market_secret(reference: str) -> str:
    value = str(reference or "").strip()
    if not value:
        return ""
    if not value.startswith("env://"):
        raise ValueError("Market credentials must use env://VARIABLE references")
    variable = value.removeprefix("env://")
    if not variable or not variable.replace("_", "").isalnum():
        raise ValueError("invalid Market credential environment reference")
    secret = str(os.getenv(variable) or "")
    if not secret:
        raise ValueError(f"Market credential environment variable is unavailable: {variable}")
    return secret

