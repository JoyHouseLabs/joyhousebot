"""Channel plugin types and interfaces."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from joyhousebot.bus.events import OutboundMessage
    from joyhousebot.channels.run_adapter import RunAdapter


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

    Each channel (Telegram, Discord, etc.) integrates with the durable runtime
    through a RunAdapter. Transport plugins never own or consume an execution
    queue.
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

    def configure(self, config: dict[str, Any], run_adapter: "RunAdapter") -> None:
        """
        Configure the channel with settings and durable run adapter.

        Called before start() to set up the channel.

        Args:
            config: Channel configuration as dict.
            run_adapter: Adapter that submits inbound messages as durable runs.
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

    @property
    def is_running(self) -> bool:
        """Check if the channel is running."""
        return False
