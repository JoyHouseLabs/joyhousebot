"""云端连接 WebSocket 客户端"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
from pathlib import Path
from typing import Any, Callable

import websockets
from websockets.client import WebSocketClientProtocol

from joyhousebot.cloud.auth import CloudAuth, get_challenge
from joyhousebot.cloud.capability import CapabilityCardGenerator
from joyhousebot.cloud.protocol import (
    BaseMessage,
    MessageType,
    AuthMessage,
    HeartbeatMessage,
    TaskAckMessage,
    TaskProgressMessage,
    TaskCompleteMessage,
    TaskFailMessage,
    CapabilityCardMessage,
    StatusUpdateMessage,
    PongMessage,
    ErrorMessage,
    TaskAssignMessage,
)

logger = logging.getLogger(__name__)


class CloudClient:
    """云端连接 WebSocket 客户端"""

    def __init__(
        self,
        config,
        auth: CloudAuth,
        capability_generator: CapabilityCardGenerator,
    ):
        self.config = config
        self.auth = auth
        self.capability_generator = capability_generator

        self._ws: WebSocketClientProtocol | None = None
        self._connected = False
        self._authenticated = False
        self._running = False
        self._challenge: str | None = None
        self._pending_tasks: dict[str, asyncio.Task] = {}

        self._on_task_assign: Callable[[TaskAssignMessage], None] | None = None
        self._on_task_cancel: Callable[[str], None] | None = None

    @property
    def connected(self) -> bool:
        """是否已连接"""
        return self._connected

    @property
    def authenticated(self) -> bool:
        """是否已认证"""
        return self._authenticated

    def set_task_assign_callback(self, callback: Callable[[TaskAssignMessage], None]) -> None:
        """设置任务分配回调"""
        self._on_task_assign = callback

    def set_task_cancel_callback(self, callback: Callable[[str], None]) -> None:
        """设置任务取消回调"""
        self._on_task_cancel = callback

    async def connect(self) -> None:
        """连接到云端"""
        if self._running:
            logger.warning("Cloud client is already running")
            return

        self._running = True
        self._authenticated = False

        try:
            await self._connect_loop()
        except Exception as e:
            logger.error(f"Cloud client error: {e}")
        finally:
            self._running = False
            self._connected = False
            self._authenticated = False

    async def _connect_loop(self) -> None:
        """连接循环，支持自动重连"""
        while self._running:
            try:
                logger.info(f"Connecting to cloud: {self.config.backend_url}")
                async with websockets.connect(
                    self.config.backend_url,
                    ping_interval=20,
                    ping_timeout=10,
                ) as ws:
                    self._ws = ws
                    self._connected = True
                    logger.info("Connected to cloud")

                    await self._handle_connection(ws)

            except Exception as e:
                logger.error(f"Connection error: {e}")
                self._connected = False
                self._authenticated = False

            if not self.config.auto_reconnect or not self._running:
                break

            logger.info(f"Reconnecting in {self.config.reconnect_interval} seconds...")
            await asyncio.sleep(self.config.reconnect_interval)

    async def _handle_connection(self, ws: WebSocketClientProtocol) -> None:
        """处理连接和消息"""
        try:
            async for message in ws:
                await self._handle_message(message)
        except Exception as e:
            logger.error(f"Message handling error: {e}")
            raise

    async def _handle_message(self, message: str) -> None:
        """处理接收到的消息"""
        try:
            data = json.loads(message)
            msg_type = MessageType(data.get("type"))

            if msg_type == MessageType.AUTH_CHALLENGE:
                await self._handle_auth_challenge(data)
            elif msg_type == MessageType.AUTH_SUCCESS:
                await self._handle_auth_success(data)
            elif msg_type == MessageType.AUTH_FAIL:
                await self._handle_auth_fail(data)
            elif msg_type == MessageType.TASK_ASSIGN:
                await self._handle_task_assign(data)
            elif msg_type == MessageType.TASK_CANCEL:
                await self._handle_task_cancel(data)
            elif msg_type == MessageType.PING:
                await self._handle_ping(data)
            elif msg_type == MessageType.ERROR:
                await self._handle_error(data)
            else:
                logger.warning(f"Unknown message type: {msg_type}")

        except Exception as e:
            logger.error(f"Failed to handle message: {e}, data: {message}")

    async def _handle_auth_challenge(self, data: dict[str, Any]) -> None:
        """处理认证挑战"""
        try:
            self._challenge = data["challenge"]
            signature = self.auth.sign_challenge(self._challenge)

            auth_msg = AuthMessage(
                public_key=self.auth.public_key_hex,
                house_name=self.config.house_name,
                description=self.config.description,
                challenge=self._challenge,
                signature=signature,
            )

            await self.send_message(auth_msg.model_dump(by_alias=True))
            logger.info("Auth request sent")

        except Exception as e:
            logger.error(f"Failed to handle auth challenge: {e}")

    async def _handle_auth_success(self, data: dict[str, Any]) -> None:
        """处理认证成功"""
        self.auth.set_credentials(data["house_id"], data["access_token"])
        self._authenticated = True
        logger.info(f"Authenticated successfully, house_id: {data['house_id']}")

        await self.send_capability_card()
        await self.send_status_update()

    async def _handle_auth_fail(self, data: dict[str, Any]) -> None:
        """处理认证失败"""
        self._authenticated = False
        reason = data.get("reason", "Unknown reason")
        logger.error(f"Authentication failed: {reason}")

    async def _handle_task_assign(self, data: dict[str, Any]) -> None:
        """处理任务分配"""
        try:
            msg = TaskAssignMessage(**data)
            logger.info(f"Task assigned: {msg.task_id}")

            await self.send_task_ack(msg.task_id, True)

            if self._on_task_assign:
                self._on_task_assign(msg)

        except Exception as e:
            logger.error(f"Failed to handle task assign: {e}")
            if msg := data.get("task_id"):
                await self.send_task_ack(msg, False)

    async def _handle_task_cancel(self, data: dict[str, Any]) -> None:
        """处理任务取消"""
        task_id = data.get("task_id")
        if task_id and self._on_task_cancel:
            logger.info(f"Task cancelled: {task_id}")
            self._on_task_cancel(task_id)

    async def _handle_ping(self, data: dict[str, Any]) -> None:
        """处理 ping"""
        msg_id = data.get("msg_id")
        pong = PongMessage(ping_msg_id=msg_id)
        await self.send_message(pong.model_dump(by_alias=True))

    async def _handle_error(self, data: dict[str, Any]) -> None:
        """处理错误消息"""
        code = data.get("code")
        message = data.get("message")
        logger.error(f"Error from server: {code} - {message}")

    async def send_message(self, message: dict[str, Any]) -> None:
        """发送消息"""
        if not self._ws:
            logger.warning("Not connected, cannot send message")
            return

        try:
            await self._ws.send(json.dumps(message, ensure_ascii=False))
        except Exception as e:
            logger.error(f"Failed to send message: {e}")

    async def send_capability_card(self) -> None:
        """发送能力介绍卡"""
        card = self.capability_generator.generate_full_schema()
        msg = CapabilityCardMessage(card=card)
        await self.send_message(msg.model_dump(by_alias=True))
        logger.info("Capability card sent")

    async def send_status_update(self) -> None:
        """发送状态更新"""
        msg = StatusUpdateMessage(
            status="online",
            capabilities=[cap.id for cap in self.config.capabilities if cap.enabled],
        )
        await self.send_message(msg.model_dump(by_alias=True))

    async def send_heartbeat(self) -> None:
        """发送心跳"""
        msg = HeartbeatMessage(
            status="online",
            metrics={
                "platform": platform.system(),
                "python_version": platform.python_version(),
            },
        )
        await self.send_message(msg.model_dump(by_alias=True))

    async def send_task_ack(self, task_id: str, accepted: bool) -> None:
        """发送任务确认"""
        msg = TaskAckMessage(task_id=task_id, accepted=accepted)
        await self.send_message(msg.model_dump(by_alias=True))

    async def send_task_progress(self, task_id: str, progress: float, detail: str | None = None) -> None:
        """发送任务进度"""
        msg = TaskProgressMessage(task_id=task_id, progress=progress, detail=detail)
        await self.send_message(msg.model_dump(by_alias=True))

    async def send_task_complete(self, task_id: str, result: dict[str, Any]) -> None:
        """发送任务完成"""
        msg = TaskCompleteMessage(task_id=task_id, result=result)
        await self.send_message(msg.model_dump(by_alias=True))

    async def send_task_fail(self, task_id: str, error: dict[str, Any]) -> None:
        """发送任务失败"""
        msg = TaskFailMessage(task_id=task_id, error=error)
        await self.send_message(msg.model_dump(by_alias=True))

    async def start_heartbeat(self, interval: int = 30) -> None:
        """启动心跳循环"""
        while self._running and self._authenticated:
            try:
                await self.send_heartbeat()
                await asyncio.sleep(interval)
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                break

    async def disconnect(self) -> None:
        """断开连接"""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        self._connected = False
        self._authenticated = False
        logger.info("Disconnected from cloud")


async def create_cloud_client(
    config,
    key_path: Path,
) -> CloudClient:
    """创建云端连接客户端"""
    auth = CloudAuth(key_path)
    capability_generator = CapabilityCardGenerator(config.cloud_connect, auth)
    return CloudClient(config.cloud_connect, auth, capability_generator)
