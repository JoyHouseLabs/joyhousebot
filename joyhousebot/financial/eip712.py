"""
EIP-712 Typed Data Signing

Implements EIP-712 for signing structured data, used by x402 protocol
for TransferWithAuthorization (EIP-3009).

Reference: https://eips.ethereum.org/EIPS/eip-712
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

from joyhousebot.identity.evm import (
    EvmIdentity,
    _keccak256,
    _private_key_from_hex,
)


@dataclass
class TypedDataField:
    """EIP-712 type field definition"""
    name: str
    type: str

    def to_dict(self) -> Dict[str, str]:
        return {"name": self.name, "type": self.type}


@dataclass
class EIP712Domain:
    """EIP-712 domain separator"""
    name: str
    version: str
    chain_id: int
    verifying_contract: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "chainId": self.chain_id,
            "verifyingContract": self.verifying_contract,
        }

    @property
    def fields(self) -> List[TypedDataField]:
        return [
            TypedDataField("name", "string"),
            TypedDataField("version", "string"),
            TypedDataField("chainId", "uint256"),
            TypedDataField("verifyingContract", "address"),
        ]


class EIP712Signer:
    """EIP-712 typed data signing implementation"""

    EIP712_DOMAIN_TYPE = "EIP712Domain"
    EIP712_DOMAIN_PREFIX = "\x19\x01"
    EIP712_TYPE_HASH_PREFIX = "\x19\x01"

    @staticmethod
    def encode_type(primary_type: str, types: Dict[str, List[TypedDataField]]) -> str:
        """
        Encode type string for hashing.
        Format: TypeName(field1:type1,field2:type2,...)
        """
        result = []
        deps = EIP712Signer._get_dependencies(primary_type, types)
        
        for type_name in [primary_type] + deps:
            if type_name not in types:
                continue
            fields = types[type_name]
            field_strs = [f"{f.type} {f.name}" for f in fields]
            result.append(f"{type_name}({','.join(field_strs)})")
        
        return "".join(result)

    @staticmethod
    def _get_dependencies(
        primary_type: str,
        types: Dict[str, List[TypedDataField]],
        visited: Optional[set] = None,
    ) -> List[str]:
        """Get ordered list of dependent types"""
        if visited is None:
            visited = set()
        
        if primary_type in visited or primary_type not in types:
            return []
        
        visited.add(primary_type)
        deps = []
        
        for field in types[primary_type]:
            field_type = field.type
            if field_type in types and field_type not in visited:
                deps.extend(EIP712Signer._get_dependencies(field_type, types, visited))
        
        return deps

    @staticmethod
    def type_hash(primary_type: str, types: Dict[str, List[TypedDataField]]) -> bytes:
        """Compute keccak256 hash of type string"""
        encoded = EIP712Signer.encode_type(primary_type, types)
        return _keccak256(encoded.encode("utf-8"))

    @staticmethod
    def hash_struct(
        primary_type: str,
        types: Dict[str, List[TypedDataField]],
        value: Dict[str, Any],
    ) -> bytes:
        """Hash a struct according to EIP-712"""
        encoded = EIP712Signer._encode_data(primary_type, types, value)
        return _keccak256(encoded)

    @staticmethod
    def _encode_data(
        primary_type: str,
        types: Dict[str, List[TypedDataField]],
        value: Dict[str, Any],
    ) -> bytes:
        """Encode struct data for hashing"""
        encoded_types: List[bytes] = []
        
        type_hash = EIP712Signer.type_hash(primary_type, types)
        encoded_types.append(type_hash)
        
        if primary_type not in types:
            raise ValueError(f"Type {primary_type} not found in types")
        
        for field in types[primary_type]:
            field_value = value.get(field.name)
            encoded_value = EIP712Signer._encode_field(
                field.type, field_value, types
            )
            encoded_types.append(encoded_value)
        
        return b"".join(encoded_types)

    @staticmethod
    def _encode_field(
        field_type: str,
        value: Any,
        types: Dict[str, List[TypedDataField]],
    ) -> bytes:
        """Encode a single field value"""
        if value is None:
            return b"\x00" * 32
        
        if field_type == "string":
            if isinstance(value, str):
                return _keccak256(value.encode("utf-8"))
            return _keccak256(value)
        
        if field_type == "bytes":
            if isinstance(value, str):
                if value.startswith("0x"):
                    value = bytes.fromhex(value[2:])
                else:
                    value = value.encode("utf-8")
            return _keccak256(value)
        
        if field_type == "bool":
            return (1 if value else 0).to_bytes(32, "big")
        
        if field_type == "address":
            if isinstance(value, str):
                if value.startswith("0x"):
                    value = value[2:]
                return bytes.fromhex(value.zfill(64))
            return value
        
        if field_type.startswith("uint"):
            bits = int(field_type[4:]) if len(field_type) > 4 else 256
            if isinstance(value, str):
                if value.startswith("0x"):
                    value = int(value[2:], 16)
                else:
                    value = int(value)
            return value.to_bytes(32, "big")
        
        if field_type.startswith("int"):
            bits = int(field_type[3:]) if len(field_type) > 3 else 256
            if isinstance(value, str):
                if value.startswith("0x"):
                    value = int(value[2:], 16)
                else:
                    value = int(value)
            if value < 0:
                value = value + (1 << bits)
            return value.to_bytes(32, "big")
        
        if field_type.startswith("bytes"):
            length = int(field_type[5:]) if len(field_type) > 5 else 32
            if isinstance(value, str):
                if value.startswith("0x"):
                    value = bytes.fromhex(value[2:])
                else:
                    value = value.encode("utf-8")
            return value.ljust(32, b"\x00")[:32]
        
        if field_type.endswith("[]"):
            item_type = field_type[:-2]
            if not isinstance(value, list):
                value = [value]
            encoded_items = [
                EIP712Signer._encode_field(item_type, item, types)
                for item in value
            ]
            return _keccak256(b"".join(encoded_items))
        
        if field_type in types:
            return EIP712Signer.hash_struct(field_type, types, value)
        
        raise ValueError(f"Unsupported field type: {field_type}")

    @staticmethod
    def hash_domain(domain: EIP712Domain) -> bytes:
        """Hash the domain separator"""
        types: Dict[str, List[TypedDataField]] = {
            "EIP712Domain": domain.fields
        }
        return EIP712Signer.hash_struct("EIP712Domain", types, domain.to_dict())

    @staticmethod
    def sign_typed_data(
        identity: EvmIdentity,
        domain: EIP712Domain,
        types: Dict[str, List[TypedDataField]],
        primary_type: str,
        message: Dict[str, Any],
    ) -> str:
        """
        Sign EIP-712 typed data.
        
        Args:
            identity: EVM identity with private key
            domain: EIP-712 domain separator
            types: Type definitions
            primary_type: The primary type being signed
            message: The message data
        
        Returns:
            65-byte signature as hex string (0x + r + s + v)
        """
        domain_hash = EIP712Signer.hash_domain(domain)
        message_hash = EIP712Signer.hash_struct(primary_type, types, message)
        
        to_sign = _keccak256(
            EIP712Signer.EIP712_DOMAIN_PREFIX.encode("utf-8") + domain_hash + message_hash
        )
        
        private_key = _private_key_from_hex(identity.private_key_hex)
        
        from cryptography.hazmat.primitives.asymmetric import ec, utils
        from cryptography.hazmat.primitives import hashes
        
        signature_der = private_key.sign(to_sign, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        r, s = utils.decode_dss_signature(signature_der)
        
        s = EIP712Signer._normalize_s(s)
        
        v = 27
        r_hex = f"{r:064x}"
        s_hex = f"{s:064x}"
        
        return f"0x{r_hex}{s_hex}{v:02x}"

    @staticmethod
    def _normalize_s(s: int) -> int:
        """Normalize s to lower half of curve order (EIP-2)"""
        SECP256K1_HALF_ORDER = 0x7FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF5D576E7357A4501DDFE92F46681B20A0
        if s > SECP256K1_HALF_ORDER:
            s = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141 - s
        return s

    @staticmethod
    def to_typed_data_json(
        domain: EIP712Domain,
        types: Dict[str, List[TypedDataField]],
        primary_type: str,
        message: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Convert to EIP-712 JSON format for compatibility with other tools.
        
        Returns format compatible with eth_signTypedData_v4
        """
        types_json = {
            name: [f.to_dict() for f in fields]
            for name, fields in types.items()
        }
        if "EIP712Domain" not in types_json:
            types_json["EIP712Domain"] = [f.to_dict() for f in domain.fields]
        
        return {
            "types": types_json,
            "primaryType": primary_type,
            "domain": domain.to_dict(),
            "message": message,
        }


