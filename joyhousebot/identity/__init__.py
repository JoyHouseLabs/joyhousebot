"""Identity helpers."""

from joyhousebot.identity.ed25519 import BotIdentity, ensure_bot_identity
from joyhousebot.identity.evm import EvmIdentity, ensure_identity, sign_challenge
from joyhousebot.identity.unlocked_wallet import (
    get_unlocked_private_key,
    is_wallet_unlocked,
    set_unlocked_private_key,
)

# Ed25519 bot identity signing (no EVM/address)
def sign_bot_challenge(private_key_hex: str, challenge: str) -> str:
    from joyhousebot.identity.ed25519 import sign_challenge as _sign
    return _sign(private_key_hex, challenge)

__all__ = [
    "BotIdentity",
    "EvmIdentity",
    "ensure_bot_identity",
    "ensure_identity",
    "sign_bot_challenge",
    "sign_challenge",
    "get_unlocked_private_key",
    "is_wallet_unlocked",
    "set_unlocked_private_key",
]

