"""MessageProcessor responsibilities for the shared Agent engine."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from loguru import logger

from joyhousebot.agent.context_budget import context_candidate
from joyhousebot.agent.context_manifest import source_entry
from joyhousebot.agent.response_prefix import resolve_response_prefix
from joyhousebot.bus.events import InboundMessage, OutboundMessage
from joyhousebot.runtime.context import (
    RunContext,
)
from joyhousebot.session.models import Session

if TYPE_CHECKING:
    pass


class MessageProcessorMixin:
    @staticmethod
    def _context_mode(run_context: RunContext) -> str:
        """Return the frozen context mode for a scheduled Monitor Run."""
        metadata = dict(run_context.metadata or {})
        if (
            metadata.get("schedule_payload_kind") == "agent_monitor"
            and metadata.get("monitor_context_mode") == "light"
        ):
            return "light"
        return "full"

    @staticmethod
    def _message_lock_key(msg: InboundMessage, session_key: str | None) -> str:
        if session_key:
            return session_key
        if msg.channel == "system":
            return msg.chat_id if ":" in msg.chat_id else f"cli:{msg.chat_id}"
        return msg.session_key

    async def _process_message(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        stream_callback: Callable[[str], Awaitable[None]] | None = None,
        execution_stream_callback: Callable[[str, dict], Awaitable[None]] | None = None,
        check_abort_requested: Callable[[str], bool] | None = None,
        run_context: RunContext | None = None,
    ) -> OutboundMessage | None:
        """Serialize one session and optionally limit global concurrent runs."""
        lock_key = self._message_lock_key(msg, session_key)
        session_lock = self._session_locks.setdefault(lock_key, asyncio.Lock())
        self._session_lock_users[lock_key] = self._session_lock_users.get(lock_key, 0) + 1

        async def _execute() -> OutboundMessage | None:
            async with session_lock:

                async def _process() -> OutboundMessage | None:
                    return await self._process_message_inner(
                        msg,
                        session_key=session_key,
                        stream_callback=stream_callback,
                        execution_stream_callback=execution_stream_callback,
                        check_abort_requested=check_abort_requested,
                        run_context=run_context,
                    )

                if self._run_semaphore is None:
                    return await _process()
                async with self._run_semaphore:
                    return await _process()

        try:
            return await _execute()
        finally:
            remaining = self._session_lock_users.get(lock_key, 1) - 1
            if remaining <= 0:
                self._session_lock_users.pop(lock_key, None)
                if self._session_locks.get(lock_key) is session_lock:
                    self._session_locks.pop(lock_key, None)
            else:
                self._session_lock_users[lock_key] = remaining

    async def _process_message_inner(
        self,
        msg: InboundMessage,
        session_key: str | None = None,
        stream_callback: Callable[[str], Awaitable[None]] | None = None,
        execution_stream_callback: Callable[[str, dict], Awaitable[None]] | None = None,
        check_abort_requested: Callable[[str], bool] | None = None,
        run_context: RunContext | None = None,
    ) -> OutboundMessage | None:
        """
        Process a single inbound message.

        Args:
            msg: The inbound message to process.
            session_key: Override session key (used by process_direct).
            stream_callback: If set, called with each content delta when provider supports streaming.
            execution_stream_callback: If set, called with (event_type, payload) for llm_delta, tool_start, tool_output, tool_end, final.
            check_abort_requested: If set, run can be aborted (e.g. chat.abort); when True for current run_id, returns None.

        Returns:
            The response message, or None if no response needed (e.g. run aborted).
        """
        # System messages route back via chat_id ("channel:chat_id")
        if msg.channel == "system":
            return await self._process_system_message(msg, run_context=run_context)

        logger.info(
            "Processing message: channel={} sender={} content_chars={}",
            msg.channel,
            msg.sender_id,
            len(msg.content),
        )
        session, scope_key, run_context = await self._prepare_message_context(
            msg, session_key=session_key, run_context=run_context
        )
        context_mode = self._context_mode(run_context)
        handled, command_response = await self._native_command(
            msg, session=session, run_context=run_context
        )
        if handled:
            return command_response
        if context_mode != "light" and len(session.messages) > self.memory_window:
            await self._consolidate_memory(session, run_context=run_context)
        initial_messages, run_context = self._build_run_context_messages(
            msg,
            session=session,
            scope_key=scope_key,
            run_context=run_context,
            context_mode=context_mode,
        )
        final_content, tools_used, aborted, last_response = await self._run_agent_loop(
            initial_messages,
            stream_callback=stream_callback,
            execution_stream_callback=execution_stream_callback,
            check_abort_requested=check_abort_requested,
            run_context=run_context,
        )
        if aborted:
            return None
        final_content = self._final_response_content(final_content)
        logger.info(
            "Agent response ready: channel={} sender={} content_chars={}",
            msg.channel,
            msg.sender_id,
            len(final_content),
        )
        await self._save_message_response(
            session,
            msg=msg,
            content=final_content,
            tools_used=tools_used,
            last_response=last_response,
        )
        reply_to = None
        if msg.metadata and "message_id" in msg.metadata:
            message_id = msg.metadata["message_id"]
            reply_to = str(message_id) if message_id is not None else None
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            reply_to=reply_to,
            metadata=msg.metadata or {},
        )

    async def _prepare_message_context(
        self,
        msg: InboundMessage,
        *,
        session_key: str | None,
        run_context: RunContext | None,
    ) -> tuple[Session, str | None, RunContext]:
        key = session_key or msg.session_key
        session = await asyncio.to_thread(self.sessions.get_or_create, key)
        scope_key = self._resolve_memory_scope_key(
            key,
            getattr(msg, "sender_id", "") or "",
            getattr(msg, "metadata", None) or {},
            run_context,
        )
        if scope_key:
            retrieval = (
                getattr(getattr(self.config, "tools", None), "retrieval", None)
                if self.config
                else None
            )
            if retrieval and getattr(retrieval, "memory_scope", "shared") == "user":
                session.metadata["last_memory_scope_key"] = scope_key
        prepared = (
            replace(
                run_context,
                session_key=key,
                channel=msg.channel,
                chat_id=msg.chat_id,
                memory_scope=scope_key,
                memory_policy=run_context.memory_policy or dict(getattr(self, "memory_policy", {})),
            )
            if run_context is not None
            else RunContext(
                run_id=uuid.uuid4().hex,
                session_key=key,
                channel=msg.channel,
                chat_id=msg.chat_id,
                user_id=(getattr(msg, "sender_id", "") or msg.chat_id or "system"),
                agent_id="default",
                session_id=key,
                memory_scope=scope_key,
                memory_policy=dict(getattr(self, "memory_policy", {})),
            )
        )
        return session, scope_key, prepared

    async def _native_command(
        self, msg: InboundMessage, *, session: Session, run_context: RunContext
    ) -> tuple[bool, OutboundMessage | None]:
        cmd = msg.content.strip().lower()
        commands_config = getattr(self.config, "commands", None) if self.config else None
        native_enabled = (
            commands_config is None
            or getattr(commands_config, "native", "auto") is True
            or getattr(commands_config, "native", "auto") == "auto"
        )
        if native_enabled and cmd == "/new":
            messages_to_archive = session.messages.copy()
            session.clear()
            await asyncio.to_thread(self.sessions.save, session)
            await asyncio.to_thread(self.sessions.invalidate, session.key)

            temp_session = Session(key=session.key)
            temp_session.messages = messages_to_archive
            await self._consolidate_memory(
                temp_session, archive_all=True, run_context=run_context
            )
            return True, OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id,
                content="New session started. Memory consolidation completed."
            )
        if native_enabled and cmd == "/help":
            return True, OutboundMessage(
                channel=msg.channel,
                chat_id=msg.chat_id,
                content="🐈 joyhousebot commands:\n/new — Start a new conversation\n/help — Show available commands",
            )
        if (cmd == "/new" or cmd == "/help") and not native_enabled:
            return True, OutboundMessage(
                channel=msg.channel, chat_id=msg.chat_id, content="Commands are disabled."
            )
        return False, None

    def _build_run_context_messages(
        self,
        msg: InboundMessage,
        *,
        session: Session,
        scope_key: str | None,
        run_context: RunContext,
        context_mode: str,
    ) -> tuple[list[dict[str, Any]], RunContext]:
        (
            initial_messages,
            context_sources,
            context_candidates,
        ) = self.context.build_messages_with_candidates(
            history=(
                []
                if context_mode == "light"
                else session.get_history(max_messages=self.memory_window)
            ),
            current_message=msg.content,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
            max_context_tokens=self.max_context_tokens,
            scope_key=scope_key,
            skill_names=list(run_context.skill_names),
            skill_refs=list(run_context.skill_refs),
            context_timestamp=run_context.context_timestamp,
            context_mode=context_mode,
        )
        run_instructions = self._apply_run_instructions(initial_messages, run_context)
        if run_instructions:
            instruction_source = source_entry(
                source_kind="run_instruction",
                source_id="run:execution-policy",
                content=run_instructions,
                classification="internal",
                authority="runtime",
                freshness="run_snapshot",
                priority=100,
                included_reason="run_execution_contract",
            )
            context_sources.append(instruction_source)
            context_candidates.append(
                context_candidate(
                    candidate_id="system:run-instruction",
                    target="system",
                    content=run_instructions,
                    source_keys=[("run_instruction", "run:execution-policy")],
                    priority=100,
                    required=True,
                    order=len(context_candidates),
                    separator="\n\n",
                )
            )
        prepared = replace(
            run_context,
            context_sources=tuple(context_sources),
            context_candidates=tuple(context_candidates),
            context_initial_message_count=len(initial_messages),
            context_budget_tokens=self.max_context_tokens,
            context_budget_strategy=(
                "priority_budget_v1" if self.max_context_tokens else "unbounded_v1"
            ),
        )
        return initial_messages, prepared

    def _final_response_content(self, final_content: str | None) -> str:
        if final_content is None:
            final_content = "I've completed processing but have no response to give."
        messages_config = getattr(self.config, "messages", None) if self.config else None
        if messages_config and getattr(messages_config, "response_prefix", None):
            prefix_template = (messages_config.response_prefix or "").strip()
            if prefix_template:
                provider_name = self._resolve_provider_name_for_model(self.model) or ""
                identity_name = (
                    getattr(getattr(self.config, "agent", None), "name", None)
                    if self.config
                    else None
                ) or "joyhousebot"
                prefix = resolve_response_prefix(
                    prefix_template,
                    {
                        "model": self.model or "",
                        "provider": provider_name,
                        "identityName": identity_name,
                        "identity": identity_name,
                    },
                )
                if prefix:
                    final_content = prefix + "\n" + final_content
        return final_content

    async def _save_message_response(
        self,
        session: Session,
        *,
        msg: InboundMessage,
        content: str,
        tools_used: list[str],
        last_response: Any,
    ) -> None:
        session.add_message("user", msg.content)
        usage_kw: dict[str, Any] = {"tools_used": tools_used if tools_used else None}
        if last_response and last_response.usage:
            usage_kw["usage"] = dict(last_response.usage)
        session.add_message("assistant", content, **usage_kw)
        await asyncio.to_thread(self.sessions.save, session)

    async def _process_system_message(
        self,
        msg: InboundMessage,
        run_context: RunContext | None = None,
    ) -> OutboundMessage | None:
        """
        Process a system message (e.g., subagent announce).

        The chat_id field contains "original_channel:original_chat_id" to route
        the response back to the correct destination.
        """
        logger.info(f"Processing system message from {msg.sender_id}")

        # Parse origin from chat_id (format: "channel:chat_id")
        if ":" in msg.chat_id:
            parts = msg.chat_id.split(":", 1)
            origin_channel = parts[0]
            origin_chat_id = parts[1]
        else:
            # Fallback
            origin_channel = "cli"
            origin_chat_id = msg.chat_id

        session_key = f"{origin_channel}:{origin_chat_id}"
        session = await asyncio.to_thread(self.sessions.get_or_create, session_key)
        scope_key = self._resolve_memory_scope_key(
            session_key,
            getattr(msg, "sender_id", "") or "",
            getattr(msg, "metadata", None) or {},
            run_context,
        )
        run_context = (
            replace(
                run_context,
                session_key=session_key,
                channel=origin_channel,
                chat_id=origin_chat_id,
                memory_scope=scope_key,
                memory_policy=run_context.memory_policy or dict(getattr(self, "memory_policy", {})),
            )
            if run_context is not None
            else RunContext(
                run_id=uuid.uuid4().hex,
                session_key=session_key,
                channel=origin_channel,
                chat_id=origin_chat_id,
                user_id=origin_chat_id or "system",
                agent_id="default",
                session_id=session_key,
                memory_scope=scope_key,
                memory_policy=dict(getattr(self, "memory_policy", {})),
            )
        )
        context_mode = self._context_mode(run_context)
        (
            initial_messages,
            context_sources,
            context_candidates,
        ) = self.context.build_messages_with_candidates(
            history=(
                []
                if context_mode == "light"
                else session.get_history(max_messages=self.memory_window)
            ),
            current_message=msg.content,
            channel=origin_channel,
            chat_id=origin_chat_id,
            max_context_tokens=self.max_context_tokens,
            scope_key=scope_key,
            skill_names=list(run_context.skill_names),
            skill_refs=list(run_context.skill_refs),
            context_timestamp=run_context.context_timestamp,
            context_mode=context_mode,
        )
        run_instructions = self._apply_run_instructions(initial_messages, run_context)
        if run_instructions:
            instruction_source = source_entry(
                source_kind="run_instruction",
                source_id="run:execution-policy",
                content=run_instructions,
                classification="internal",
                authority="runtime",
                freshness="run_snapshot",
                priority=100,
                included_reason="run_execution_contract",
            )
            context_sources.append(instruction_source)
            context_candidates.append(
                context_candidate(
                    candidate_id="system:run-instruction",
                    target="system",
                    content=run_instructions,
                    source_keys=[("run_instruction", "run:execution-policy")],
                    priority=100,
                    required=True,
                    order=len(context_candidates),
                    separator="\n\n",
                )
            )
        run_context = replace(
            run_context,
            context_sources=tuple(context_sources),
            context_candidates=tuple(context_candidates),
            context_initial_message_count=len(initial_messages),
            context_budget_tokens=self.max_context_tokens,
            context_budget_strategy=(
                "priority_budget_v1" if self.max_context_tokens else "unbounded_v1"
            ),
        )
        final_content, _, _, last_response = await self._run_agent_loop(
            initial_messages,
            run_context=run_context,
        )

        if final_content is None:
            final_content = "Background task completed."

        session.add_message("user", f"[System: {msg.sender_id}] {msg.content}")
        usage_kw: dict[str, Any] = {}
        if last_response and last_response.usage:
            usage_kw["usage"] = dict(last_response.usage)
        session.add_message("assistant", final_content, **usage_kw)
        await asyncio.to_thread(self.sessions.save, session)

        return OutboundMessage(
            channel=origin_channel, chat_id=origin_chat_id, content=final_content
        )

    async def process_direct(
        self,
        content: str,
        session_key: str = "cli:direct",
        channel: str = "cli",
        chat_id: str = "direct",
        sender_id: str = "user",
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        stream_callback: Callable[[str], Awaitable[None]] | None = None,
        execution_stream_callback: Callable[[str, dict], Awaitable[None]] | None = None,
        check_abort_requested: Callable[[str], bool] | None = None,
        run_context: RunContext | None = None,
    ) -> str | None:
        """
        Process a message directly (for CLI or cron usage).

        Args:
            content: The message content.
            session_key: Session identifier (overrides channel:chat_id for session lookup).
            channel: Source channel (for tool context routing).
            chat_id: Source chat ID (for tool context routing).
            stream_callback: If set, called with each content delta when provider supports streaming.
            execution_stream_callback: Receives structured events persisted by the Run runtime.
            check_abort_requested: If set, run can be aborted (e.g. chat.abort); when aborted returns None.

        Returns:
            The agent's response text, or None if run was aborted.
        """
        await self.connect_capability_connectors()
        msg = InboundMessage(
            channel=channel,
            sender_id=sender_id,
            chat_id=chat_id,
            content=content,
            media=media or [],
            metadata=metadata or {},
        )

        response = await self._process_message(
            msg,
            session_key=session_key,
            stream_callback=stream_callback,
            execution_stream_callback=execution_stream_callback,
            check_abort_requested=check_abort_requested,
            run_context=run_context,
        )
        return response.content if response else None
