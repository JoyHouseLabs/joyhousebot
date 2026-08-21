"""Distributed channel connector ownership and durable outbound delivery."""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
import zlib
from typing import Any

from loguru import logger

from joyhousebot.bus.events import OutboundMessage
from joyhousebot.channels.extensions import ChannelExtension, ChannelExtensionRegistry
from joyhousebot.channels.repository import ChannelRepository
from joyhousebot.channels.run_adapter import RunAdapter
from joyhousebot.config.extensions import (
    allowed_channel_extension_ids,
    allowed_channel_ids,
    extension_settings,
)
from joyhousebot.config.schema import Config
from joyhousebot.utils.exceptions import classify_exception, sanitize_error_message


class ChannelManager:
    """Own enabled connectors through leases and deliver a fenced PG outbox."""

    LEASE_MS = 30_000
    OUTBOX_LEASE_MS = 60_000

    def __init__(
        self,
        config: Config,
        *,
        runtime_store: Any | None = None,
        worker_id: str | None = None,
    ) -> None:
        self.config = config
        self.run_adapter: RunAdapter | None = None
        self.worker_id = worker_id or f"channels-{uuid.uuid4().hex}"
        self.repository = ChannelRepository(runtime_store) if runtime_store is not None else None
        self.registry = ChannelExtensionRegistry()
        self.extensions: dict[str, ChannelExtension] = {}
        self._active_channels: set[str] = set()
        self._allowed_channels: set[str] = set()
        self._extension_tasks: dict[str, asyncio.Task[Any]] = {}
        self._outbound_queues: dict[str, list[asyncio.Queue[OutboundMessage]]] = {}
        self._outbound_workers: dict[str, list[asyncio.Task[Any]]] = {}
        self._dispatch_task: asyncio.Task[Any] | None = None
        self._coordinator_task: asyncio.Task[Any] | None = None
        self._activation_task: asyncio.Task[Any] | None = None
        self._init_channels()

    def _channel_config(self, channel_id: str, extension: ChannelExtension) -> Any:
        manifest = getattr(extension, "extension_manifest", None)
        extension_id = (
            str(manifest.extension_id) if manifest is not None else f"channel-{channel_id}"
        )
        values = extension_settings(self.config, extension_id)
        values["enabled"] = True
        return values

    def _init_channels(self) -> None:
        registry = self.registry
        extensions = self.config.extensions
        if extensions.discover_entry_points:
            registry.load_entry_points(
                allowed_ids=allowed_channel_extension_ids(self.config)
            )

        self._allowed_channels = set(allowed_channel_ids(self.config))
        for channel_id in sorted(self._allowed_channels):
            extension = registry.get(channel_id)
            if extension is None:
                raise RuntimeError(
                    f"channel {channel_id!r} is enabled but no installed extension provides it"
                )
            if self._extension_desired(channel_id):
                self.extensions[channel_id] = extension

        if self.repository is not None:
            for manifest in registry.manifests():
                self.repository.store.upsert_extension_release(manifest.to_release_dict())

    def extension_releases(self) -> list[dict[str, Any]]:
        return [manifest.to_release_dict() for manifest in self.registry.manifests()]

    def set_run_adapter(self, adapter: RunAdapter) -> None:
        self.run_adapter = adapter
        for channel_id, extension in self.extensions.items():
            self._configure_extension(channel_id, extension)

    def _configure_extension(self, channel_id: str, extension: ChannelExtension) -> None:
        if self.run_adapter is None:
            return
        channel_config = self._channel_config(channel_id, extension)
        values = (
            dict(channel_config)
            if isinstance(channel_config, dict)
            else channel_config.model_dump()
        )
        values["messages_config"] = self.config.messages
        values["commands_config"] = self.config.commands
        extension.configure(values, self.run_adapter)

    def _extension_desired(self, channel_id: str) -> bool:
        if self.repository is None:
            return True
        checker = getattr(self.repository.store, "is_extension_execution_enabled", None)
        return not callable(checker) or bool(checker(f"channel-{channel_id}"))

    def _desired_channels(self) -> set[str]:
        return {
            name for name in self._allowed_channels if self._extension_desired(name)
        }

    async def publish_outbound(self, message: OutboundMessage) -> None:
        """Persist or enqueue a terminal runtime reply for delivery."""
        if self.repository is None:
            await self._enqueue_local(message)
        else:
            await asyncio.to_thread(self._enqueue_cluster_outbound, message)

    async def start_all(self) -> None:
        if self.extensions and self.run_adapter is None:
            raise RuntimeError("channel run adapter must be configured before start")
        if not self.extensions:
            logger.warning("No channels currently active; waiting for control-plane activation")
        workers = max(1, min(32, self.config.gateway.channel_send_workers))
        for name, extension in self.extensions.items():
            queues = [asyncio.Queue(maxsize=1000) for _ in range(workers)]
            self._outbound_queues[name] = queues
            self._outbound_workers[name] = [
                asyncio.create_task(
                    self._send_loop(name, extension, queue),
                    name=f"channel-send:{name}:{index}",
                )
                for index, queue in enumerate(queues)
            ]
        self._dispatch_task = asyncio.create_task(
            self._outbox_loop(), name=f"channel-outbox:{self.worker_id}"
        )
        if self.repository is None:
            for name, extension in self.extensions.items():
                self._active_channels.add(name)
                self._extension_tasks[name] = asyncio.create_task(
                    self._run_extension(name, extension), name=f"channel:{name}"
                )
        else:
            self._coordinator_task = asyncio.create_task(
                self._lease_loop(), name=f"channel-leases:{self.worker_id}"
            )
        self._activation_task = asyncio.create_task(
            self._activation_loop(), name=f"channel-activation:{self.worker_id}"
        )

    async def stop_all(self) -> None:
        tasks = [
            task
            for task in (
                self._dispatch_task,
                self._coordinator_task,
                self._activation_task,
                *self._extension_tasks.values(),
                *(task for values in self._outbound_workers.values() for task in values),
            )
            if task is not None
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for extension in self.extensions.values():
            try:
                await extension.stop()
            except Exception as exc:
                logger.warning("Channel stop failed: {}", sanitize_error_message(str(exc)))
        self._release_all_channel_leases()
        self._active_channels.clear()
        self._extension_tasks.clear()
        self._outbound_workers.clear()
        self._outbound_queues.clear()

    async def _activation_loop(self) -> None:
        """Reconcile PostgreSQL desired state without restarting Channel Worker."""
        while True:
            desired = await asyncio.to_thread(self._desired_channels)
            for name in sorted(set(self.extensions) - desired):
                await self._deactivate_channel(name)
            for name in sorted(desired - set(self.extensions)):
                await self._activate_channel(name)
            await asyncio.sleep(1.0)

    async def _activate_channel(self, name: str) -> None:
        extension = self.registry.get(name)
        if extension is None:
            logger.error("Cannot activate unavailable Channel extension {}", name)
            return
        if self.run_adapter is None:
            raise RuntimeError("channel run adapter must be configured before activation")
        self._configure_extension(name, extension)
        self.extensions[name] = extension
        workers = max(1, min(32, self.config.gateway.channel_send_workers))
        queues = [asyncio.Queue(maxsize=1000) for _ in range(workers)]
        self._outbound_queues[name] = queues
        self._outbound_workers[name] = [
            asyncio.create_task(
                self._send_loop(name, extension, queue),
                name=f"channel-send:{name}:{index}",
            )
            for index, queue in enumerate(queues)
        ]
        if self.repository is None:
            self._active_channels.add(name)
            self._extension_tasks[name] = asyncio.create_task(
                self._run_extension(name, extension), name=f"channel:{name}"
            )
        logger.info("Activated Channel extension channel-{}", name)

    async def _deactivate_channel(self, name: str) -> None:
        extension = self.extensions.pop(name, None)
        if extension is None:
            return
        self._active_channels.discard(name)
        task = self._extension_tasks.pop(name, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        workers = self._outbound_workers.pop(name, [])
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        self._outbound_queues.pop(name, None)
        await extension.stop()
        if self.repository is not None:
            await asyncio.to_thread(
                self.repository.release, name, worker_id=self.worker_id
            )
        logger.info("Deactivated Channel extension channel-{}", name)

    async def _run_extension(self, name: str, extension: ChannelExtension) -> None:
        try:
            await extension.start()
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
            for name, extension in list(self.extensions.items()):
                owned = await asyncio.to_thread(self._acquire_channel_lease, name)
                if owned and name not in self._active_channels:
                    self._active_channels.add(name)
                    self._extension_tasks[name] = asyncio.create_task(
                        self._run_extension(name, extension), name=f"channel:{name}"
                    )
                elif not owned and name in self._active_channels:
                    self._active_channels.discard(name)
                    task = self._extension_tasks.pop(name, None)
                    if task:
                        task.cancel()
                    await extension.stop()
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
        extension: ChannelExtension,
        queue: asyncio.Queue[OutboundMessage],
    ) -> None:
        while True:
            message = await queue.get()
            success = False
            error: str | None = None
            try:
                result = await extension.send(message)
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
            lease_ms=self.LEASE_MS,
        )

    def _release_all_channel_leases(self) -> None:
        if self.repository is not None:
            self.repository.release_owner(self.worker_id)

    def _enqueue_cluster_outbound(self, message: OutboundMessage) -> str | None:
        if self.repository is None:
            return None
        metadata = dict(message.metadata or {})
        outbound_id = str(
            metadata.get("_runtime_outbound_id") or metadata.get("id") or ""
        ).strip()
        if not outbound_id and (message.request_id or metadata.get("run_id")):
            identity = "\x1f".join(
                (
                    message.channel,
                    message.chat_id,
                    str(message.request_id or ""),
                    str(metadata.get("run_id") or ""),
                    str(message.reply_to or ""),
                    message.content,
                )
            )
            outbound_id = f"message:{hashlib.sha256(identity.encode()).hexdigest()}"
        return self.repository.enqueue(
            {
                "id": outbound_id or None,
                "user_id": metadata.get("user_id"),
                "channel": message.channel,
                "chat_id": message.chat_id,
                "content": message.content,
                "reply_to": message.reply_to,
                "media": list(message.media),
                "metadata": metadata,
                "request_id": message.request_id,
                "tracker_id": message.tracker_id,
            }
        )

    def _claim_cluster_outbound(self) -> list[OutboundMessage]:
        if self.repository is None or not self._active_channels:
            return []
        entries = self.repository.claim(
            sorted(self._active_channels),
            worker_id=self.worker_id,
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
        )

    def get_status(self) -> dict[str, dict[str, Any]]:
        leases = self.repository.list_leases() if self.repository is not None else {}
        counts = self.repository.status_counts() if self.repository is not None else {}
        names = set(self.registry.list_channels()) | set(self.extensions) | set(leases)
        # Lease expiry is compared against the database clock that wrote it.
        now_ms = (
            self.repository.db_now_ms()
            if self.repository is not None
            else int(time.time() * 1000)
        )
        result: dict[str, dict[str, Any]] = {}
        for name in sorted(names):
            lease = leases.get(name, {})
            owner = lease.get("owner")
            running = bool(lease and int(lease.get("until", 0)) > now_ms)
            if self.repository is None:
                running = name in self._active_channels
                owner = self.worker_id if running else None
            extension_status = self.extensions[name].get_status() if name in self.extensions else None
            result[name] = {
                "enabled": name in self.extensions,
                "running": running,
                "connected": bool(extension_status.connected) if extension_status else False,
                "last_error": extension_status.last_error if extension_status else None,
                "last_message_at": extension_status.last_message_at if extension_status else None,
                "local_owner": owner == self.worker_id,
                "owner_worker_id": owner,
                "outbox": counts.get(name, {"pending": 0, "sending": 0, "dead": 0}),
            }
        return result
