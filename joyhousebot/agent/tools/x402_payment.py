"""
x402 Payment Protocol Tools for Agent.

Enables agents to check USDC/USDT balances and make x402 payments.
Wallet must be unlocked at startup for payment features to work.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from joyhousebot.agent.tools.base import Tool
from joyhousebot.financial.x402_client import X402Client, X402Policy
from joyhousebot.financial.token_balance import TokenBalanceChecker
from joyhousebot.financial.chains import SupportedChains
from joyhousebot.identity.wallet_session import (
    get_wallet_session,
    is_wallet_unlocked,
    get_unlocked_identity,
    get_unlocked_address,
)

logger = logging.getLogger(__name__)


class CheckTokenBalanceTool(Tool):
    """Tool to check USDC/USDT balance on blockchain."""
    
    @property
    def name(self) -> str:
        return "check_token_balance"
    
    @property
    def description(self) -> str:
        return (
            "Check USDC or USDT token balance for a wallet address on a blockchain network. "
            "Supported networks: base (default), arbitrum, polygon, ethereum, bsc. "
            "Supported tokens: USDC (default), USDT."
        )
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "address": {
                    "type": "string",
                    "description": "Wallet address (0x...). If not provided, uses bot's wallet if unlocked.",
                },
                "network": {
                    "type": "string",
                    "description": "Blockchain network: base, arbitrum, polygon, ethereum, bsc. Default: base",
                    "enum": ["base", "arbitrum", "polygon", "ethereum", "bsc"],
                },
                "token": {
                    "type": "string",
                    "description": "Token symbol: USDC or USDT. Default: USDC",
                    "enum": ["USDC", "USDT"],
                },
            },
            "required": [],
        }
    
    async def execute(
        self,
        address: Optional[str] = None,
        network: str = "base",
        token: str = "USDC",
        **kwargs: Any,
    ) -> str:
        checker = TokenBalanceChecker()
        try:
            if not address:
                address = get_unlocked_address()
                if not address:
                    return "Error: No address provided and wallet is not unlocked"
            
            normalized_network = SupportedChains.normalize_network_id(network)
            if not normalized_network:
                return f"Error: Unsupported network '{network}'"
            
            result = await checker.get_balance(address, normalized_network, token)
            
            if result.ok:
                return f"Balance: {result.balance:.2f} {result.symbol} on {result.chain}"
            else:
                return f"Error: {result.error}"
        finally:
            await checker.close()


class X402FetchTool(Tool):
    """Tool to make HTTP requests with x402 payment support."""
    
    def __init__(self, policy: Optional[X402Policy] = None):
        self._policy = policy or X402Policy()
    
    @property
    def name(self) -> str:
        return "x402_fetch"
    
    @property
    def description(self) -> str:
        return (
            "Fetch URL with automatic x402 payment support. "
            "If the endpoint requires payment (returns HTTP 402), "
            "automatically signs and pays using USDC from the bot's wallet. "
            "REQUIRES wallet to be unlocked at startup. "
            "Use with caution - only call URLs you trust."
        )
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to fetch",
                },
                "method": {
                    "type": "string",
                    "description": "HTTP method: GET, POST, PUT, DELETE. Default: GET",
                    "enum": ["GET", "POST", "PUT", "DELETE"],
                },
                "body": {
                    "type": "string",
                    "description": "Request body (for POST/PUT)",
                },
                "max_payment_cents": {
                    "type": "integer",
                    "description": "Maximum payment allowed in cents (e.g., 50 = $0.50). Default: 100 ($1)",
                    "minimum": 1,
                    "maximum": 10000,
                },
            },
            "required": ["url"],
        }
    
    async def execute(
        self,
        url: str,
        method: str = "GET",
        body: Optional[str] = None,
        max_payment_cents: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        if not is_wallet_unlocked():
            return "Error: Wallet is not unlocked. Payment features require wallet to be unlocked at startup."
        
        identity = get_unlocked_identity()
        if not identity:
            return "Error: Could not get wallet identity"
        
        client = X402Client(policy=self._policy)
        try:
            result = await client.fetch_with_payment(
                url=url,
                identity=identity,
                method=method,
                body=body,
                max_payment_cents=max_payment_cents,
            )
            
            if result.success:
                response_text = str(result.response) if result.response else "OK"
                if result.amount_paid:
                    return f"Paid ${result.amount_paid:.2f}. Response: {response_text}"
                return response_text
            else:
                return f"Error: {result.error}"
        finally:
            await client.close()


class GetWalletStatusTool(Tool):
    """Tool to check wallet status."""
    
    @property
    def name(self) -> str:
        return "get_wallet_status"
    
    @property
    def description(self) -> str:
        return (
            "Get the bot's wallet status. "
            "Shows if wallet exists, is unlocked, and the address if available."
        )
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }
    
    async def execute(self, **kwargs: Any) -> str:
        session = get_wallet_session()
        status = session.get_status()
        
        lines = []
        if status["has_wallet"]:
            lines.append("Wallet: exists")
        else:
            lines.append("Wallet: not found")
        
        if status["is_unlocked"]:
            lines.append(f"Status: unlocked")
            lines.append(f"Address: {status['address']}")
        else:
            lines.append("Status: locked")
        
        return "\n".join(lines)


class GetSupportedNetworksTool(Tool):
    """Tool to list supported blockchain networks."""
    
    @property
    def name(self) -> str:
        return "get_supported_networks"
    
    @property
    def description(self) -> str:
        return (
            "List all supported blockchain networks for USDC/USDT payments "
            "with their chain IDs and token addresses."
        )
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
            "required": [],
        }
    
    async def execute(self, **kwargs: Any) -> str:
        chains = SupportedChains.get_mainnet_chains()
        lines = ["Supported networks:"]
        for chain in chains:
            lines.append(f"\n{chain.name} (chain_id={chain.chain_id}):")
            if chain.usdc:
                lines.append(f"  USDC: {chain.usdc.address}")
            if chain.usdt:
                lines.append(f"  USDT: {chain.usdt.address}")
        return "\n".join(lines)


def register_x402_tools(
    registry: Any,
    policy: Optional[X402Policy] = None,
    enabled: bool = True,
) -> None:
    """
    Register x402 payment tools with the tool registry.
    
    Args:
        registry: ToolRegistry instance
        policy: Optional X402Policy for payment constraints
        enabled: Whether tools are enabled by default
    """
    registry.register(CheckTokenBalanceTool(), optional=not enabled)
    registry.register(X402FetchTool(policy), optional=not enabled)
    registry.register(GetWalletStatusTool(), optional=not enabled)
    registry.register(GetSupportedNetworksTool(), optional=not enabled)
