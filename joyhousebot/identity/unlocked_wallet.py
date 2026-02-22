"""In-memory unlocked default wallet (private key) for signing etc."""

from __future__ import annotations

_unlocked_private_key: str | None = None


def set_unlocked_private_key(private_key_hex: str | None) -> None:
    """Set or clear the in-memory decrypted private key (e.g. after unlock at startup)."""
    global _unlocked_private_key
    _unlocked_private_key = (private_key_hex.strip() if private_key_hex else None) or None


def get_unlocked_private_key() -> str | None:
    """Return the in-memory private key if wallet was unlocked at startup; else None."""
    return _unlocked_private_key


def is_wallet_unlocked() -> bool:
    return _unlocked_private_key is not None and len(_unlocked_private_key) > 0
