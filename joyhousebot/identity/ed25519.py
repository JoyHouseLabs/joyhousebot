"""Ed25519 bot identity: key generation and signing (no EVM/address, for auth only)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from joyhousebot.utils.helpers import ensure_dir

# Ed25519: private 32 bytes, public 32 bytes, signature 64 bytes
PRIVATE_KEY_HEX_LEN = 64
PUBLIC_KEY_HEX_LEN = 64
SIGNATURE_HEX_LEN = 128


@dataclass
class BotIdentity:
    """Bot identity key pair (Ed25519). No address; public_key is for auth only."""

    private_key_hex: str
    public_key_hex: str


def _normalize_hex(value: str, expected_len: int | None = None) -> str:
    val = value.strip().lower()
    if val.startswith("0x"):
        val = val[2:]
    if expected_len is not None and len(val) != expected_len:
        raise ValueError(f"invalid hex length, expected {expected_len}")
    int(val, 16)
    return val


def _generate_private_key_bytes() -> bytes:
    return ed25519.Ed25519PrivateKey.generate().private_bytes(
        encoding=Encoding.Raw,
        format=PrivateFormat.Raw,
        encryption_algorithm=NoEncryption(),
    )


def load_or_create_private_key(path: Path) -> bytes:
    """Load Ed25519 private key from disk or create a new one (32 raw bytes)."""
    if path.exists():
        raw = path.read_text(encoding="utf-8").strip()
        if raw.startswith("0x"):
            raw = raw[2:]
        if len(raw) != PRIVATE_KEY_HEX_LEN:
            raise ValueError(f"invalid Ed25519 private key length, expected {PRIVATE_KEY_HEX_LEN} hex chars")
        return bytes.fromhex(raw)

    key_bytes = _generate_private_key_bytes()
    ensure_dir(path.parent)
    path.write_text(key_bytes.hex(), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key_bytes


def identity_from_private_key_bytes(key_bytes: bytes) -> BotIdentity:
    if len(key_bytes) != 32:
        raise ValueError("Ed25519 private key must be 32 bytes")
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(key_bytes)
    public_key = private_key.public_key()
    pub_bytes = public_key.public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)
    return BotIdentity(
        private_key_hex=key_bytes.hex(),
        public_key_hex=pub_bytes.hex(),
    )


def ensure_bot_identity(key_path: Path) -> BotIdentity:
    """Load or create Ed25519 bot identity at key_path."""
    key_bytes = load_or_create_private_key(key_path)
    return identity_from_private_key_bytes(key_bytes)


def sign_challenge(private_key_hex: str, challenge: str) -> str:
    """Sign challenge (utf-8 bytes) with Ed25519; return signature as 128-char hex."""
    key_hex = _normalize_hex(private_key_hex, expected_len=PRIVATE_KEY_HEX_LEN)
    key_bytes = bytes.fromhex(key_hex)
    private_key = ed25519.Ed25519PrivateKey.from_private_bytes(key_bytes)
    message = challenge.encode("utf-8")
    signature = private_key.sign(message)
    return signature.hex()
