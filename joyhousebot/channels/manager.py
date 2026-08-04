"""Distributed channel connector ownership and durable outbound delivery."""

from __future__ import annotations

import asyncio
import time
import uuid
import zlib
from typing import Any

from loguru import logger

from joyhousebot.bus.events import OutboundMessage
from joyhousebot.channels.plugins import ChannelPlugin, ChannelRegistry
from joyhousebot.channels.repository import ChannelRepository
from joyhousebot.channels.run_adapter import RunAdapter
from joyhousebot.config.schema import Config
from joyhousebot.utils.exceptions import classify_exception, sanitize_error_message


class ChannelManager:
    """Own enabled connectors through leases and deliver a fenced PG outbox."""

    LEASE_MS = 30_000
    OUTBOX_LEASE_MS = 60_000

    def __init__(
        self,
        config: Config,
        bus: Any | None = None,
        *,
        runtime_store: Any | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.config = config
        self.run_adapter: RunAdapter | None = None
        self.worker_id = worker_id or f"channels-{uuid.uuid4().hex}"
        self.repository = ChannelRepository(runtime_store) if runtime_store is not None else None
        self.registry = ChannelRegistry()
        self.plugins: dict[str, ChannelPlugin] = {}
        self._active_channels: set[str] = set()
        self._plugin_tasks: dict[str, asyncio.Task[Any]] = {}
        self._outbound_queues: dict[str, list[asyncio.Queue[OutboundMessage]]] = {}
        self._outbound_workers: list[asyncio.Task[Any]] = []
        self._dispatch_task: asyncio.Task[Any] | None = None
        self._coordinator_task: asyncio.Task[Any] | None = None
        self._init_channels()

    def _channel_config(self, channel_id: str) -> Any:
        return getattr(self.config.channels, channel_id, None)

    def _init_channels(self) -> None:
        registry = self.registry
        registry.load_all_builtins()
        for channel_id in registry.list_channels():
            channel_config = self._channel_config(channel_id)
            if channel_config is None or not channel_config.enabled:
                continue
            plugin = registry.get(channel_id)
            if plugin is None:
                continue
            self.plugins[channel_id] = plugin

    def set_run_adapter(self, adapter: RunAdapter) -> None:
        self.run_adapter = adapter
        for channel_id, plugin in self.plugins.items():
            channel_config = self._channel_config(channel_id)
            values = channel_config.model_dump()
            if channel_id == "telegram":
                values["groq_api_key"] = self.config.providers.groq.api_key
            values["messages_config"] = self.config.messages
            values["commands_config"] = self.config.commands
            plugin.configure(values, adapter)

    async def publish_outbound(self, message: OutboundMessage) -> None:
        """Persist or enqueue a terminal runtime reply for delivery."""
        if self.repository is None:
            await self._enqueue_local(message)
        else:
            await asyncio.to_thread(self._enqueue_cluster_outbound, message)

    async def start_all(self) -> None:
        if self.plugins and self.run_adapter is None:
            raise RuntimeError("channel run adapter must be configured before start")
        if not self.plugins:
            logger.warning("No channels enabled")
            return
        workers = max(1, min(32, self.config.gateway.channel_send_workers))
        for name, plugin in self.plugins.items():
            queues = [asyncio.Queue(maxsize=1000) for _ in range(workers)]
            self._outbound_queues[name] = queues
            self._outbound_workers.extend(
                asyncio.create_task(
                    self._send_loop(name, plugin, queue),
                    name=f"channel-send:{name}:{index}",
                )
                for index, queue in enumerate(queues)
            )
        self._dispatch_task = asyncio.create_task(
            self._outbox_loop(), name=f"channel-outbox:{self.worker_id}"
        )
        if self.repository is None:
            for name, plugin in self.plugins.items():
                self._active_channels.add(name)
                self._plugin_tasks[name] = asyncio.create_task(
                    self._run_plugin(name, plugin), name=f"channel:{name}"
                )
        else:
            self._coordinator_task = asyncio.create_task(
                self._lease_loop(), name=f"channel-leases:{self.worker_id}"
            )

    async def stop_all(self) -> None:
        tasks = [
            task
            for task in (
                self._dispatch_task,
                self._coordinator_task,
                *self._plugin_tasks.values(),
                *self._outbound_workers,
            )
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for plugin in self.plugins.values():
            try:
                await plugin.stop()
            except Exception as exc:
                logger.warning("Channel stop failed: {}", sanitize_error_message(str(exc)))
        self._release_all_channel_leases()
        self._active_channels.clear()
        self._plugin_tasks.clear()
        self._outbound_workers.clear()
        self._outbound_queues.clear()

    async def _run_plugin(self, name: str, plugin: ChannelPlugin) -> None:
        try:
            await plugin.start()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code, _, _ = classify_exception(exc)
            logger.error(
                "Channel {} failed [{}]: {}",
                name,
                code,
                sanitize_error_message(str(exc)),
            )

    async def _lease_loop(self) -> None:
        while True:
            for name, plugin in self.plugins.items():
                owned = await asyncio.to_thread(self._acquire_channel_lease, name)
                if owned and name not in self._active_channels:
                    self._active_channels.add(name)
                    self._plugin_tasks[name] = asyncio.create_task(
                        self._run_plugin(name, plugin), name=f"channel:{name}"
                    )
                elif not owned and name in self._active_channels:
                    self._active_channels.discard(name)
                    task = self._plugin_tasks.pop(name, None)
                    if task:
                        task.cancel()
                    await plugin.stop()
            await asyncio.sleep(self.LEASE_MS / 3000)

    async def _outbox_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(0.5)
            except asyncio.TimeoutError:
                pass
            if self.repository is not None:
                claimed = await asyncio.to_thread(self._claim_cluster_outbound)
                for message in claimed:
                    await self._enqueue_local(message)

    async def _enqueue_local(self, message: OutboundMessage) -> None:
        queues = self._outbound_queues.get(message.channel)
        if not queues:
            if self.repository is not None:
                await asyncio.to_thread(
                    self._finish_cluster_outbound,
                    message,
                    success=False,
                    error="channel is not active on lease owner",
                )
            return
        shard = zlib.crc32(message.chat_id.encode()) % len(queues)
        await queues[shard].put(message)

    async def _send_loop(
        self,
        name: str,
        plugin: ChannelPlugin,
        queue: asyncio.Queue[OutboundMessage],
    ) -> None:
        while True:
            message = await queue.get()
            success = False
            error: str | None = None
            try:
                result = await plugin.send(message)
                success = bool(result.success)
                error = result.error
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = sanitize_error_message(str(exc))
            finally:
                queue.task_done()
            if self.repository is not None:
                await asyncio.to_thread(
                    self._finish_cluster_outbound,
                    message,
                    success=success,
                    error=error,
                )
            if not success:
                logger.warning("Channel {} delivery failed: {}", name, error or "unknown")

    def _acquire_channel_lease(self, channel: str) -> bool:
        if self.repository is None:
            return True
        return self.repository.acquire_lease(
            channel,
            worker_id=self.worker_id,
            now_ms=int(time.time() * 1000),
            lease_ms=self.LEASE_MS,
        )

    def _release_all_channel_leases(self) -> None:
        if self.repository is not None:
            self.repository.release_owner(self.worker_id)

    def _enqueue_cluster_outbound(self, message: OutboundMessage) -> str | None:
        if self.repository is None:
            return None
        metadata = dict(message.metadata or {})
        return self.repository.enqueue(
            {
                "user_id": metadata.get("user_id"),
                "channel": message.channel,
                "chat_id": message.chat_id,
                "content": message.content,
                "reply_to": message.reply_to,
                "media": list(message.media),
                "metadata": metadata,
                "request_id": message.request_id,
                "tracker_id": message.tracker_id,
                "available_at_ms": int(time.time() * 1000),
            }
        )

    def _claim_cluster_outbound(self) -> list[OutboundMessage]:
        if self.repository is None or not self._active_channels:
            return []
        entries = self.repository.claim(
            sorted(self._active_channels),
            worker_id=self.worker_id,
            now_ms=int(time.time() * 1000),
            lease_ms=self.OUTBOX_LEASE_MS,
        )
        messages: list[OutboundMessage] = []
        for entry in entries:
            metadata = {
                **entry["metadata"],
                "_outbound_id": entry["id"],
                "_lease_version": entry["lease_version"],
            }
            messages.append(
                OutboundMessage(
                    channel=entry["channel"],
                    chat_id=entry["chat_id"],
                    content=entry["content"],
                    reply_to=entry["reply_to"],
                    media=entry["media"],
                    metadata=metadata,
                    request_id=entry["request_id"],
                    tracker_id=entry["tracker_id"],
                )
            )
        return messages

    def _finish_cluster_outbound(
        self,
        message: OutboundMessage,
        *,
        success: bool,
        error: str | None = None,
    ) -> tuple[str, int] | None:
        if self.repository is None:
            return None
        outbound_id = str(message.metadata.get("_outbound_id") or "")
        lease_version = int(message.metadata.get("_lease_version") or 0)
        if not outbound_id or not lease_version:
            return None
        return self.repository.finish(
            outbound_id,
            worker_id=self.worker_id,
            lease_version=lease_version,
            success=success,
            error=error,
            max_attempts=self.config.gateway.channel_send_max_attempts,
            now_ms=int(time.time() * 1000),
        )

    def get_status(self) -> dict[str, dict[str, Any]]:
        leases = self.repository.list_leases() if self.repository is not None else {}
        counts = self.repository.status_counts() if self.repository is not None else {}
        names = set(self.registry.list_builtins()) | set(self.plugins) | set(leases)
        now_ms = int(time.time() * 1000)
        result: dict[str, dict[str, Any]] = {}
        for name in sorted(names):
            lease = leases.get(name, {})
            owner = lease.get("owner")
            running = bool(lease and int(lease.get("until", 0)) > now_ms)
            if self.repository is None:
                running = name in self._active_channels
                owner = self.worker_id if running else None
            plugin_status = self.plugins[name].get_status() if name in self.plugins else None
            result[name] = {
                "enabled": name in self.plugins,
                "running": running,
                "connected": bool(plugin_status.connected) if plugin_status else False,
                "last_error": plugin_status.last_error if plugin_status else None,
                "last_message_at": plugin_status.last_message_at if plugin_status else None,
                "local_owner": owner == self.worker_id,
                "owner_worker_id": owner,
                "outbox": counts.get(name, {"pending": 0, "sending": 0, "dead": 0}),
            }
        return result