def sign_transfer_with_authorization(
    identity: EvmIdentity,
    usdc_address: str,
    chain_id: int,
    from_address: str,
    to_address: str,
    value: int,
    valid_after: int,
    valid_before: int,
    nonce: Optional[bytes] = None,
) -> Dict[str, Any]:
    """
    Sign USDC TransferWithAuthorization (EIP-3009) for x402 payments.
    
    Args:
        identity: EVM identity with private key
        usdc_address: USDC contract address
        chain_id: Chain ID (e.g., 8453 for Base)
        from_address: Sender address
        to_address: Recipient address
        value: Amount in atomic units (6 decimals for USDC)
        valid_after: Unix timestamp when transfer becomes valid
        valid_before: Unix timestamp when transfer expires
        nonce: 32-byte nonce (generated if not provided)
    
    Returns:
        Dict with signature and authorization data for x402 payment header
    """
    if nonce is None:
        nonce = secrets.token_bytes(32)
    elif isinstance(nonce, str):
        if nonce.startswith("0x"):
            nonce = bytes.fromhex(nonce[2:])
        else:
            nonce = bytes.fromhex(nonce)
    
    domain = EIP712Domain(
        name="USD Coin",
        version="2",
        chain_id=chain_id,
        verifying_contract=usdc_address,
    )
    
    types = {
        "TransferWithAuthorization": [
            TypedDataField("from", "address"),
            TypedDataField("to", "address"),
            TypedDataField("value", "uint256"),
            TypedDataField("validAfter", "uint256"),
            TypedDataField("validBefore", "uint256"),
            TypedDataField("nonce", "bytes32"),
        ]
    }
    
    message = {
        "from": from_address,
        "to": to_address,
        "value": value,
        "validAfter": valid_after,
        "validBefore": valid_before,
        "nonce": "0x" + nonce.hex(),
    }
    
    signature = EIP712Signer.sign_typed_data(
        identity, domain, types, "TransferWithAuthorization", message
    )
    
    return {
        "signature": signature,
        "authorization": {
            "from": from_address,
            "to": to_address,
            "value": str(value),
            "validAfter": str(valid_after),
            "validBefore": str(valid_before),
            "nonce": "0x" + nonce.hex(),
        },
    }


