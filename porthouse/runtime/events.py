"""Durable event publishing and live subscriptions."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import replace

from porthouse.runtime.models import AgentEvent, EventType
from porthouse.runtime.narrative import prepare_event
from porthouse.storage.contracts import EventStorePort

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "timed_out"})
_SUSPENDED_STATUSES = frozenset(
    {"waiting_input", "waiting_approval", "waiting_external", "scheduled", "paused"}
)
# Bound the cached run identities; oldest entries are evicted FIFO.
_RUN_IDENTITY_MAX = 10_000
# Live subscriptions never outlive this duration even for stuck runs.
_MAX_SUBSCRIPTION_SECONDS = 3600.0


class EventBroker:
    """Persist events first, then fan them out to bounded live subscribers."""

    def __init__(self, store: EventStorePort, *, subscriber_buffer: int = 256) -> None:
        self._store = store
        self.subscriber_buffer = max(1, subscriber_buffer)
        self._subscribers: dict[str, set[asyncio.Queue[AgentEvent]]] = defaultdict(set)
        self._run_identity: dict[str, tuple[str, str | None, str | None, str, str, str]] = {}
        self._lock = asyncio.Lock()

    async def prepare(self, event: AgentEvent) -> AgentEvent:
        """Attach durable run identity and the safe public narrative."""
        identity = self._run_identity.get(event.run_id)
        if identity is None:
            record = await asyncio.to_thread(self._store.get_runtime_run, event.run_id)
            if record is not None:
                identity = (
                    record.root_run_id or record.run_id,
                    record.parent_run_id,
                    record.parent_task_id,
                    record.user_id,
                    record.session_id,
                    record.agent_id,
                )
                self._run_identity[event.run_id] = identity
                while len(self._run_identity) > _RUN_IDENTITY_MAX:
                    self._run_identity.pop(next(iter(self._run_identity)))
        if identity is not None:
            root_run_id, parent_run_id, parent_task_id, user_id, session_id, agent_id = identity
            event = replace(
                event,
                root_run_id=event.root_run_id or root_run_id,
                parent_run_id=event.parent_run_id or parent_run_id,
                parent_task_id=event.parent_task_id or parent_task_id,
                user_id=event.user_id or user_id,
                session_id=event.session_id or session_id,
                agent_id=event.agent_id or agent_id,
            )
        return prepare_event(event)

    async def fanout(self, persisted: AgentEvent) -> AgentEvent:
        """Deliver an event that has already committed to the durable store."""
        async with self._lock:
            subscribers = list(self._subscribers.get(persisted.run_id, ()))
        for queue in subscribers:
            try:
                queue.put_nowait(persisted)
            except asyncio.QueueFull:
                # Drop the oldest live notification. The durable store remains
                # authoritative and clients can replay using the sequence.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(persisted)
                except asyncio.QueueFull:
                    pass
        return persisted

    async def publish(self, event: AgentEvent) -> AgentEvent:
        event = await self.prepare(event)
        persisted = await asyncio.to_thread(self._store.append_runtime_event, event)
        return await self.fanout(persisted)

    async def _subscription_finished(self, run_id: str, cursor: int) -> bool:
        """True when a terminal run has no more durable events to deliver."""
        record = await asyncio.to_thread(self._store.get_runtime_run, run_id)
        if record is None:
            return True
        if record.status not in _TERMINAL_STATUSES | _SUSPENDED_STATUSES:
            return False
        remaining = await asyncio.to_thread(
            self._store.list_runtime_events,
            run_id,
            after_sequence=cursor,
            limit=1,
        )
        return not remaining

    async def _history_purged(self, run_id: str) -> bool:
        """True when retention tombstoned this run's event/log history."""
        record = await asyncio.to_thread(self._store.get_runtime_run, run_id)
        if record is None:
            return False
        metadata = dict((getattr(record, "options", None) or {}).get("metadata") or {})
        return bool(metadata.get("events_purged"))

    async def subscribe(
        self,
        run_id: str,
        *,
        after_sequence: int = 0,
    ) -> AsyncIterator[AgentEvent]:
        queue: asyncio.Queue[AgentEvent] = asyncio.Queue(self.subscriber_buffer)
        async with self._lock:
            self._subscribers[run_id].add(queue)
        cursor = max(0, after_sequence)
        started = time.monotonic()
        try:
            if await self._history_purged(run_id):
                # Retention deleted this run's events/logs while the run row
                # survived.  Signal the gap explicitly instead of letting a
                # sequence replay silently miss events.
                yield AgentEvent(
                    run_id=run_id,
                    type=EventType.RUN_HISTORY_PURGED.value,
                    summary="Earlier run history was purged by retention",
                    data={"reason": "runtime events and logs purged by retention"},
                )
            history = await asyncio.to_thread(
                self._store.list_runtime_events,
                run_id,
                after_sequence=cursor,
                limit=1000,
            )
            for event in history:
                if event.sequence is not None and event.sequence <= cursor:
                    continue
                cursor = max(cursor, event.sequence or cursor)
                yield event
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    pending = [event]
                except TimeoutError:
                    if time.monotonic() - started >= _MAX_SUBSCRIPTION_SECONDS:
                        return
                    # Another FastAPI worker may own the run. Polling the
                    # durable log makes its events visible to this SSE client.
                    pending = await asyncio.to_thread(
                        self._store.list_runtime_events,
                        run_id,
                        after_sequence=cursor,
                        limit=1000,
                    )
                for event in pending:
                    if event.sequence is not None and event.sequence <= cursor:
                        continue
                    cursor = max(cursor, event.sequence or cursor)
                    yield event
                # A terminal run has no future events; end the subscription
                # once the durable log has been fully replayed.
                if await self._subscription_finished(run_id, cursor):
                    return
        finally:
            async with self._lock:
                subscribers = self._subscribers.get(run_id)
                if subscribers is not None:
                    subscribers.discard(queue)
                    if not subscribers:
                        self._subscribers.pop(run_id, None)
