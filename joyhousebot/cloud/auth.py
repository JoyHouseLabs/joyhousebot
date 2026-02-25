"""云端连接身份认证"""

from __future__ import annotations

import secrets
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from joyhousebot.identity.ed25519 import (
    ensure_bot_identity,
    sign_challenge,
    PUBLIC_KEY_HEX_LEN,
    SIGNATURE_HEX_LEN,
)


class CloudAuth:
    """云端连接认证管理器"""

    def __init__(self, key_path: Path):
        self.key_path = key_path
        self.identity = ensure_bot_identity(key_path)
        self._access_token: str | None = None
        self._house_id: str | None = None

    @property
    def public_key_hex(self) -> str:
        """获取公钥十六进制字符串"""
        return self.identity.public_key_hex

    @property
    def private_key_hex(self) -> str:
        """获取私钥十六进制字符串"""
        return self.identity.private_key_hex

    @property
    def access_token(self) -> str | None:
        """获取访问令牌"""
        return self._access_token

    @property
    def house_id(self) -> str | None:
        """获取分配的 house_id"""
        return self._house_id

    def sign_challenge(self, challenge: str) -> str:
        """签名挑战"""
        return sign_challenge(self.private_key_hex, challenge)

    def verify_public_key(self, public_key_hex: str, signature_hex: str, message: str) -> bool:
        """验证签名"""
        try:
            if len(public_key_hex) != PUBLIC_KEY_HEX_LEN:
                return False
            if len(signature_hex) != SIGNATURE_HEX_LEN:
                return False

            public_key_bytes = bytes.fromhex(public_key_hex)
            signature_bytes = bytes.fromhex(signature_hex)
            message_bytes = message.encode("utf-8")

            public_key = ed25519.Ed25519PublicKey.from_public_bytes(public_key_bytes)
            public_key.verify(signature_bytes, message_bytes)
            return True
        except Exception:
            return False

    def set_credentials(self, house_id: str, access_token: str) -> None:
        """设置认证凭证"""
        self._house_id = house_id
        self._access_token = access_token

    def clear_credentials(self) -> None:
        """清除认证凭证"""
        self._house_id = None
        self._access_token = None

    def is_authenticated(self) -> bool:
        """检查是否已认证"""
        return self._access_token is not None and self._house_id is not None


def get_challenge() -> str:
    """生成随机挑战字符串"""
    return f"challenge_{secrets.token_hex(32)}"
