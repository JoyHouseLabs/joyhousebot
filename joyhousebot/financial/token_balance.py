"""
Token Balance Checker

Query ERC20 token balances (USDC/USDT) from blockchain.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from joyhousebot.financial.chains import ChainConfig, SupportedChains, TokenInfo

logger = logging.getLogger(__name__)

BALANCE_OF_ABI = [
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]

ERC20_ABI = BALANCE_OF_ABI + [
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function",
    },
]


def encode_function_call(function_name: str, abi: List[Dict], args: List[Any]) -> str:
    """
    Encode a function call for eth_call.
    Simple implementation without web3.py dependency.
    """
    func_abi = None
    for item in abi:
        if item.get("type") == "function" and item.get("name") == function_name:
            func_abi = item
            break
    
    if not func_abi:
        raise ValueError(f"Function {function_name} not found in ABI")
    
    signature = f"{function_name}({','.join(inp['type'] for inp in func_abi['inputs'])})"
    selector = keccak256(signature.encode("utf-8"))[:4].hex()
    
    encoded_args = ""
    for i, arg in enumerate(args):
        arg_type = func_abi["inputs"][i]["type"]
        encoded_args += encode_arg(arg_type, arg)
    
    return "0x" + selector + encoded_args


def encode_arg(arg_type: str, value: Any) -> str:
    """Encode a single argument"""
    if arg_type == "address":
        if isinstance(value, str) and value.startswith("0x"):
            value = value[2:]
        return value.lower().zfill(64)
    
    if arg_type.startswith("uint"):
        if isinstance(value, str):
            if value.startswith("0x"):
                value = int(value[2:], 16)
            else:
                value = int(value)
        return hex(value)[2:].zfill(64)
    
    if arg_type == "bytes32":
        if isinstance(value, str):
            if value.startswith("0x"):
                value = bytes.fromhex(value[2:])
            else:
                value = value.encode("utf-8")
        return value.hex().ljust(64, "0")[:64]
    
    raise ValueError(f"Unsupported type: {arg_type}")


def decode_uint256(data: str) -> int:
    """Decode uint256 from hex string"""
    if data.startswith("0x"):
        data = data[2:]
    return int(data, 16)


def keccak256(data: bytes) -> bytes:
    """Keccak-256 hash"""
    from Crypto.Hash import keccak
    h = keccak.new(digest_bits=256)
    h.update(data)
    return h.digest()


@dataclass
class BalanceResult:
    """Token balance query result"""
    balance: float
    symbol: str
    decimals: int
    chain: str
    token_address: str
    wallet_address: str
    ok: bool
    error: Optional[str] = None


class TokenBalanceChecker:
    """Query ERC20 token balances from blockchain"""
    
    def __init__(
        self,
        rpc_urls: Optional[Dict[str, str]] = None,
        timeout: float = 10.0,
    ):
        """
        Initialize balance checker.
        
        Args:
            rpc_urls: Custom RPC URLs per network ID
            timeout: Request timeout in seconds
        """
        self.rpc_urls = rpc_urls or {}
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client"""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client
    
    async def close(self) -> None:
        """Close HTTP client"""
        if self._client:
            await self._client.aclose()
            self._client = None
    
    def _get_rpc_url(self, chain: ChainConfig) -> str:
        """Get RPC URL for chain"""
        if chain.network_id in self.rpc_urls:
            return self.rpc_urls[chain.network_id]
        return chain.rpc_url
    
    async def eth_call(
        self,
        rpc_url: str,
        to: str,
        data: str,
        block: str = "latest",
    ) -> Optional[str]:
        """Make eth_call RPC request"""
        client = await self._get_client()
        
        payload = {
            "jsonrpc": "2.0",
            "method": "eth_call",
            "params": [{"to": to, "data": data}, block],
            "id": 1,
        }
        
        try:
            resp = await client.post(rpc_url, json=payload)
            resp.raise_for_status()
            result = resp.json()
            
            if "error" in result:
                logger.error(f"RPC error: {result['error']}")
                return None
            
            return result.get("result")
        except Exception as e:
            logger.error(f"eth_call failed: {e}")
            return None
    
    async def get_balance(
        self,
        wallet_address: str,
        network_id: str = "eip155:8453",
        token: str = "USDC",
    ) -> BalanceResult:
        """
        Get token balance for wallet.
        
        Args:
            wallet_address: Wallet address (0x...)
            network_id: Chain network ID
            token: Token symbol (USDC or USDT)
        
        Returns:
            BalanceResult with balance in human-readable format
        """
        chain = SupportedChains.get_chain(network_id)
        if not chain:
            return BalanceResult(
                balance=0,
                symbol=token.upper(),
                decimals=6,
                chain=network_id,
                token_address="",
                wallet_address=wallet_address,
                ok=False,
                error=f"Unsupported network: {network_id}",
            )
        
        token_info = chain.get_token(token)
        if not token_info:
            return BalanceResult(
                balance=0,
                symbol=token.upper(),
                decimals=6,
                chain=network_id,
                token_address="",
                wallet_address=wallet_address,
                ok=False,
                error=f"Token {token} not supported on {chain.name}",
            )
        
        rpc_url = self._get_rpc_url(chain)
        
        data = encode_function_call("balanceOf", BALANCE_OF_ABI, [wallet_address])
        
        result = await self.eth_call(rpc_url, token_info.address, data)
        
        if result is None:
            return BalanceResult(
                balance=0,
                symbol=token_info.symbol,
                decimals=token_info.decimals,
                chain=network_id,
                token_address=token_info.address,
                wallet_address=wallet_address,
                ok=False,
                error="RPC call failed",
            )
        
        try:
            raw_balance = decode_uint256(result)
            human_balance = raw_balance / (10 ** token_info.decimals)
            
            return BalanceResult(
                balance=human_balance,
                symbol=token_info.symbol,
                decimals=token_info.decimals,
                chain=network_id,
                token_address=token_info.address,
                wallet_address=wallet_address,
                ok=True,
            )
        except Exception as e:
            return BalanceResult(
                balance=0,
                symbol=token_info.symbol,
                decimals=token_info.decimals,
                chain=network_id,
                token_address=token_info.address,
                wallet_address=wallet_address,
                ok=False,
                error=str(e),
            )
    
    async def get_usdc_balance(
        self,
        wallet_address: str,
        network_id: str = "eip155:8453",
    ) -> float:
        """
        Get USDC balance (convenience method).
        
        Returns balance as float, 0 on error.
        """
        result = await self.get_balance(wallet_address, network_id, "USDC")
        return result.balance if result.ok else 0
    
    async def get_usdt_balance(
        self,
        wallet_address: str,
        network_id: str = "eip155:8453",
    ) -> float:
        """
        Get USDT balance (convenience method).
        
        Returns balance as float, 0 on error.
        """
        result = await self.get_balance(wallet_address, network_id, "USDT")
        return result.balance if result.ok else 0
    
    async def get_all_balances(
        self,
        wallet_address: str,
        networks: Optional[List[str]] = None,
        tokens: Optional[List[str]] = None,
    ) -> List[BalanceResult]:
        """
        Get balances across multiple networks and tokens.
        
        Args:
            wallet_address: Wallet address
            networks: List of network IDs (default: all mainnets)
            tokens: List of token symbols (default: USDC, USDT)
        
        Returns:
            List of BalanceResult for each combination
        """
        if networks is None:
            networks = [
                "eip155:8453",
                "eip155:42161",
                "eip155:137",
            ]
        
        if tokens is None:
            tokens = ["USDC", "USDT"]
        
        tasks = []
        for network_id in networks:
            for token in tokens:
                tasks.append(
                    self.get_balance(wallet_address, network_id, token)
                )
        
        return await asyncio.gather(*tasks)


async def get_token_balance(
    wallet_address: str,
    network_id: str = "eip155:8453",
    token: str = "USDC",
) -> float:
    """
    Quick function to get token balance.
    
    Creates a temporary checker, queries balance, and closes.
    """
    checker = TokenBalanceChecker()
    try:
        result = await checker.get_balance(wallet_address, network_id, token)
        return result.balance if result.ok else 0
    finally:
        await checker.close()
