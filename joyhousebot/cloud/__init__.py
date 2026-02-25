"""云端连接模块"""

from joyhousebot.cloud.auth import CloudAuth, get_challenge
from joyhousebot.cloud.capability import CapabilityCardGenerator
from joyhousebot.cloud.client import CloudClient, create_cloud_client
from joyhousebot.cloud.protocol import (
    MessageType,
    BaseMessage,
    AuthMessage,
    AuthChallengeMessage,
    AuthSuccessMessage,
    AuthFailMessage,
    HeartbeatMessage,
    TaskAckMessage,
    TaskProgressMessage,
    TaskCompleteMessage,
    TaskFailMessage,
    TaskAssignMessage,
    TaskCancelMessage,
    CapabilityCardMessage,
    StatusUpdateMessage,
    ErrorMessage,
    PingMessage,
    PongMessage,
)

__all__ = [
    "CloudAuth",
    "get_challenge",
    "CapabilityCardGenerator",
    "CloudClient",
    "create_cloud_client",
    "MessageType",
    "BaseMessage",
    "AuthMessage",
    "AuthChallengeMessage",
    "AuthSuccessMessage",
    "AuthFailMessage",
    "HeartbeatMessage",
    "TaskAckMessage",
    "TaskProgressMessage",
    "TaskCompleteMessage",
    "TaskFailMessage",
    "TaskAssignMessage",
    "TaskCancelMessage",
    "CapabilityCardMessage",
    "StatusUpdateMessage",
    "ErrorMessage",
    "PingMessage",
    "PongMessage",
]
