"""Channel plugin types and interfaces."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, TYPE_CHECKING

if TYPE_CHECKING:
    from joyhousebot.bus.events import InboundMessage, OutboundMessage
    from joyhousebot.bus.queue import MessageBus


class ChatType(str, Enum):
    """Supported chat types."""
    DIRECT = "direct"
    GROUP = "group"
    CHANNEL = "channel"
    THREAD = "thread"


@dataclass
class ChannelCapabilities:
    """Describes what a channel supports."""
    chat_types: list[ChatType] = field(default_factory=lambda: [ChatType.DIRECT])
    supports_media: bool = False
    supports_reactions: bool = False
    supports_threads: bool = False
    supports_typing: bool = False
    supports_polls: bool = False
    supports_edit: bool = False
    supports_delete: bool = False
    text_chunk_limit: int = 4096
    streaming: bool = False


@dataclass
class ChannelMeta:
    """Channel metadata."""
    display_name: str
    description: str = ""
    icon: str = ""
    order: int = 100


@dataclass
class ChannelConfigSpec:
    """Specification for a channel configuration field."""
    name: str
    type: str  # "string", "number", "boolean", "array"
    required: bool = False
    secret: bool = False
    default: Any = None
    description: str = ""
    placeholder: str = ""


@dataclass
class SendResult:
    """Result of sending a message."""
    success: bool
    message_id: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChannelStatus:
    """Runtime status of a channel."""
    running: bool = False
    connected: bool = False
    last_error: str | None = None
    last_message_at: str | None = None
    account_info: dict[str, Any] = field(default_factory=dict)


class ChannelPlugin(ABC):
    """
    Abstract base class for channel plugins.
    
    Each channel (Telegram, Discord, etc.) should implement this interface
    to integrate with the joyhousebot message bus.
    """
    
    @property
    @abstractmethod
    def id(self) -> str:
        """Unique channel identifier (e.g., 'telegram', 'discord')."""
        pass
    
    @property
    @abstractmethod
    def meta(self) -> ChannelMeta:
        """Channel metadata."""
        pass
    
    @property
    @abstractmethod
    def capabilities(self) -> ChannelCapabilities:
        """Channel capabilities."""
        pass
    
    @property
    def config_schema(self) -> list[ChannelConfigSpec]:
        """Configuration schema for this channel."""
        return []
    
    def create_config_model(self, raw_config: dict[str, Any]) -> Any:
        """
        Create a typed config object from raw dict.
        Override this to provide custom config validation.
        """
        return raw_config
    
    def configure(self, config: dict[str, Any], bus: "MessageBus") -> None:
        """
        Configure the channel with settings and message bus.
        
        Called before start() to set up the channel.
        
        Args:
            config: Channel configuration as dict.
            bus: The message bus for publishing inbound messages.
        """
        pass
    
    @abstractmethod
    async def start(self) -> None:
        """
        Start the channel and begin listening for messages.
        
        configure() must be called before start().
        """
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel and clean up resources."""
        pass
    
    @abstractmethod
    async def send(self, msg: "OutboundMessage") -> SendResult:
        """
        Send a message through this channel.
        
        Args:
            msg: The message to send.
            
        Returns:
            SendResult with success status and metadata.
        """
        pass
    
    async def send_typing(self, chat_id: str) -> None:
        """Send typing indicator (if supported)."""
        pass
    
    async def send_reaction(self, chat_id: str, message_id: str, emoji: str) -> None:
        """Send a reaction to a message (if supported)."""
        pass
    
    def is_allowed(self, sender_id: str, config: Any) -> bool:
        """
        Check if a sender is allowed to use this bot.
        
        Default implementation checks allow_from list.
        """
        allow_list = getattr(config, "allow_from", []) or []
        if not allow_list:
            return True
        
        sender_str = str(sender_id)
        if sender_str in allow_list:
            return True
        if "|" in sender_str:
            for part in sender_str.split("|"):
                if part and part in allow_list:
                    return True
        return False
    
    def get_status(self) -> ChannelStatus:
        """Get current channel status."""
        return ChannelStatus()
    
    def normalize_target(self, target: str) -> str:
        """Normalize a target identifier (e.g., strip protocol prefix)."""
        return target
    
    @property
    def is_running(self) -> bool:
        """Check if the channel is running."""
        return False


type ChannelPluginFactory = Callable[[], ChannelPlugin]
