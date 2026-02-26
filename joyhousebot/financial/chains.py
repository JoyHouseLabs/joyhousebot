"""
Blockchain Configuration for x402 Protocol

Supported chains and token addresses for USDC/USDT payments.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class TokenInfo:
    """Token contract information"""
    address: str
    symbol: str
    name: str
    decimals: int
    version: str = "2"


@dataclass
class ChainConfig:
    """Blockchain configuration"""
    chain_id: int
    network_id: str
    name: str
    rpc_url: str
    usdc: Optional[TokenInfo] = None
    usdt: Optional[TokenInfo] = None
    is_testnet: bool = False
    block_explorer: str = ""
    
    def get_token(self, symbol: str) -> Optional[TokenInfo]:
        """Get token info by symbol (case-insensitive)"""
        symbol_upper = symbol.upper()
        if symbol_upper == "USDC":
            return self.usdc
        if symbol_upper == "USDT":
            return self.usdt
        return None


class SupportedChains:
    """Registry of supported blockchain networks"""
    
    CHAINS: Dict[str, ChainConfig] = {}
    
    BASE_MAINNET = ChainConfig(
        chain_id=8453,
        network_id="eip155:8453",
        name="Base",
        rpc_url="https://mainnet.base.org",
        usdc=TokenInfo(
            address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            symbol="USDC",
            name="USD Coin",
            decimals=6,
            version="2",
        ),
        usdt=TokenInfo(
            address="0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2",
            symbol="USDT",
            name="Tether USD",
            decimals=6,
        ),
        is_testnet=False,
        block_explorer="https://basescan.org",
    )
    
    BASE_SEPOLIA = ChainConfig(
        chain_id=84532,
        network_id="eip155:84532",
        name="Base Sepolia",
        rpc_url="https://sepolia.base.org",
        usdc=TokenInfo(
            address="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            symbol="USDC",
            name="USD Coin",
            decimals=6,
            version="2",
        ),
        is_testnet=True,
        block_explorer="https://sepolia.basescan.org",
    )
    
    ARBITRUM_ONE = ChainConfig(
        chain_id=42161,
        network_id="eip155:42161",
        name="Arbitrum One",
        rpc_url="https://arb1.arbitrum.io/rpc",
        usdc=TokenInfo(
            address="0xaf88d065e77c8cC2239327C5EDb3A432268e5831",
            symbol="USDC",
            name="USD Coin",
            decimals=6,
            version="2",
        ),
        usdt=TokenInfo(
            address="0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
            symbol="USDT",
            name="Tether USD",
            decimals=6,
        ),
        is_testnet=False,
        block_explorer="https://arbiscan.io",
    )
    
    POLYGON = ChainConfig(
        chain_id=137,
        network_id="eip155:137",
        name="Polygon",
        rpc_url="https://polygon-rpc.com",
        usdc=TokenInfo(
            address="0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359",
            symbol="USDC",
            name="USD Coin",
            decimals=6,
            version="2",
        ),
        usdt=TokenInfo(
            address="0xc2132D05D31c914a87C6611C10748AEb04B58e8F",
            symbol="USDT",
            name="Tether USD",
            decimals=6,
        ),
        is_testnet=False,
        block_explorer="https://polygonscan.com",
    )
    
    ETHEREUM = ChainConfig(
        chain_id=1,
        network_id="eip155:1",
        name="Ethereum",
        rpc_url="https://eth.llamarpc.com",
        usdc=TokenInfo(
            address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            symbol="USDC",
            name="USD Coin",
            decimals=6,
            version="2",
        ),
        usdt=TokenInfo(
            address="0xdAC17F958D2ee523a2206206994597C13D831ec7",
            symbol="USDT",
            name="Tether USD",
            decimals=6,
        ),
        is_testnet=False,
        block_explorer="https://etherscan.io",
    )
    
    BSC = ChainConfig(
        chain_id=56,
        network_id="eip155:56",
        name="BNB Smart Chain",
        rpc_url="https://bsc-dataseed1.binance.org",
        usdc=TokenInfo(
            address="0x8AC76a50cc25877605Fd8aB64762B617D09F42fB",
            symbol="USDC",
            name="USD Coin",
            decimals=18,
            version="2",
        ),
        usdt=TokenInfo(
            address="0x55d398326f99059fF775485246999027B3197955",
            symbol="USDT",
            name="Tether USD",
            decimals=18,
        ),
        is_testnet=False,
        block_explorer="https://bscscan.com",
    )
    
    BSC_TESTNET = ChainConfig(
        chain_id=97,
        network_id="eip155:97",
        name="BSC Testnet",
        rpc_url="https://data-seed-prebsc-1-s1.binance.org:8545",
        is_testnet=True,
        block_explorer="https://testnet.bscscan.com",
    )
    
    @classmethod
    def _init_chains(cls) -> None:
        """Initialize chain registry"""
        if cls.CHAINS:
            return
        cls.CHAINS = {
            "eip155:8453": cls.BASE_MAINNET,
            "eip155:84532": cls.BASE_SEPOLIA,
            "eip155:42161": cls.ARBITRUM_ONE,
            "eip155:137": cls.POLYGON,
            "eip155:1": cls.ETHEREUM,
            "eip155:56": cls.BSC,
            "eip155:97": cls.BSC_TESTNET,
            "base": cls.BASE_MAINNET,
            "base-sepolia": cls.BASE_SEPOLIA,
            "arbitrum": cls.ARBITRUM_ONE,
            "polygon": cls.POLYGON,
            "ethereum": cls.ETHEREUM,
            "eth": cls.ETHEREUM,
            "bsc": cls.BSC,
            "bnb": cls.BSC,
            "bnb-smart-chain": cls.BSC,
            "bsc-testnet": cls.BSC_TESTNET,
        }
    
    @classmethod
    def get_chain(cls, network_id: str) -> Optional[ChainConfig]:
        """Get chain config by network ID"""
        cls._init_chains()
        normalized = network_id.strip().lower()
        
        if normalized in cls.CHAINS:
            return cls.CHAINS[normalized]
        
        if normalized == "base":
            return cls.CHAINS["eip155:8453"]
        if normalized == "base-sepolia":
            return cls.CHAINS["eip155:84532"]
        
        return None
    
    @classmethod
    def get_all_chains(cls, testnet: bool = False) -> List[ChainConfig]:
        """Get all supported chains"""
        cls._init_chains()
        seen = set()
        chains = []
        for config in cls.CHAINS.values():
            if config.network_id not in seen:
                if testnet or not config.is_testnet:
                    chains.append(config)
                    seen.add(config.network_id)
        return chains
    
    @classmethod
    def get_mainnet_chains(cls) -> List[ChainConfig]:
        """Get mainnet chains only"""
        return cls.get_all_chains(testnet=False)
    
    @classmethod
    def normalize_network_id(cls, raw: str) -> Optional[str]:
        """Normalize network ID to eip155:chainid format"""
        cls._init_chains()
        normalized = raw.strip().lower()
        
        mapping = {
            "base": "eip155:8453",
            "base-sepolia": "eip155:84532",
            "arbitrum": "eip155:42161",
            "arbitrum-one": "eip155:42161",
            "polygon": "eip155:137",
            "ethereum": "eip155:1",
            "eth": "eip155:1",
            "bsc": "eip155:56",
            "bnb": "eip155:56",
            "bnb-smart-chain": "eip155:56",
            "bsc-testnet": "eip155:97",
        }
        
        if normalized in mapping:
            return mapping[normalized]
        
        if normalized.startswith("eip155:"):
            return normalized
        
        return None


DEFAULT_CHAIN = SupportedChains.BASE_MAINNET
DEFAULT_NETWORK_ID = "eip155:8453"
