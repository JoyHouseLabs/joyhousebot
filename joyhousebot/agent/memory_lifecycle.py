"""MemoryLifecycle responsibilities for the shared Agent engine."""

import json
from datetime import datetime, timezone
from hashlib import sha256
from typing import TYPE_CHECKING

import json_repair
from loguru import logger

from joyhousebot.domain.memory_policy import EffectiveMemoryPolicy
from joyhousebot.runtime.context import (
    RunContext,
)
from joyhousebot.runtime.schema_limits import structured_contract_json
from joyhousebot.services.memory.store import MemoryStore
from joyhousebot.services.memory.writes import MemoryWriteController
from joyhousebot.utils.exceptions import (
    LLMError,
    classify_exception,
    sanitize_error_message,
)

if TYPE_CHECKING:
    pass


class MemoryLifecycleMixin:
    @staticmethod
    def _apply_run_instructions(messages: list[dict], run_context: RunContext) -> str | None:
        instructions: list[str] = []
        if run_context.system_prompt:
            instructions.append(run_context.system_prompt.strip())
        if run_context.output_schema:
            instructions.append(
                "Return only JSON matching this JSON Schema:\n"
                + str(
                    structured_contract_json(
                        run_context.output_schema,
                        label="run output_schema",
                    )
                )
            )
        if (run_context.verification_policy or {}).get("verifiers"):
            instructions.append(
                "Your final answer must pass this completion verification policy:\n"
                + str(
                    structured_contract_json(
                        run_context.verification_policy,
                        label="run verification_policy",
                    )
                )
            )
        if not instructions:
            return None
        extra = "\n\n".join(item for item in instructions if item)
        for message in messages:
            if message.get("role") == "system":
                message["content"] = f"{message.get('content') or ''}\n\n{extra}".strip()
                return extra
        messages.insert(0, {"role": "system", "content": extra})
        return extra

    def _memory_scope(self, session, run_context: RunContext | None) -> str | None:
        scope_key = run_context.memory_scope if run_context is not None else None
        if scope_key is not None or not self.config:
            return scope_key
        retrieval = getattr(getattr(self.config, "tools", None), "retrieval", None)
        mode = getattr(retrieval, "memory_scope", "user") if retrieval else "user"
        if mode == "session":
            return session.key
        if mode == "user":
            return (session.metadata or {}).get("last_memory_scope_key") or session.key
        return scope_key

    def _messages_for_consolidation(
        self, session, *, archive_all: bool
    ) -> tuple[list[dict], int] | None:
        if archive_all:
            logger.info(
                f"Memory consolidation (archive_all): {len(session.messages)} total messages archived"
            )
            return session.messages, 0
        keep_count = self.memory_window // 2
        if len(session.messages) <= keep_count:
            logger.debug(
                f"Session {session.key}: No consolidation needed "
                f"(messages={len(session.messages)}, keep={keep_count})"
            )
            return None
        if len(session.messages) - session.last_consolidated <= 0:
            logger.debug(f"Session {session.key}: No new messages to consolidate")
            return None
        old_messages = session.messages[session.last_consolidated : -keep_count]
        if not old_messages:
            return None
        logger.info(
            f"Memory consolidation started: {len(session.messages)} total, "
            f"{len(old_messages)} new to consolidate, {keep_count} keep"
        )
        return old_messages, keep_count

    @staticmethod
    def _conversation_text(messages: list[dict]) -> str:
        lines = []
        for message in messages:
            if not message.get("content"):
                continue
            tools = (
                f" [tools: {', '.join(message['tools_used'])}]"
                if message.get("tools_used")
                else ""
            )
            lines.append(
                f"[{message.get('timestamp', '?')[:16]}] "
                f"{message['role'].upper()}{tools}: {message['content']}"
            )
        return "\n".join(lines)

    @staticmethod
    def _memory_body(value: str) -> str:
        if value.startswith("<!-- updated_at=") and " -->" in value:
            return value.split(" -->", 1)[-1].lstrip("\n")
        return value

    @staticmethod
    def _memory_date(timestamp: str) -> str:
        try:
            return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            return datetime.now(timezone.utc).date().isoformat()

    @staticmethod
    def _flush_settings() -> tuple[bool, str, str]:
        try:
            from joyhousebot.config.access import get_config

            retrieval = getattr(getattr(get_config(), "tools", None), "retrieval", None)
            if retrieval is None:
                return False, "", ""
            return (
                bool(getattr(retrieval, "memory_flush_before_consolidation", False)),
                getattr(retrieval, "memory_flush_system_prompt", "")
                or "Session nearing compaction. Output only valid JSON.",
                getattr(retrieval, "memory_flush_prompt", "")
                or (
                    "Write any lasting notes: return JSON with optional keys "
                    "daily_log_entry and memory_additions. If nothing to store, return {}."
                ),
            )
        except Exception:
            return False, "", ""

    def _apply_flush_result(
        self,
        result: dict,
        *,
        memory: MemoryStore,
        writer: MemoryWriteController,
        memory_date: str,
        memory_timestamp: str,
        source_fingerprint: str,
    ) -> str:
        daily = result.get("daily_log_entry")
        if isinstance(daily, str) and daily.strip():
            writer.append(
                f"{memory_date}.md",
                daily.strip(),
                source_kind="consolidation.flush.daily",
                source_fingerprint=source_fingerprint,
            )
        additions = result.get("memory_additions")
        if not isinstance(additions, str) or not additions.strip():
            return self._memory_body(memory.read_long_term())
        body = self._memory_body(memory.read_long_term())
        writer.replace(
            "MEMORY.md",
            f"<!-- updated_at={memory_timestamp} -->\n{body.rstrip()}\n\n{additions.strip()}",
            source_kind="consolidation.flush.long_term",
            source_fingerprint=source_fingerprint,
        )
        return self._memory_body(memory.read_long_term())

    async def _flush_before_consolidation(
        self,
        *,
        memory: MemoryStore,
        writer: MemoryWriteController,
        conversation: str,
        memory_date: str,
        memory_timestamp: str,
        source_fingerprint: str,
        policy: EffectiveMemoryPolicy,
        current_memory: str,
    ) -> str:
        enabled, system, prompt = self._flush_settings()
        if not enabled or not prompt or policy.write_mode != "direct":
            return current_memory
        try:
            response = await self.provider.chat(
                messages=[
                    {"role": "system", "content": system},
                    {
                        "role": "user",
                        "content": f"{prompt}\n\n## Recent conversation\n{conversation[:4000]}",
                    },
                ],
                model=self.model,
            )
            text = (response.content or "").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            result = json_repair.loads(text) if text else {}
            if not isinstance(result, dict):
                return current_memory
            return self._apply_flush_result(
                result,
                memory=memory,
                writer=writer,
                memory_date=memory_date,
                memory_timestamp=memory_timestamp,
                source_fingerprint=source_fingerprint,
            )
        except Exception as exc:
            logger.debug(f"Memory flush before consolidation skipped: {exc}")
            return current_memory

    @staticmethod
    def _consolidation_prompt(
        *, conversation: str, current_memory: str, raw_profile: str
    ) -> str:
        return f"""You are a memory consolidation agent. Process this conversation and return a JSON object with these keys:

1. "history_entry": A paragraph (2-5 sentences) summarizing the key events/decisions/topics. Start with a timestamp like [YYYY-MM-DD HH:MM]. Include enough detail to be useful when found by search later. This will be appended to HISTORY.md and to memory/YYYY-MM-DD.md (daily log).

2. "memory_update": The updated long-term memory content. Add any new facts: user location, preferences, personal info, habits, project context, technical decisions, tools/services used. If nothing new, return the existing content unchanged. If any existing long-term fact has been superseded or invalidated by this conversation, do not silently remove it; keep or briefly mention the old conclusion and add a clear new conclusion that explicitly supersedes it (e.g. "Previously: X. Now: Y." or "Supersedes: …"). You may tag items with [P0] (permanent), [P1] (e.g. 90-day), [P2] (e.g. 30-day) for lifecycle.

3. "profile_update" (optional): Updated personal attributes and stable user preferences for PROFILE.md. Omit when no profile information was learned or when that layer is disabled.

4. "l0_update" (optional): If you have a concise summary of active topics and retrieval hints (about 100–300 tokens), set this to the content for memory/.abstract. Omit the key or set to null if not needed.

## Current Long-term Memory
{current_memory or "(empty)"}

## Current User Profile
{raw_profile or "(empty)"}

## Conversation to Process
{conversation}

Respond with ONLY valid JSON, no markdown fences."""

    async def _request_consolidation(self, prompt: str) -> dict | None:
        response = await self.provider.chat(
            messages=[
                {
                    "role": "system",
                    "content": "You are a memory consolidation agent. Respond only with valid JSON.",
                },
                {"role": "user", "content": prompt},
            ],
            model=self.model,
        )
        text = (response.content or "").strip()
        if not text:
            logger.warning("Memory consolidation: LLM returned empty response, skipping")
            return None
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        result = json_repair.loads(text)
        if not isinstance(result, dict):
            logger.warning(f"Memory consolidation: unexpected response: {text[:200]}")
            return None
        return result

    @staticmethod
    def _history_max_entries() -> int:
        try:
            from joyhousebot.config.access import get_config

            retrieval = getattr(getattr(get_config(), "tools", None), "retrieval", None)
            return int(getattr(retrieval, "history_max_entries", 0) or 0)
        except Exception:
            return 0

    def _write_consolidation(
        self,
        result: dict,
        *,
        writer: MemoryWriteController,
        policy: EffectiveMemoryPolicy,
        memory_date: str,
        memory_timestamp: str,
        source_fingerprint: str,
        current_memory: str,
        raw_profile: str,
    ) -> None:
        entry = result.get("history_entry")
        if entry and policy.layer_enabled("episodic", "write"):
            writer.append(
                "HISTORY.md",
                str(entry),
                source_kind="consolidation.history",
                source_fingerprint=source_fingerprint,
                max_entries=self._history_max_entries(),
                fact_type="episode",
            )
            writer.append(
                f"{memory_date}.md",
                str(entry),
                source_kind="consolidation.daily",
                source_fingerprint=source_fingerprint,
                fact_type="episode",
            )
        update = result.get("memory_update")
        if update and update != current_memory and policy.layer_enabled("long_term", "write"):
            writer.replace(
                "MEMORY.md",
                f"<!-- updated_at={memory_timestamp} -->\n{update}",
                source_kind="consolidation.long_term",
                source_fingerprint=source_fingerprint,
                fact_type="long_term_fact",
            )
        profile = result.get("profile_update")
        if profile and profile != raw_profile and policy.layer_enabled("profile", "write"):
            writer.replace(
                "PROFILE.md",
                f"<!-- updated_at={memory_timestamp} -->\n{profile}",
                source_kind="consolidation.profile",
                source_fingerprint=source_fingerprint,
                fact_type="profile_attribute",
            )
        abstract = result.get("l0_update")
        if isinstance(abstract, str) and abstract.strip() and policy.layer_enabled(
            "episodic", "write"
        ):
            writer.replace(
                ".abstract",
                abstract.strip(),
                source_kind="consolidation.abstract",
                source_fingerprint=source_fingerprint,
                fact_type="memory_index",
            )

    async def _consolidate_memory(
        self,
        session,
        archive_all: bool = False,
        run_context: RunContext | None = None,
    ) -> None:
        policy = EffectiveMemoryPolicy.from_dict(getattr(self, "memory_policy", None))
        if not policy.can_consolidate:
            logger.debug("Memory consolidation skipped: disabled by Agent memory policy")
            return
        if run_context is None:
            raise RuntimeError("Memory consolidation requires an authenticated RunContext")
        selection = self._messages_for_consolidation(session, archive_all=archive_all)
        if selection is None:
            return
        old_messages, keep_count = selection
        conversation = self._conversation_text(old_messages)
        fingerprint = sha256(f"{session.key}\0{conversation}".encode()).hexdigest()
        memory = MemoryStore(
            self.runtime_store, scope_key=self._memory_scope(session, run_context)
        )
        timestamp = run_context.context_timestamp or datetime.now(timezone.utc).isoformat()
        writer = MemoryWriteController(
            self.runtime_store,
            scope_key=memory.scope_key,
            policy=policy,
            context=run_context,
        )
        if policy.write_mode == "direct" and policy.layer_enabled("episodic", "write"):
            memory.ensure_memory_structure()
        raw_memory = memory.read_long_term() if policy.layer_enabled("long_term", "read") else ""
        raw_profile = memory.read_profile() if policy.layer_enabled("profile", "read") else ""
        current_memory = await self._flush_before_consolidation(
            memory=memory,
            writer=writer,
            conversation=conversation,
            memory_date=self._memory_date(timestamp),
            memory_timestamp=timestamp,
            source_fingerprint=fingerprint,
            policy=policy,
            current_memory=self._memory_body(raw_memory),
        )
        try:
            result = await self._request_consolidation(
                self._consolidation_prompt(
                    conversation=conversation,
                    current_memory=current_memory,
                    raw_profile=raw_profile,
                )
            )
            if result is None:
                return
            self._write_consolidation(
                result,
                writer=writer,
                policy=policy,
                memory_date=self._memory_date(timestamp),
                memory_timestamp=timestamp,
                source_fingerprint=fingerprint,
                current_memory=current_memory,
                raw_profile=raw_profile,
            )
            session.last_consolidated = 0 if archive_all else len(session.messages) - keep_count
            logger.info(
                f"Memory consolidation done: {len(session.messages)} messages, "
                f"last_consolidated={session.last_consolidated}"
            )
        except json.JSONDecodeError as exc:
            logger.error(f"Memory consolidation JSON parse error: {exc}")
        except LLMError as exc:
            logger.error(f"Memory consolidation LLM error [{exc.code}]: {exc.message}")
        except ConnectionError as exc:
            logger.error(
                f"Memory consolidation connection error: {sanitize_error_message(str(exc))}"
            )
        except Exception as exc:
            code, _category, _ = classify_exception(exc)
            logger.error(
                f"Memory consolidation failed [{code}]: {sanitize_error_message(str(exc))}"
            )
