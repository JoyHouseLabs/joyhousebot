"""Base class for channel plugins with common utilities."""

from __future__ import annotations

from abc import abstractmethod
from datetime import datetime
from typing import TYPE_CHECKING, Any

from loguru import logger

from joyhousebot.channels.plugins.types import (
    ChannelCapabilities,
    ChannelMeta,
    ChannelPlugin,
    ChannelStatus,
    SendResult,
)
from joyhousebot.bus.events import InboundMessage

if TYPE_CHECKING:
    from joyhousebot.bus.events import OutboundMessage
    from joyhousebot.bus.queue import MessageBus


class BaseChannelPlugin(ChannelPlugin):
    """
    Base class for channel plugins with common functionality.
    
    Provides:
    - Message bus integration
    - Permission checking
    - Typing indicator support
    - Logging utilities
    - Status tracking
    """
    
    def __init__(self) -> None:
        self._bus: MessageBus | None = None
        self._config: dict[str, Any] = {}
        self._running = False
        self._connected = False
        self._last_error: str | None = None
        self._last_message_at: datetime | None = None
    
    @property
    @abstractmethod
    def id(self) -> str:
        pass
    
    @property
    @abstractmethod
    def meta(self) -> ChannelMeta:
        pass
    
    @property
    @abstractmethod
    def capabilities(self) -> ChannelCapabilities:
        pass
    
    def configure(self, config: dict[str, Any], bus: "MessageBus") -> None:
        """Configure the channel with settings and message bus."""
        self._config = config or {}
        self._bus = bus
    
    @abstractmethod
    async def start(self) -> None:
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        pass
    
    @abstractmethod
    async def send(self, msg: "OutboundMessage") -> SendResult:
        pass
    
    @property
    def is_running(self) -> bool:
        return self._running
    
    @property
    def is_connected(self) -> bool:
        return self._connected
    
    def get_status(self) -> ChannelStatus:
        return ChannelStatus(
            running=self._running,
            connected=self._connected,
            last_error=self._last_error,
            last_message_at=self._last_message_at.isoformat() if self._last_message_at else None,
        )
    
    async def _publish_inbound(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """
        Publish an inbound message to the bus.
        
        Returns True if message was published, False if sender not allowed.
        """
        if not self._bus:
            logger.error(f"[{self.id}] Message bus not initialized")
            return False
        
        if not self.is_allowed(sender_id, self._config):
            logger.warning(
                f"[{self.id}] Access denied for sender {sender_id}. "
                f"Add to allow_from list in config."
            )
            return False
        
        msg = InboundMessage(
            channel=self.id,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=content,
            media=media or [],
            metadata=metadata or {},
        )
        
        await self._bus.publish_inbound(msg)
        self._last_message_at = datetime.now()
        return True
    
    def _log_start(self) -> None:
        """Log channel start."""
        logger.info(f"[{self.id}] Starting {self.meta.display_name} channel...")
    
    def _log_started(self) -> None:
        """Log channel started."""
        logger.info(f"[{self.id}] {self.meta.display_name} channel started")
    
    def _log_stop(self) -> None:
        """Log channel stop."""
        logger.info(f"[{self.id}] Stopping {self.meta.display_name} channel...")
    
    def _log_stopped(self) -> None:
        """Log channel stopped."""
        logger.info(f"[{self.id}] {self.meta.display_name} channel stopped")
    
    def _log_error(self, message: str, error: Exception | None = None) -> None:
        """Log an error."""
        self._last_error = message
        if error:
            logger.error(f"[{self.id}] {message}: {error}")
        else:
            logger.error(f"[{self.id}] {message}")
    
    def _set_running(self, running: bool) -> None:
        """Set running state."""
        self._running = running
    
    def _set_connected(self, connected: bool) -> None:
        """Set connected state."""
        self._connected = connected
        if connected:
            self._last_error = None
    
    @staticmethod
    def is_allowed(sender_id: str, config: dict[str, Any]) -> bool:
        """
        Check if sender is allowed to send messages.
        
        Args:
            sender_id: The sender's ID
            config: Channel configuration dict
            
        Returns:
            True if sender is allowed, False otherwise
        """
        allow_from = config.get("allow_from", [])
        if not allow_from:
            return True
        return str(sender_id) in [str(a) for a in allow_from]
