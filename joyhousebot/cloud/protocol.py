"""WebSocket 消息协议定义"""

from __future__ import annotations

from enum import Enum
from typing import Any
from pydantic import BaseModel, Field


class MessageType(str, Enum):
    """消息类型枚举"""

    AUTH = "auth"
    AUTH_CHALLENGE = "auth.challenge"
    AUTH_SUCCESS = "auth.success"
    AUTH_FAIL = "auth.fail"
    HEARTBEAT = "heartbeat"
    TASK_ACK = "task.ack"
    TASK_PROGRESS = "task.progress"
    TASK_COMPLETE = "task.complete"
    TASK_FAIL = "task.fail"
    TASK_ASSIGN = "task.assign"
    TASK_CANCEL = "task.cancel"
    CAPABILITY_CARD = "capability.card"
    STATUS_UPDATE = "status.update"
    PING = "ping"
    PONG = "pong"
    ERROR = "error"


class BaseMessage(BaseModel):
    """基础消息"""
    msg_type: MessageType = Field(alias="type")
    msg_id: str = Field(default_factory=lambda: f"msg_{__import__('uuid').uuid4().hex[:12]}")
    timestamp: int = Field(default_factory=lambda: int(__import__('time').time() * 1000))


class AuthMessage(BaseMessage):
    """认证消息"""
    msg_type: MessageType = MessageType.AUTH
    public_key: str
    house_name: str
    description: str
    challenge: str
    signature: str


class AuthChallengeMessage(BaseMessage):
    """认证挑战消息"""
    msg_type: MessageType = MessageType.AUTH_CHALLENGE
    challenge: str


class AuthSuccessMessage(BaseMessage):
    """认证成功消息"""
    msg_type: MessageType = MessageType.AUTH_SUCCESS
    house_id: str
    access_token: str


class AuthFailMessage(BaseMessage):
    """认证失败消息"""
    msg_type: MessageType = MessageType.AUTH_FAIL
    reason: str


class HeartbeatMessage(BaseMessage):
    """心跳消息"""
    msg_type: MessageType = MessageType.HEARTBEAT
    status: str = "online"
    metrics: dict[str, Any] = Field(default_factory=dict)


class TaskAckMessage(BaseMessage):
    """任务确认消息"""
    msg_type: MessageType = MessageType.TASK_ACK
    task_id: str
    accepted: bool


class TaskProgressMessage(BaseMessage):
    """任务进度消息"""
    msg_type: MessageType = MessageType.TASK_PROGRESS
    task_id: str
    progress: float
    detail: str | None = None


class TaskCompleteMessage(BaseMessage):
    """任务完成消息"""
    msg_type: MessageType = MessageType.TASK_COMPLETE
    task_id: str
    result: dict[str, Any]


class TaskFailMessage(BaseMessage):
    """任务失败消息"""
    msg_type: MessageType = MessageType.TASK_FAIL
    task_id: str
    error: dict[str, Any]


class TaskAssignMessage(BaseMessage):
    """任务分配消息"""
    msg_type: MessageType = MessageType.TASK_ASSIGN
    task_id: str
    task_type: str
    task_version: str
    input: dict[str, Any]
    constraints: dict[str, Any] = Field(default_factory=dict)


class TaskCancelMessage(BaseMessage):
    """任务取消消息"""
    msg_type: MessageType = MessageType.TASK_CANCEL
    task_id: str


class CapabilityCardMessage(BaseMessage):
    """能力卡消息"""
    msg_type: MessageType = MessageType.CAPABILITY_CARD
    card: dict[str, Any]


class StatusUpdateMessage(BaseMessage):
    """状态更新消息"""
    msg_type: MessageType = MessageType.STATUS_UPDATE
    status: str
    capabilities: list[str]


class ErrorMessage(BaseMessage):
    """错误消息"""
    msg_type: MessageType = MessageType.ERROR
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class PingMessage(BaseMessage):
    """Ping 消息"""
    msg_type: MessageType = MessageType.PING


class PongMessage(BaseModel):
    """Pong 消息"""
    msg_type: MessageType = MessageType.PONG
    ping_msg_id: str
