"""
Wallet Session Manager

Manages wallet unlock state during a session.
If password is provided at startup, wallet is unlocked and can be used for payments.
If no password, wallet remains locked and payment features are disabled.

Security: Private key is held in memory only during the session and cleared on exit.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Optional

from joyhousebot.identity.evm import EvmIdentity
from joyhousebot.identity.wallet_store import (
    get_wallet_address,
    decrypt_wallet,
    wallet_file_exists,
)

logger = logging.getLogger(__name__)


@dataclass
class UnlockedWallet:
    """Represents an unlocked wallet ready for signing."""
    identity: EvmIdentity
    address: str
    
    def is_valid(self) -> bool:
        return self.identity is not None and self.address is not None


class WalletSession:
    """
    Singleton wallet session manager.
    
    Manages wallet unlock state for the current session.
    Thread-safe implementation.
    
    Usage:
        # At startup with password:
        session = WalletSession.get_instance()
        if session.unlock(password):
            print(f"Wallet unlocked: {session.address}")
        
        # Check if unlocked:
        if session.is_unlocked:
            identity = session.get_identity()
            # Use identity for signing...
        
        # Lock on exit:
        session.lock()
    """
    
    _instance: Optional['WalletSession'] = None
    _lock = threading.Lock()
    
    def __new__(cls) -> 'WalletSession':
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._private_key: Optional[str] = None
        self._identity: Optional[EvmIdentity] = None
        self._address: Optional[str] = None
        self._session_lock = threading.Lock()
    
    @classmethod
    def get_instance(cls) -> 'WalletSession':
        """Get singleton instance."""
        return cls()
    
    @property
    def is_unlocked(self) -> bool:
        """Check if wallet is unlocked and ready for use."""
        with self._session_lock:
            return self._identity is not None and self._private_key is not None
    
    @property
    def address(self) -> Optional[str]:
        """Get wallet address if unlocked, None otherwise."""
        with self._session_lock:
            return self._address
    
    @property
    def has_wallet(self) -> bool:
        """Check if a wallet exists (regardless of unlock state)."""
        return wallet_file_exists()
    
    def unlock(self, password: str) -> bool:
        """
        Attempt to unlock wallet with password.
        
        Args:
            password: Wallet password
        
        Returns:
            True if unlock successful, False otherwise
        """
        with self._session_lock:
            try:
                if not self.has_wallet:
                    logger.warning("No wallet found to unlock")
                    return False
                
                address = get_wallet_address()
                if not address:
                    logger.warning("Could not get wallet address")
                    return False
                
                private_key = decrypt_wallet(password)
                
                self._identity = EvmIdentity(
                    private_key_hex=private_key,
                    address=address,
                )
                self._private_key = private_key
                self._address = address
                
                logger.info(f"Wallet unlocked: {address}")
                return True
                
            except ValueError as e:
                logger.warning(f"Failed to unlock wallet: {e}")
                self._clear_unlocked_state()
                return False
            except Exception as e:
                logger.error(f"Unexpected error unlocking wallet: {e}")
                self._clear_unlocked_state()
                return False
    
    def lock(self) -> None:
        """Lock the wallet, clearing all sensitive data from memory."""
        with self._session_lock:
            self._clear_unlocked_state()
            logger.info("Wallet locked")
    
    def _clear_unlocked_state(self) -> None:
        """Clear sensitive data from memory."""
        if self._private_key:
            secure_erase = ['0'] * len(self._private_key)
            self._private_key = ''.join(secure_erase)
        self._private_key = None
        self._identity = None
        self._address = None
    
    def get_identity(self) -> Optional[EvmIdentity]:
        """
        Get EVM identity for signing.
        
        Returns:
            EvmIdentity if unlocked, None otherwise
        """
        with self._session_lock:
            if self._identity is None:
                return None
            return EvmIdentity(
                private_key_hex=self._identity.private_key_hex,
                address=self._identity.address,
            )
    
    def get_status(self) -> dict:
        """Get wallet status info."""
        with self._session_lock:
            return {
                "has_wallet": self.has_wallet,
                "is_unlocked": self._identity is not None,
                "address": self._address,
            }


def get_wallet_session() -> WalletSession:
    """Get the global wallet session instance."""
    return WalletSession.get_instance()


def is_wallet_unlocked() -> bool:
    """Check if wallet is unlocked."""
    return get_wallet_session().is_unlocked


def get_unlocked_identity() -> Optional[EvmIdentity]:
    """Get unlocked identity if available."""
    return get_wallet_session().get_identity()


def get_unlocked_address() -> Optional[str]:
    """Get unlocked address if available."""
    return get_wallet_session().address
