"""Bridge durable Agent Runtime execution to chat channel connectors."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger

from joyhousebot.bus.events import InboundMessage, OutboundMessage
from joyhousebot.channels.run_adapter import RunAdapter
from joyhousebot.runtime.models import AgentOptions
from joyhousebot.runtime.tracking import append_trace_event_async, ensure_tracking_ids


class ChannelRuntimeBridge(RunAdapter):
    """Consume channel ingress, submit durable runs, and route terminal replies.

    Connectors remain responsible for platform I/O.  The bridge is deliberately
    thin: execution ordering, leases, retries, and concurrency belong to the
    NativeAgentRuntime rather than a process-local message queue.
    """

    def __init__(
        self,
        *,
        runtime: Any,
        outbound_sink: Callable[[OutboundMessage], Awaitable[None]],
        default_agent_id: str = "default",
        max_in_flight: int = 256,
    ) -> None:
        self.runtime = runtime
        self._outbound_sink = outbound_sink
        self.default_agent_id = default_agent_id or "default"
        self._running = False
        self._run_task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._capacity = asyncio.Semaphore(max(1, max_in_flight))

    @staticmethod
    def _runtime_user_id(msg: InboundMessage) -> str:
        """Return the isolated user identity for a channel message.

        User state must not be shared merely because several people are in the
        same group chat.  Prefixing the provider identity also prevents equal
        sender IDs from different channels from colliding.
        """
        configured = str((msg.metadata or {}).get("runtime_user_id") or "").strip()
        sender_id = str(msg.sender_id or "").strip()
        if configured:
            return configured
        if not sender_id:
            raise ValueError("channel sender_id is required for user isolation")
        return f"{msg.channel}:{sender_id}"

    @staticmethod
    def _idempotency_key(msg: InboundMessage) -> str | None:
        message_id = (msg.metadata or {}).get("message_id")
        if message_id is None or str(message_id).strip() == "":
            return None
        return f"channel:{msg.channel}:{msg.chat_id}:{message_id}"

    async def run(self) -> None:
        """Keep the adapter lifecycle alive.

        Ingress is now invoked directly by Channel Extensions; this task exists
        only so ChannelWorker can manage adapter lifetime uniformly.
        """
        self._running = True
        self._run_task = asyncio.current_task()
        logger.info("Channel runtime bridge started")
        try:
            while self._running:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise
        finally:
            self._running = False
            self._run_task = None

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        self._tasks.discard(task)
        self._capacity.release()
        if task.cancelled():
            return
        try:
            task.result()
        except Exception as exc:
            logger.error("Channel runtime bridge task failed: {}", exc)

    async def handle(self, msg: InboundMessage):
        await self._capacity.acquire()
        try:
            return await self._handle(msg)
        finally:
            self._capacity.release()

    async def _handle(self, msg: InboundMessage):
        """Run one channel message through the durable runtime."""
        metadata = dict(msg.metadata or {})
        metadata.setdefault("channel", msg.channel)
        metadata.setdefault("chat_id", msg.chat_id)
        metadata.setdefault("sender_id", msg.sender_id)
        request_id, tracker_id = ensure_tracking_ids(
            request_id=msg.request_id or metadata.get("request_id") or metadata.get("message_id"),
            tracker_id=msg.tracker_id or metadata.get("tracker_id"),
            request_prefix="channel",
        )
        metadata["request_id"] = request_id
        metadata["tracker_id"] = tracker_id
        user_id = self._runtime_user_id(msg)
        reply_to = metadata.get("message_id")
        # The terminal Run transaction consumes this frozen delivery intent and
        # inserts a deterministic outbox row. It is private Runtime metadata,
        # never a second execution path inside a connector process.
        metadata["_runtime_channel_delivery"] = {
            "channel": msg.channel,
            "chat_id": msg.chat_id,
            "reply_to": str(reply_to) if reply_to is not None else None,
            "request_id": request_id,
            "tracker_id": tracker_id,
            "metadata": {
                "message_id": str(reply_to) if reply_to is not None else None,
                "sender_id": msg.sender_id,
            },
        }
        await append_trace_event_async(
            store=self.runtime.store,
            tracker_id=tracker_id,
            request_id=request_id,
            user_id=user_id,
            transport=f"channel:{msg.channel}",
            direction="inbound",
            operation="message.receive",
            stage="request",
            status="received",
            data={"chat_id": msg.chat_id, "sender_id": msg.sender_id, "has_media": bool(msg.media)},
        )
        options = AgentOptions(
            prompt=msg.content,
            user_id=user_id,
            session_id=msg.session_key,
            agent_id=str(metadata.get("agent_id") or self.default_agent_id),
            channel=msg.channel,
            chat_id=msg.chat_id,
            sender_id=msg.sender_id,
            media=list(msg.media or []),
            metadata=metadata,
            idempotency_key=self._idempotency_key(msg),
            request_id=request_id,
            tracker_id=tracker_id,
        )
        record = await self.runtime.submit_run(options)
        # Bound the wait so a lost or wedged run cannot leak this handler
        # coroutine forever: run timeout plus a grace period.
        wait_timeout = float(options.timeout_seconds or 300) + 300.0
        completed = await self.runtime.wait(record.run_id, timeout=wait_timeout)
        if completed is None:
            logger.error("Channel run disappeared: {}", record.run_id)
            return None
        if completed.status == "cancelled":
            return completed

        # PostgreSQL workers project the reply in finish_runtime_run_bundle(). Waiting
        # here preserves the historical adapter return value, but enqueueing a
        # second message would violate exactly-once intent creation.
        if getattr(self.runtime.store, "backend_name", None) == "postgres":
            return completed

        if completed.status == "completed":
            content = str((completed.result or {}).get("content") or "")
        else:
            if completed.status not in {"failed", "timed_out"}:
                logger.warning(
                    "Channel run {} did not finish within {}s; giving up (status {})",
                    record.run_id,
                    wait_timeout,
                    completed.status,
                )
            else:
                logger.warning(
                    "Channel run {} ended with status {}", record.run_id, completed.status
                )
            content = "Sorry, I couldn't complete that request. Please try again."

        await self._outbound_sink(
            OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content=content,
                reply_to=str(reply_to) if reply_to is not None else None,
                metadata={
                    **dict(msg.metadata or {}),
                    "request_id": request_id,
                    "tracker_id": tracker_id,
                    "user_id": user_id,
                },
                request_id=request_id,
                tracker_id=tracker_id,
            )
        )
        return completed

    async def close(self) -> None:
        self._running = False
        run_task = self._run_task
        if run_task is not None and run_task is not asyncio.current_task():
            run_task.cancel()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if run_task is not None and run_task is not asyncio.current_task():
            await asyncio.gather(run_task, return_exceptions=True)
