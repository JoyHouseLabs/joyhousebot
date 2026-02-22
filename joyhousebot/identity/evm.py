"""EVM identity generation and signing."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from pathlib import Path

from Crypto.Hash import keccak
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, utils
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from joyhousebot.utils.helpers import ensure_dir

SECP256K1_N = int(
    "FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141",
    16,
)


@dataclass
class EvmIdentity:
    private_key_hex: str
    address: str
    public_key_hex: str


def _normalize_key_hex(value: str) -> str:
    key = value.strip()
    if key.startswith("0x"):
        key = key[2:]
    if len(key) != 64:
        raise ValueError("Invalid private key length, expected 32-byte hex")
    key_int = int(key, 16)
    if not (0 < key_int < SECP256K1_N):
        raise ValueError("Invalid private key range for secp256k1")
    return key.lower()


def _keccak256(data: bytes) -> bytes:
    h = keccak.new(digest_bits=256)
    h.update(data)
    return h.digest()


def _private_key_from_hex(private_key_hex: str) -> ec.EllipticCurvePrivateKey:
    key_int = int(_normalize_key_hex(private_key_hex), 16)
    return ec.derive_private_key(key_int, ec.SECP256K1())


def _address_from_private_key(private_key_hex: str) -> str:
    private_key = _private_key_from_hex(private_key_hex)
    public_key = private_key.public_key()
    uncompressed = public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    # Ethereum address is last 20 bytes of keccak(pubkey_without_prefix_0x04)
    addr = _keccak256(uncompressed[1:])[-20:].hex()
    return f"0x{addr}"


def _public_key_from_private_key(private_key_hex: str) -> str:
    private_key = _private_key_from_hex(private_key_hex)
    public_key = private_key.public_key()
    uncompressed = public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return f"0x{uncompressed.hex()}"


def _generate_private_key_hex() -> str:
    while True:
        raw = secrets.token_bytes(32)
        key_int = int.from_bytes(raw, "big")
        if 0 < key_int < SECP256K1_N:
            return raw.hex()


def load_or_create_private_key(path: Path) -> str:
    """Load EVM private key from disk or create a new one."""
    if path.exists():
        return _normalize_key_hex(path.read_text(encoding="utf-8"))

    private_key_hex = _generate_private_key_hex()

    ensure_dir(path.parent)
    path.write_text(private_key_hex, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        # On some systems/channels chmod may fail; keep best-effort behavior.
        pass
    return private_key_hex


def identity_from_private_key(private_key_hex: str) -> EvmIdentity:
    key = _normalize_key_hex(private_key_hex)
    return EvmIdentity(
        private_key_hex=key,
        address=_address_from_private_key(key),
        public_key_hex=_public_key_from_private_key(key),
    )


def ensure_identity(key_path: Path) -> EvmIdentity:
    key_hex = load_or_create_private_key(key_path)
    return identity_from_private_key(key_hex)


def sign_challenge(private_key_hex: str, challenge: str) -> str:
    """
    Sign challenge with secp256k1 key and return compact r||s hex.

    Notes:
    - This signs the Ethereum prefixed message hash.
    - Recovery id (v) is intentionally omitted for lightweight MVP transport.
    """
    key = _normalize_key_hex(private_key_hex)
    private_key = _private_key_from_hex(key)
    msg = challenge.encode("utf-8")
    prefixed = f"\x19Ethereum Signed Message:\n{len(msg)}".encode("utf-8") + msg
    digest = _keccak256(prefixed)
    signature_der = private_key.sign(digest, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
    r, s = utils.decode_dss_signature(signature_der)
    return f"0x{r:064x}{s:064x}"