def sign_permit(
    identity: EvmIdentity,
    token_address: str,
    token_name: str,
    chain_id: int,
    owner_address: str,
    spender_address: str,
    value: int,
    deadline: int,
    nonce: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Sign EIP-2612 Permit for USDT and other tokens.
    
    USDT on many chains uses EIP-2612 Permit instead of EIP-3009.
    
    Args:
        identity: EVM identity with private key
        token_address: Token contract address
        token_name: Token name for domain (e.g., "Tether USD")
        chain_id: Chain ID
        owner_address: Token owner address
        spender_address: Address to approve spending
        value: Amount to approve in atomic units
        deadline: Unix timestamp when permit expires
        nonce: Contract nonce (will be queried if not provided)
    
    Returns:
        Dict with signature and permit data for x402 payment header
    """
    domain = EIP712Domain(
        name=token_name,
        version="1",
        chain_id=chain_id,
        verifying_contract=token_address,
    )
    
    types = {
        "Permit": [
            TypedDataField("owner", "address"),
            TypedDataField("spender", "address"),
            TypedDataField("value", "uint256"),
            TypedDataField("nonce", "uint256"),
            TypedDataField("deadline", "uint256"),
        ]
    }
    
    if nonce is None:
        nonce = 0
    
    message = {
        "owner": owner_address,
        "spender": spender_address,
        "value": value,
        "nonce": nonce,
        "deadline": deadline,
    }
    
    signature = EIP712Signer.sign_typed_data(
        identity, domain, types, "Permit", message
    )
    
    sig_hex = signature[2:] if signature.startswith("0x") else signature
    r = "0x" + sig_hex[:64]
    s = "0x" + sig_hex[64:128]
    v = int(sig_hex[128:130], 16)
    
    return {
        "signature": signature,
        "permit": {
            "owner": owner_address,
            "spender": spender_address,
            "value": str(value),
            "deadline": str(deadline),
            "v": v,
            "r": r,
            "s": s,
        },
    }


def sign_usdt_permit(
    identity: EvmIdentity,
    usdt_address: str,
    chain_id: int,
    owner_address: str,
    spender_address: str,
    value: int,
    deadline: int,
    nonce: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Sign EIP-2612 Permit specifically for USDT.
    
    Convenience wrapper for sign_permit with USDT defaults.
    """
    return sign_permit(
        identity=identity,
        token_address=usdt_address,
        token_name="Tether USD",
        chain_id=chain_id,
        owner_address=owner_address,
        spender_address=spender_address,
        value=value,
        deadline=deadline,
        nonce=nonce,
    )


def sign_token_payment(
    identity: EvmIdentity,
    token_address: str,
    token_symbol: str,
    chain_id: int,
    from_address: str,
    to_address: str,
    value: int,
    valid_after: int,
    valid_before: int,
    nonce: Optional[bytes] = None,
) -> Dict[str, Any]:
    """
    Sign payment authorization for USDC or USDT.
    
    Automatically selects the correct signing method:
    - USDC: EIP-3009 TransferWithAuthorization
    - USDT: EIP-2612 Permit
    
    Args:
        identity: EVM identity
        token_address: Token contract address
        token_symbol: "USDC" or "USDT"
        chain_id: Chain ID
        from_address: Sender address
        to_address: Recipient address
        value: Amount in atomic units
        valid_after: Unix timestamp when valid
        valid_before: Unix timestamp when expires
        nonce: Optional nonce
    
    Returns:
        Dict with signature and authorization data
    """
    token_upper = token_symbol.upper()
    
    if token_upper == "USDC":
        return sign_transfer_with_authorization(
            identity=identity,
            usdc_address=token_address,
            chain_id=chain_id,
            from_address=from_address,
            to_address=to_address,
            value=value,
            valid_after=valid_after,
            valid_before=valid_before,
            nonce=nonce,
        )
    
    if token_upper == "USDT":
        deadline = valid_before
        return sign_usdt_permit(
            identity=identity,
            usdt_address=token_address,
            chain_id=chain_id,
            owner_address=from_address,
            spender_address=to_address,
            value=value,
            deadline=deadline,
        )
    
    raise ValueError(f"Unsupported token: {token_symbol}")


def sign_receive_with_authorization(
    identity: EvmIdentity,
    usdc_address: str,
    chain_id: int,
    from_address: str,
    to_address: str,
    value: int,
    valid_after: int,
    valid_before: int,
    nonce: Optional[bytes] = None,
) -> Dict[str, Any]:
    """
    Sign USDC ReceiveWithAuthorization (EIP-3009) for receiving payments.
    
    Similar to TransferWithAuthorization but allows the recipient to pull funds.
    """
    if nonce is None:
        nonce = secrets.token_bytes(32)
    elif isinstance(nonce, str):
        if nonce.startswith("0x"):
            nonce = bytes.fromhex(nonce[2:])
        else:
            nonce = bytes.fromhex(nonce)
    
    domain = EIP712Domain(
        name="USD Coin",
        version="2",
        chain_id=chain_id,
        verifying_contract=usdc_address,
    )
    
    types = {
        "ReceiveWithAuthorization": [
            TypedDataField("from", "address"),
            TypedDataField("to", "address"),
            TypedDataField("value", "uint256"),
            TypedDataField("validAfter", "uint256"),
            TypedDataField("validBefore", "uint256"),
            TypedDataField("nonce", "bytes32"),
        ]
    }
    
    message = {
        "from": from_address,
        "to": to_address,
        "value": value,
        "validAfter": valid_after,
        "validBefore": valid_before,
        "nonce": "0x" + nonce.hex(),
    }
    
    signature = EIP712Signer.sign_typed_data(
        identity, domain, types, "ReceiveWithAuthorization", message
    )
    
    return {
        "signature": signature,
        "authorization": {
            "from": from_address,
            "to": to_address,
            "value": str(value),
            "validAfter": str(valid_after),
            "validBefore": str(valid_before),
            "nonce": "0x" + nonce.hex(),
        },
    }
