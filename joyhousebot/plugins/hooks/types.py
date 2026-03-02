"""Plugin hook types - event definitions and handler signatures.

Aligned with OpenClaw's hook system for compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Union


class HookName(str, Enum):
    BEFORE_AGENT_START = "before_agent_start"
    AGENT_END = "agent_end"
    BEFORE_COMPACTION = "before_compaction"
    AFTER_COMPACTION = "after_compaction"
    BEFORE_RESET = "before_reset"
    MESSAGE_RECEIVED = "message_received"
    MESSAGE_SENDING = "message_sending"
    MESSAGE_SENT = "message_sent"
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    TOOL_RESULT_PERSIST = "tool_result_persist"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    GATEWAY_START = "gateway_start"
    GATEWAY_STOP = "gateway_stop"


@dataclass
class HookContext:
    agent_id: str | None = None
    session_id: str | None = None
    session_key: str | None = None
    workspace_dir: str | None = None
    channel: str | None = None
    account_id: str | None = None
    message_provider: str | None = None


@dataclass
class BeforeAgentStartEvent:
    prompt: str
    messages: list[Any] = field(default_factory=list)


@dataclass
class BeforeAgentStartResult:
    system_prompt: str | None = None
    prepend_context: str | None = None


@dataclass
class AgentEndEvent:
    messages: list[Any] = field(default_factory=list)
    success: bool = True
    error: str | None = None
    duration_ms: float = 0


@dataclass
class BeforeCompactionEvent:
    message_count: int = 0
    compacting_count: int | None = None
    token_count: int | None = None
    messages: list[Any] = field(default_factory=list)
    session_file: str | None = None


@dataclass
class AfterCompactionEvent:
    message_count: int = 0
    token_count: int | None = None
    compacted_count: int = 0
    session_file: str | None = None


@dataclass
class BeforeResetEvent:
    session_file: str | None = None
    messages: list[Any] = field(default_factory=list)
    reason: str | None = None


@dataclass
class MessageContext:
    channel_id: str | None = None
    account_id: str | None = None
    conversation_id: str | None = None


@dataclass
class MessageReceivedEvent:
    from_id: str
    content: str
    timestamp: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MessageSendingEvent:
    to_id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class MessageSendingResult:
    content: str | None = None
    cancel: bool = False


@dataclass
class MessageSentEvent:
    to_id: str
    content: str
    success: bool = True
    error: str | None = None


@dataclass
class ToolContext:
    agent_id: str | None = None
    session_key: str | None = None
    tool_name: str = ""


@dataclass
class BeforeToolCallEvent:
    tool_name: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class BeforeToolCallResult:
    params: dict[str, Any] | None = None
    block: bool = False
    block_reason: str | None = None


@dataclass
class AfterToolCallEvent:
    tool_name: str
    params: dict[str, Any] = field(default_factory=dict)
    result: Any | None = None
    error: str | None = None
    duration_ms: float = 0


@dataclass
class ToolResultPersistContext:
    agent_id: str | None = None
    session_key: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None


@dataclass
class ToolResultPersistEvent:
    tool_name: str | None = None
    tool_call_id: str | None = None
    message: dict[str, Any] = field(default_factory=dict)
    is_synthetic: bool = False


@dataclass
class ToolResultPersistResult:
    message: dict[str, Any] | None = None


@dataclass
class SessionContext:
    agent_id: str | None = None
    session_id: str = ""


@dataclass
class SessionStartEvent:
    session_id: str = ""
    resumed_from: str | None = None


@dataclass
class SessionEndEvent:
    session_id: str = ""
    message_count: int = 0
    duration_ms: float = 0


@dataclass
class GatewayContext:
    port: int | None = None


@dataclass
class GatewayStartEvent:
    port: int = 0


@dataclass
class GatewayStopEvent:
    reason: str | None = None


BeforeAgentStartHandler = Callable[
    [BeforeAgentStartEvent, HookContext],
    Union[BeforeAgentStartResult, None]
]
AgentEndHandler = Callable[[AgentEndEvent, HookContext], None]
BeforeCompactionHandler = Callable[[BeforeCompactionEvent, HookContext], None]
AfterCompactionHandler = Callable[[AfterCompactionEvent, HookContext], None]
BeforeResetHandler = Callable[[BeforeResetEvent, HookContext], None]
MessageReceivedHandler = Callable[[MessageReceivedEvent, MessageContext], None]
MessageSendingHandler = Callable[
    [MessageSendingEvent, MessageContext],
    Union[MessageSendingResult, None]
]
MessageSentHandler = Callable[[MessageSentEvent, MessageContext], None]
BeforeToolCallHandler = Callable[
    [BeforeToolCallEvent, ToolContext],
    Union[BeforeToolCallResult, None]
]
AfterToolCallHandler = Callable[[AfterToolCallEvent, ToolContext], None]
ToolResultPersistHandler = Callable[
    [ToolResultPersistEvent, ToolResultPersistContext],
    Union[ToolResultPersistResult, None]
]
SessionStartHandler = Callable[[SessionStartEvent, SessionContext], None]
SessionEndHandler = Callable[[SessionEndEvent, SessionContext], None]
GatewayStartHandler = Callable[[GatewayStartEvent, GatewayContext], None]
GatewayStopHandler = Callable[[GatewayStopEvent, GatewayContext], None]

HookHandler = Union[
    BeforeAgentStartHandler,
    AgentEndHandler,
    BeforeCompactionHandler,
    AfterCompactionHandler,
    BeforeResetHandler,
    MessageReceivedHandler,
    MessageSendingHandler,
    MessageSentHandler,
    BeforeToolCallHandler,
    AfterToolCallHandler,
    ToolResultPersistHandler,
    SessionStartHandler,
    SessionEndHandler,
    GatewayStartHandler,
    GatewayStopHandler,
    Callable[[Any, Any], Any],
]


@dataclass
class PluginHookRegistration:
    plugin_id: str
    hook_name: str
    handler: HookHandler
    priority: int = 0
    source: str = ""
