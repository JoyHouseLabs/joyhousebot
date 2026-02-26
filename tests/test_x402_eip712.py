"""
Tests for x402 Payment Protocol - EIP-712 Signing

Tests EIP-712 typed data signing for EIP-3009 (TransferWithAuthorization)
and EIP-2612 (Permit).
"""

import pytest
import secrets
from unittest.mock import MagicMock

from joyhousebot.financial.eip712 import (
    EIP712Signer,
    EIP712Domain,
    TypedDataField,
    sign_transfer_with_authorization,
    sign_permit,
    sign_token_payment,
)
from joyhousebot.financial.chains import SupportedChains


class MockEvmIdentity:
    """Mock EVM identity for testing"""
    
    def __init__(self, private_key_hex: str):
        self.private_key_hex = private_key_hex
        self.address = "0x1234567890123456789012345678901234567890"


class TestEIP712Domain:
    """Test EIP-712 domain separator"""
    
    def test_domain_to_dict(self):
        domain = EIP712Domain(
            name="USD Coin",
            version="2",
            chain_id=8453,
            verifying_contract="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        )
        
        result = domain.to_dict()
        
        assert result["name"] == "USD Coin"
        assert result["version"] == "2"
        assert result["chainId"] == 8453
        assert result["verifyingContract"] == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    
    def test_domain_fields(self):
        domain = EIP712Domain(
            name="Test",
            version="1",
            chain_id=1,
            verifying_contract="0x0000000000000000000000000000000000000001",
        )
        
        fields = domain.fields
        
        assert len(fields) == 4
        assert fields[0].name == "name"
        assert fields[0].type == "string"


class TestEIP712Signer:
    """Test EIP-712 signing"""
    
    def test_encode_type(self):
        types = {
            "TransferWithAuthorization": [
                TypedDataField("from", "address"),
                TypedDataField("to", "address"),
                TypedDataField("value", "uint256"),
            ]
        }
        
        result = EIP712Signer.encode_type("TransferWithAuthorization", types)
        
        assert result == "TransferWithAuthorization(address from,address to,uint256 value)"
    
    def test_type_hash(self):
        types = {
            "Test": [
                TypedDataField("value", "uint256"),
            ]
        }
        
        result = EIP712Signer.type_hash("Test", types)
        
        assert len(result) == 32
        assert isinstance(result, bytes)
    
    def test_encode_field_address(self):
        result = EIP712Signer._encode_field(
            "address",
            "0x1234567890123456789012345678901234567890",
            {}
        )
        
        assert len(result) == 32
    
    def test_encode_field_uint256(self):
        result = EIP712Signer._encode_field("uint256", 1000, {})
        
        assert len(result) == 32
        assert result == (1000).to_bytes(32, "big")
    
    def test_encode_field_string(self):
        result = EIP712Signer._encode_field("string", "hello", {})
        
        assert len(result) == 32


class TestChains:
    """Test chain configuration"""
    
    def test_get_base_mainnet(self):
        chain = SupportedChains.get_chain("eip155:8453")
        
        assert chain is not None
        assert chain.chain_id == 8453
        assert chain.name == "Base"
        assert chain.usdc is not None
        assert chain.usdc.address == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    
    def test_get_chain_by_alias(self):
        chain = SupportedChains.get_chain("base")
        
        assert chain is not None
        assert chain.chain_id == 8453
    
    def test_get_arbitrum(self):
        chain = SupportedChains.get_chain("eip155:42161")
        
        assert chain is not None
        assert chain.chain_id == 42161
        assert chain.usdc is not None
        assert chain.usdt is not None
    
    def test_get_polygon(self):
        chain = SupportedChains.get_chain("eip155:137")
        
        assert chain is not None
        assert chain.chain_id == 137
    
    def test_get_bsc(self):
        chain = SupportedChains.get_chain("eip155:56")
        
        assert chain is not None
        assert chain.chain_id == 56
        assert chain.name == "BNB Smart Chain"
        assert chain.usdc is not None
        assert chain.usdt is not None
    
    def test_get_bsc_by_alias(self):
        chain = SupportedChains.get_chain("bsc")
        assert chain is not None
        assert chain.chain_id == 56
        
        chain = SupportedChains.get_chain("bnb")
        assert chain is not None
        assert chain.chain_id == 56
    
    def test_normalize_network_id(self):
        assert SupportedChains.normalize_network_id("base") == "eip155:8453"
        assert SupportedChains.normalize_network_id("arbitrum") == "eip155:42161"
        assert SupportedChains.normalize_network_id("polygon") == "eip155:137"
        assert SupportedChains.normalize_network_id("bsc") == "eip155:56"
        assert SupportedChains.normalize_network_id("bnb") == "eip155:56"
        assert SupportedChains.normalize_network_id("eip155:8453") == "eip155:8453"
    
    def test_get_all_mainnet_chains(self):
        chains = SupportedChains.get_mainnet_chains()
        
        assert len(chains) >= 3
        chain_ids = [c.chain_id for c in chains]
        assert 8453 in chain_ids
        assert 42161 in chain_ids
        assert 137 in chain_ids
    
    def test_get_token(self):
        chain = SupportedChains.get_chain("eip155:8453")
        
        usdc = chain.get_token("USDC")
        assert usdc is not None
        assert usdc.symbol == "USDC"
        
        usdt = chain.get_token("USDT")
        assert usdt is not None
        assert usdt.symbol == "USDT"
        
        unknown = chain.get_token("UNKNOWN")
        assert unknown is None


class TestSignTransferWithAuthorization:
    """Test USDC TransferWithAuthorization signing"""
    
    def test_sign_structure(self):
        private_key = "0x" + secrets.token_hex(32)
        identity = MockEvmIdentity(private_key)
        
        result = sign_transfer_with_authorization(
            identity=identity,
            usdc_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            chain_id=8453,
            from_address=identity.address,
            to_address="0x0987654321098765432109876543210987654321",
            value=1000000,
            valid_after=0,
            valid_before=9999999999,
            nonce=secrets.token_bytes(32),
        )
        
        assert "signature" in result
        assert "authorization" in result
        assert result["signature"].startswith("0x")
        assert len(result["signature"]) == 132
        assert result["authorization"]["from"] == identity.address


class TestSignPermit:
    """Test EIP-2612 Permit signing for USDT"""
    
    def test_permit_structure(self):
        private_key = "0x" + secrets.token_hex(32)
        identity = MockEvmIdentity(private_key)
        
        result = sign_permit(
            identity=identity,
            token_address="0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2",
            token_name="Tether USD",
            chain_id=8453,
            owner_address=identity.address,
            spender_address="0x0987654321098765432109876543210987654321",
            value=1000000,
            deadline=9999999999,
            nonce=0,
        )
        
        assert "signature" in result
        assert "permit" in result
        assert result["signature"].startswith("0x")
        assert result["permit"]["owner"] == identity.address
        assert "v" in result["permit"]
        assert "r" in result["permit"]
        assert "s" in result["permit"]


class TestSignTokenPayment:
    """Test unified token payment signing"""
    
    def test_usdc_payment(self):
        private_key = "0x" + secrets.token_hex(32)
        identity = MockEvmIdentity(private_key)
        
        result = sign_token_payment(
            identity=identity,
            token_address="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            token_symbol="USDC",
            chain_id=8453,
            from_address=identity.address,
            to_address="0x0987654321098765432109876543210987654321",
            value=1000000,
            valid_after=0,
            valid_before=9999999999,
        )
        
        assert "signature" in result
        assert "authorization" in result
    
    def test_usdt_payment(self):
        private_key = "0x" + secrets.token_hex(32)
        identity = MockEvmIdentity(private_key)
        
        result = sign_token_payment(
            identity=identity,
            token_address="0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2",
            token_symbol="USDT",
            chain_id=8453,
            from_address=identity.address,
            to_address="0x0987654321098765432109876543210987654321",
            value=1000000,
            valid_after=0,
            valid_before=9999999999,
        )
        
        assert "signature" in result
        assert "permit" in result
    
    def test_unsupported_token(self):
        private_key = "0x" + secrets.token_hex(32)
        identity = MockEvmIdentity(private_key)
        
        with pytest.raises(ValueError, match="Unsupported token"):
            sign_token_payment(
                identity=identity,
                token_address="0x0000000000000000000000000000000000000001",
                token_symbol="UNKNOWN",
                chain_id=8453,
                from_address=identity.address,
                to_address="0x0987654321098765432109876543210987654321",
                value=1000000,
                valid_after=0,
                valid_before=9999999999,
            )
