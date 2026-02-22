"""Device identity: Ed25519 keypair, device id derivation, signature verification.

Aligned with OpenClaw device-identity: deviceId = SHA256(publicKey raw),
sign payload with private key, verify with public key (PEM or base64url raw).
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
    load_pem_public_key,
)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = 4 - (len(text) % 4)
    if pad != 4:
        text += "=" * pad
    return base64.urlsafe_b64decode(text)


def _raw_from_pem(public_key_pem: str) -> bytes:
    key = load_pem_public_key(public_key_pem.encode("utf-8"))
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("not Ed25519 public key")
    return key.public_bytes(encoding=Encoding.Raw, format=PublicFormat.Raw)


def fingerprint_public_key(public_key_pem: str) -> str:
    raw = _raw_from_pem(public_key_pem)
    return hashlib.sha256(raw).hexdigest()


def derive_device_id_from_public_key(public_key: str) -> str | None:
    """Derive deviceId from public key (PEM or base64url raw)."""
    try:
        if "BEGIN" in public_key:
            raw = _raw_from_pem(public_key)
        else:
            raw = _b64url_decode(public_key)
        return hashlib.sha256(raw).hexdigest()
    except Exception:
        return None


def normalize_device_public_key_base64url(public_key: str) -> str | None:
    """Normalize public key to base64url raw form."""
    try:
        if "BEGIN" in public_key:
            raw = _raw_from_pem(public_key)
        else:
            raw = _b64url_decode(public_key)
        return _b64url_encode(raw)
    except Exception:
        return None


def verify_device_signature(
    public_key: str,
    payload: str,
    signature_base64url: str,
) -> bool:
    """Verify Ed25519 signature over payload. public_key: PEM or base64url raw."""
    try:
        if "BEGIN" in public_key:
            key = load_pem_public_key(public_key.encode("utf-8"))
        else:
            raw = _b64url_decode(public_key)
            key = Ed25519PublicKey.from_public_bytes(raw)
        if not isinstance(key, Ed25519PublicKey):
            return False
        sig = _b64url_decode(signature_base64url)
        key.verify(sig, payload.encode("utf-8"))
        return True
    except Exception:
        return False


def sign_device_payload(private_key_pem: str, payload: str) -> str:
    """Sign payload with Ed25519 private key; return signature as base64url."""
    key = load_pem_private_key(private_key_pem.encode("utf-8"), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("not Ed25519 private key")
    sig = key.sign(payload.encode("utf-8"))
    return _b64url_encode(sig)
