"""Minimal DSSE Ed25519 profile used by Porthouse App releases."""

from __future__ import annotations

import base64
import binascii
import hmac
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from porthouse.market_protocol.canonical import parse_strict_json


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _unb64(value: str) -> bytes:
    try:
        return base64.b64decode(str(value), validate=True)
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ValueError("invalid DSSE base64 value") from exc


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64url(value: str) -> bytes:
    try:
        raw = str(value)
        return base64.b64decode(
            raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True
        )
    except (ValueError, TypeError, binascii.Error) as exc:
        raise ValueError("invalid base64url value") from exc


def _pae(payload_type: str, payload: bytes) -> bytes:
    type_bytes = payload_type.encode("utf-8")
    return b"DSSEv1 %d %s %d %s" % (
        len(type_bytes),
        type_bytes,
        len(payload),
        payload,
    )


def ed25519_key_id(public_key: bytes) -> str:
    if len(public_key) != 32:
        raise ValueError("Ed25519 public key must contain 32 bytes")
    return f"ed25519:sha256:{sha256(public_key).hexdigest()}"


@dataclass(frozen=True, slots=True)
class Ed25519KeyPair:
    key_id: str
    public_key: str
    private_key: str

    def public_record(self) -> dict[str, str]:
        return {
            "key_id": self.key_id,
            "algorithm": "ed25519",
            "public_key": f"base64url:{self.public_key}",
        }


def generate_ed25519_key_pair() -> Ed25519KeyPair:
    private = Ed25519PrivateKey.generate()
    private_bytes = private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public_bytes = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return Ed25519KeyPair(
        key_id=ed25519_key_id(public_bytes),
        public_key=_b64url(public_bytes),
        private_key=_b64url(private_bytes),
    )


def public_key_bytes(value: str) -> bytes:
    raw = str(value).removeprefix("base64url:")
    decoded = _unb64url(raw)
    if len(decoded) != 32:
        raise ValueError("Ed25519 public key must contain 32 bytes")
    return decoded


def private_key_from_text(value: str) -> Ed25519PrivateKey:
    raw = str(value).strip().removeprefix("base64url:")
    decoded = _unb64url(raw)
    if len(decoded) != 32:
        raise ValueError("Ed25519 private key must contain 32 bytes")
    return Ed25519PrivateKey.from_private_bytes(decoded)


def sign_dsse(
    payload: bytes,
    *,
    payload_type: str,
    private_key: str | Ed25519PrivateKey,
    key_id: str | None = None,
) -> dict[str, Any]:
    if not payload_type or len(payload_type) > 255:
        raise ValueError("DSSE payload_type is required and must be <= 255 characters")
    signer = (
        private_key
        if isinstance(private_key, Ed25519PrivateKey)
        else private_key_from_text(private_key)
    )
    public_bytes = signer.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    resolved_key_id = key_id or ed25519_key_id(public_bytes)
    if not hmac.compare_digest(resolved_key_id, ed25519_key_id(public_bytes)):
        raise ValueError("DSSE key_id does not match the signing key")
    signature = signer.sign(_pae(payload_type, payload))
    return {
        "payloadType": payload_type,
        "payload": _b64(payload),
        "signatures": [{"keyid": resolved_key_id, "sig": _b64(signature)}],
    }


def verify_dsse(
    envelope: Mapping[str, Any] | bytes | str,
    *,
    public_keys: Mapping[str, str | bytes],
    expected_payload_type: str | None = None,
) -> tuple[bytes, str]:
    value = parse_strict_json(envelope) if isinstance(envelope, (bytes, str)) else dict(envelope)
    payload_type = str(value.get("payloadType") or "")
    if expected_payload_type and not hmac.compare_digest(payload_type, expected_payload_type):
        raise ValueError("unexpected DSSE payload type")
    payload = _unb64(str(value.get("payload") or ""))
    signatures = value.get("signatures")
    if not isinstance(signatures, list) or not signatures:
        raise ValueError("DSSE envelope must contain at least one signature")
    seen: set[str] = set()
    for item in signatures:
        if not isinstance(item, dict):
            continue
        key_id = str(item.get("keyid") or "")
        if not key_id or key_id in seen:
            continue
        seen.add(key_id)
        raw_public = public_keys.get(key_id)
        if raw_public is None:
            continue
        public_bytes = raw_public if isinstance(raw_public, bytes) else public_key_bytes(raw_public)
        if not hmac.compare_digest(ed25519_key_id(public_bytes), key_id):
            continue
        try:
            Ed25519PublicKey.from_public_bytes(public_bytes).verify(
                _unb64(str(item.get("sig") or "")),
                _pae(payload_type, payload),
            )
        except (InvalidSignature, ValueError):
            continue
        return payload, key_id
    raise ValueError("DSSE envelope has no valid signature from a trusted key")
