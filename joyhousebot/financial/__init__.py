"""
x402 Payment Protocol Support

Enables USDC/USDT micropayments via HTTP 402.
Based on EIP-3009 (TransferWithAuthorization).
"""

from joyhousebot.financial.eip712 import EIP712Signer, EIP712Domain, TypedDataField
from joyhousebot.financial.x402_client import X402Client, X402PaymentResult, PaymentRequirement
from joyhousebot.financial.token_balance import TokenBalanceChecker, TokenInfo
from joyhousebot.financial.chains import ChainConfig, SupportedChains

__all__ = [
    "EIP712Signer",
    "EIP712Domain",
    "TypedDataField",
    "X402Client",
    "X402PaymentResult",
    "PaymentRequirement",
    "TokenBalanceChecker",
    "TokenInfo",
    "ChainConfig",
    "SupportedChains",
]
