"""Password, session, and RFC 6238 TOTP primitives for platform admins.

Passwords are one-way hashed with scrypt.  TOTP secrets must remain
recoverable, so they are encrypted with an application key before entering
PostgreSQL.  Session, challenge, and recovery tokens are stored as SHA-256
digests only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_SCRYPT_N = 1 << 14
_SCRYPT_R = 8
_SCRYPT_P = 5
_SCRYPT_LENGTH = 32
_TOTP_STEP_SECONDS = 30
_TOTP_DIGITS = 6
_RECOVERY_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
DEFAULT_DEVELOPMENT_ADMIN_USER = "porthouse"
DEFAULT_DEVELOPMENT_ADMIN_PASSWORD = "porthouse"


def _b64_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def validate_password(password: str) -> str:
    value = str(password)
    if len(value) < 12:
        raise ValueError("password must contain at least 12 characters")
    if len(value) > 1024:
        raise ValueError("password must contain at most 1024 characters")
    return value


def _hash_password_value(value: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        value.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_LENGTH,
        maxmem=128 * 1024 * 1024,
    )
    return (
        f"scrypt$n={_SCRYPT_N},r={_SCRYPT_R},p={_SCRYPT_P}"
        f"${_b64_encode(salt)}${_b64_encode(digest)}"
    )


def hash_password(password: str) -> str:
    """Validate and hash a regular administrator password."""
    return _hash_password_value(validate_password(password))


def hash_development_default_password() -> str:
    """Hash the single documented insecure-local bootstrap password."""
    return _hash_password_value(DEFAULT_DEVELOPMENT_ADMIN_PASSWORD)


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, params, salt_value, digest_value = str(encoded).split("$", 3)
        if algorithm != "scrypt":
            return False
        parsed = dict(item.split("=", 1) for item in params.split(","))
        n, r, p = int(parsed["n"]), int(parsed["r"]), int(parsed["p"])
        if n < 2 or n > 1 << 20 or r < 1 or r > 64 or p < 1 or p > 64:
            return False
        expected = _b64_decode(digest_value)
        actual = hashlib.scrypt(
            str(password).encode("utf-8"),
            salt=_b64_decode(salt_value),
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
            maxmem=128 * 1024 * 1024,
        )
        return hmac.compare_digest(actual, expected)
    except (KeyError, TypeError, ValueError):
        return False


def token_digest(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def new_bearer_token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def auth_encryption_key(*, production: bool, development_password: str = "") -> bytes:
    """Return the AES-256 key used for TOTP secrets.

    Production must inject a random 32-byte URL-safe base64 value through
    ``PORTHOUSE_AUTH_ENCRYPTION_KEY``.  Development derives a stable local
    key from the explicitly local bootstrap password so setup survives a
    restart without pretending that the development default is production
    secret material.
    """
    raw = str(os.getenv("PORTHOUSE_AUTH_ENCRYPTION_KEY") or "").strip()
    if raw:
        try:
            key = _b64_decode(raw)
        except ValueError as exc:
            raise ValueError("PORTHOUSE_AUTH_ENCRYPTION_KEY must be URL-safe base64") from exc
        if len(key) != 32:
            raise ValueError("PORTHOUSE_AUTH_ENCRYPTION_KEY must decode to 32 bytes")
        return key
    if production:
        raise ValueError("PORTHOUSE_AUTH_ENCRYPTION_KEY is required for TOTP in production")
    seed = str(development_password or DEFAULT_DEVELOPMENT_ADMIN_PASSWORD)
    return hashlib.sha256(f"porthouse-development-totp:{seed}".encode()).digest()


def encrypt_totp_secret(secret: str, key: bytes) -> str:
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, str(secret).encode("ascii"), b"porthouse-totp-v1")
    return f"v1.{_b64_encode(nonce)}.{_b64_encode(ciphertext)}"


def decrypt_totp_secret(value: str, key: bytes) -> str:
    try:
        version, nonce, ciphertext = str(value).split(".", 2)
        if version != "v1":
            raise ValueError("unsupported TOTP secret encryption version")
        plaintext = AESGCM(key).decrypt(
            _b64_decode(nonce),
            _b64_decode(ciphertext),
            b"porthouse-totp-v1",
        )
        return plaintext.decode("ascii")
    except Exception as exc:
        raise ValueError("unable to decrypt TOTP secret") from exc


def generate_totp_secret() -> str:
    """Generate the RFC 4226 recommended 160-bit shared secret."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def totp_uri(*, secret: str, user_id: str, issuer: str = "Porthousebot") -> str:
    label = quote(f"{issuer}:{user_id}", safe="")
    return (
        f"otpauth://totp/{label}?secret={quote(secret, safe='')}"
        f"&issuer={quote(issuer, safe='')}&algorithm=SHA1&digits={_TOTP_DIGITS}"
        f"&period={_TOTP_STEP_SECONDS}"
    )


def _hotp(secret: str, counter: int) -> str:
    padded = secret + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded, casefold=True)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10**_TOTP_DIGITS)).zfill(_TOTP_DIGITS)


def matching_totp_counter(
    secret: str,
    code: str,
    *,
    now: float | None = None,
    window: int = 1,
) -> int | None:
    normalized = "".join(character for character in str(code) if character.isdigit())
    if len(normalized) != _TOTP_DIGITS:
        return None
    counter = int((time.time() if now is None else now) // _TOTP_STEP_SECONDS)
    for delta in range(-max(0, window), max(0, window) + 1):
        candidate = counter + delta
        if candidate >= 0 and hmac.compare_digest(_hotp(secret, candidate), normalized):
            return candidate
    return None


def generate_recovery_codes(count: int = 8) -> list[str]:
    codes: list[str] = []
    for _ in range(max(1, count)):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(12))
        codes.append(f"{raw[:4]}-{raw[4:8]}-{raw[8:]}")
    return codes


def normalize_recovery_code(code: str) -> str:
    return "".join(character for character in str(code).upper() if character.isalnum())


def recovery_code_digest(code: str) -> str:
    return token_digest(normalize_recovery_code(code))
