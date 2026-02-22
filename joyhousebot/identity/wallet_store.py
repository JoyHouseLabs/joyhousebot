"""Wallet storage: password validation and encrypted EVM private key in SQLite."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes

from joyhousebot.identity.evm import (
    _generate_private_key_hex,
    identity_from_private_key,
)
from joyhousebot.storage.sqlite_store import LocalStateStore

# Password: at least 8 chars, must include both uppercase and lowercase
MIN_PASSWORD_LEN = 8

# Default chain for EVM
DEFAULT_CHAIN = "evm"
DEFAULT_CHAIN_ID = 1


def validate_wallet_password(password: str) -> None:
    """Raise ValueError if password does not meet requirements."""
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"密码长度至少 {MIN_PASSWORD_LEN} 位")
    if not any(c.isupper() for c in password):
        raise ValueError("密码须包含大写字母")
    if not any(c.islower() for c in password):
        raise ValueError("密码须包含小写字母")


def get_wallet_file_path(data_dir: Path | None = None) -> Path:
    """Path to the legacy encrypted wallet file (for migration)."""
    if data_dir is None:
        from joyhousebot.config.loader import get_data_dir
        data_dir = get_data_dir()
    return data_dir / "wallet.enc"


def _derive_key(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        100_000,
        dklen=32,
    )


def _encrypt_private_key(private_key_hex: str, password: str) -> str:
    salt = get_random_bytes(16)
    key = _derive_key(password, salt)
    cipher = AES.new(key, AES.MODE_GCM)
    ciphertext, tag = cipher.encrypt_and_digest(private_key_hex.encode("utf-8"))
    nonce = cipher.nonce
    blob = salt + nonce + tag + ciphertext
    return base64.b64encode(blob).decode("ascii")


def _decrypt_blob(encoded: str, password: str) -> str:
    try:
        blob = base64.b64decode(encoded)
    except Exception as e:
        raise ValueError("钱包数据格式无效") from e
    if len(blob) < 16 + 16 + 16:
        raise ValueError("钱包数据格式无效")
    salt = blob[:16]
    nonce = blob[16:32]
    tag = blob[32:48]
    ciphertext = blob[48:]
    key = _derive_key(password, salt)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    try:
        plaintext = cipher.decrypt_and_verify(ciphertext, tag)
    except Exception:
        raise ValueError("密码错误")
    return plaintext.decode("utf-8")


def _resolve_wallet(wallet_id: int | None = None, address: str | None = None) -> dict:
    """Resolve wallet row by id or address; default wallet if both None."""
    store = LocalStateStore.default()
    if wallet_id is not None:
        row = store.get_wallet_by_id(wallet_id)
    elif address and address.strip():
        row = store.get_wallet_by_address(address.strip())
    else:
        row = store.get_wallet()
    if not row:
        raise ValueError("钱包不存在")
    return row


def create_and_save_wallet(
    password: str,
    wallet_path: Path | None = None,
    chain: str = DEFAULT_CHAIN,
    chain_id: int = DEFAULT_CHAIN_ID,
    set_as_default: bool = True,
) -> str:
    """
    Generate new EVM key, encrypt with password, save to SQLite.
    Returns the EVM address (0x...).
    """
    validate_wallet_password(password)
    private_key_hex = _generate_private_key_hex()
    identity = identity_from_private_key(private_key_hex)
    encrypted_blob = _encrypt_private_key(private_key_hex, password)
    store = LocalStateStore.default()
    store.add_wallet(
        address=identity.address,
        encrypted_blob=encrypted_blob,
        chain=chain,
        chain_id=chain_id,
        set_as_default=set_as_default,
    )
    path = wallet_path if wallet_path is not None else get_wallet_file_path()
    if path.exists():
        path.unlink()
    return identity.address


def clear_wallet_file(wallet_path: Path | None = None) -> None:
    """Remove default wallet from SQLite and legacy file if present."""
    LocalStateStore.default().delete_wallet()
    path = wallet_path if wallet_path is not None else get_wallet_file_path()
    if path.exists():
        path.unlink()


def wallet_file_exists(wallet_path: Path | None = None) -> bool:
    """Whether any wallet exists (SQLite or legacy file)."""
    if LocalStateStore.default().get_wallet() is not None:
        return True
    path = wallet_path if wallet_path is not None else get_wallet_file_path()
    return path.exists()


def list_wallets() -> list[dict]:
    """List all wallets (id, address, chain, chain_id, is_default, created_at)."""
    return LocalStateStore.default().list_wallets()


def set_default_wallet(wallet_id: int | None = None, address: str | None = None) -> None:
    """Set default wallet by id or address."""
    store = LocalStateStore.default()
    row = _resolve_wallet(wallet_id=wallet_id, address=address)
    store.set_default_wallet(row["id"])


def change_wallet_password(
    old_password: str,
    new_password: str,
    wallet_id: int | None = None,
    address: str | None = None,
) -> None:
    """Change password for a wallet (decrypt with old, re-encrypt with new)."""
    validate_wallet_password(new_password)
    row = _resolve_wallet(wallet_id=wallet_id, address=address)
    private_key_hex = _decrypt_blob(row["encrypted_blob"], old_password)
    new_blob = _encrypt_private_key(private_key_hex, new_password)
    LocalStateStore.default().update_wallet_encrypted_blob(row["id"], new_blob)


def decrypt_wallet(
    password: str,
    wallet_id: int | None = None,
    address: str | None = None,
    wallet_path: Path | None = None,
) -> str:
    """
    Decrypt wallet with password, return private key hex.
    If wallet_id/address given, decrypt that wallet; else default wallet.
    Legacy: if no row in SQLite but wallet.enc exists, decrypt from file and migrate.
    """
    store = LocalStateStore.default()
    row = store.get_wallet() if (wallet_id is None and not (address and address.strip())) else None
    if wallet_id is not None or (address and address.strip()):
        row = _resolve_wallet(wallet_id=wallet_id, address=address)
    if row is not None:
        return _decrypt_blob(row["encrypted_blob"], password)
    path = wallet_path if wallet_path is not None else get_wallet_file_path()
    if not path.exists():
        raise ValueError("钱包不存在")
    encoded = path.read_text(encoding="utf-8")
    plaintext = _decrypt_blob(encoded, password)
    identity = identity_from_private_key(plaintext)
    store.set_wallet(
        address=identity.address,
        encrypted_blob=encoded,
        chain=DEFAULT_CHAIN,
        chain_id=DEFAULT_CHAIN_ID,
    )
    path.unlink()
    return plaintext


def get_wallet_address() -> str | None:
    """Get default wallet address from SQLite (no password)."""
    row = LocalStateStore.default().get_wallet()
    return row["address"] if row else None


def get_wallet_chain_info() -> tuple[str, int]:
    """Get (chain, chain_id) of default wallet; defaults if no wallet."""
    row = LocalStateStore.default().get_wallet()
    if row is None:
        return DEFAULT_CHAIN, DEFAULT_CHAIN_ID
    return str(row["chain"]), int(row["chain_id"])
