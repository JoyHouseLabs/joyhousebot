"""MemoryLifecycle responsibilities for the shared Agent engine."""

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import json_repair
from loguru import logger

from joyhousebot.agent.memory import MemoryStore
from joyhousebot.agent.memory_policy import EffectiveMemoryPolicy
from joyhousebot.runtime.context import (
    RunContext,
)
from joyhousebot.utils.exceptions import (
    LLMError,
    classify_exception,
    sanitize_error_message,
)

if TYPE_CHECKING:
    pass


class MemoryLifecycleMixin:
    @staticmethod
    def _apply_run_instructions(messages: list[dict], run_context: RunContext) -> None:
        instructions: list[str] = []
        if run_context.system_prompt:
            instructions.append(run_context.system_prompt.strip())
        if run_context.output_schema:
            instructions.append(
                "Return only JSON matching this JSON Schema:\n"
                + json.dumps(run_context.output_schema, ensure_ascii=False)[:20000]
            )
        if not instructions:
            return
        extra = "\n\n".join(item for item in instructions if item)
        for message in messages:
            if message.get("role") == "system":
                message["content"] = f"{message.get('content') or ''}\n\n{extra}".strip()
                return
        messages.insert(0, {"role": "system", "content": extra})

    async def _consolidate_memory(self, session, archive_all: bool = False) -> None:
        """Consolidate old messages into MEMORY.md + HISTORY.md.

        Args:
            archive_all: If True, clear all messages and reset session (for /new command).
                       If False, only write to files without modifying session.
        """
        policy = EffectiveMemoryPolicy.from_dict(getattr(self, "memory_policy", None))
        if not policy.can_consolidate:
            logger.debug("Memory consolidation skipped: disabled by Agent memory policy")
            return

        scope_key = None
        if self.config:
            retrieval = getattr(getattr(self.config, "tools", None), "retrieval", None)
            if retrieval:
                mode = getattr(retrieval, "memory_scope", "user") or "user"
                if mode == "session":
                    scope_key = session.key
                elif mode == "user":
                    scope_key = (session.metadata or {}).get("last_memory_scope_key") or session.key
        memory = MemoryStore(self.runtime_store, scope_key=scope_key)
        if policy.layer_enabled("episodic", "write"):
            memory.ensure_memory_structure()

        if archive_all:
            old_messages = session.messages
            keep_count = 0
            logger.info(
                f"Memory consolidation (archive_all): {len(session.messages)} total messages archived"
            )
        else:
            keep_count = self.memory_window // 2
            if len(session.messages) <= keep_count:
                logger.debug(
                    f"Session {session.key}: No consolidation needed (messages={len(session.messages)}, keep={keep_count})"
                )
                return

            messages_to_process = len(session.messages) - session.last_consolidated
            if messages_to_process <= 0:
                logger.debug(
                    f"Session {session.key}: No new messages to consolidate (last_consolidated={session.last_consolidated}, total={len(session.messages)})"
                )
                return

            old_messages = session.messages[session.last_consolidated : -keep_count]
            if not old_messages:
                return
            logger.info(
                f"Memory consolidation started: {len(session.messages)} total, {len(old_messages)} new to consolidate, {keep_count} keep"
            )

        lines = []
        for m in old_messages:
            if not m.get("content"):
                continue
            tools = f" [tools: {', '.join(m['tools_used'])}]" if m.get("tools_used") else ""
            lines.append(
                f"[{m.get('timestamp', '?')[:16]}] {m['role'].upper()}{tools}: {m['content']}"
            )
        conversation = "\n".join(lines)
        raw_memory = (
            memory.read_long_term() if policy.layer_enabled("long_term", "read") else ""
        )
        raw_profile = memory.read_profile() if policy.layer_enabled("profile", "read") else ""
        # Strip leading updated_at comment for prompt so LLM sees only body and does not echo it
        if raw_memory.startswith("<!-- updated_at=") and " -->" in raw_memory:
            current_memory = raw_memory.split(" -->", 1)[-1].lstrip("\n")
        else:
            current_memory = raw_memory

        # Optionally capture durable notes before context consolidation.
        flush_enabled = False
        flush_system = ""
        flush_prompt = ""
        try:
            from joyhousebot.config.access import get_config

            cfg = get_config()
            retrieval = getattr(getattr(cfg, "tools", None), "retrieval", None)
            if retrieval is not None:
                flush_enabled = getattr(retrieval, "memory_flush_before_consolidation", False)
                flush_system = (
                    getattr(retrieval, "memory_flush_system_prompt", "")
                    or "Session nearing compaction. Output only valid JSON."
                )
                flush_prompt = (
                    getattr(retrieval, "memory_flush_prompt", "")
                    or "Write any lasting notes: return JSON with optional keys daily_log_entry and memory_additions. If nothing to store, return {}."
                )
        except Exception:
            pass
        if flush_enabled and flush_prompt:
            try:
                flush_user = f"{flush_prompt}\n\n## Recent conversation\n{conversation[:4000]}"
                flush_response = await self.provider.chat(
                    messages=[
                        {"role": "system", "content": flush_system},
                        {"role": "user", "content": flush_user},
                    ],
                    model=self.model,
                )
                flush_text = (flush_response.content or "").strip()
                if flush_text.startswith("```"):
                    flush_text = flush_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                if flush_text:
                    flush_result = json_repair.loads(flush_text)
                    if isinstance(flush_result, dict):
                        if (
                            isinstance(flush_result.get("daily_log_entry"), str)
                            and flush_result["daily_log_entry"].strip()
                        ):
                            date_str = datetime.now(timezone.utc).date().isoformat()
                            memory.append_l2_daily(
                                date_str, flush_result["daily_log_entry"].strip()
                            )
                        if (
                            isinstance(flush_result.get("memory_additions"), str)
                            and flush_result["memory_additions"].strip()
                        ):
                            raw_memory = memory.read_long_term()
                            body = (
                                raw_memory.split(" -->", 1)[-1].lstrip("\n")
                                if (
                                    raw_memory.startswith("<!-- updated_at=")
                                    and " -->" in raw_memory
                                )
                                else raw_memory
                            )
                            memory.write_long_term(
                                body.rstrip() + "\n\n" + flush_result["memory_additions"].strip(),
                                updated_at=datetime.now(timezone.utc).isoformat(),
                            )
                            raw_memory = memory.read_long_term()
                            current_memory = (
                                raw_memory.split(" -->", 1)[-1].lstrip("\n")
                                if (
                                    raw_memory.startswith("<!-- updated_at=")
                                    and " -->" in raw_memory
                                )
                                else raw_memory
                            )
            except Exception as e:
                logger.debug(f"Memory flush before consolidation skipped: {e}")

        prompt = f"""You are a memory consolidation agent. Process this conversation and return a JSON object with these keys:

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

        try:
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
                return
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            result = json_repair.loads(text)
            if not isinstance(result, dict):
                logger.warning(
                    f"Memory consolidation: unexpected response type, skipping. Response: {text[:200]}"
                )
                return

            history_max_entries = 0
            try:
                from joyhousebot.config.access import get_config

                cfg = get_config()
                retrieval = getattr(getattr(cfg, "tools", None), "retrieval", None)
                if retrieval is not None:
                    history_max_entries = getattr(retrieval, "history_max_entries", 0) or 0
            except Exception:
                pass

            if (entry := result.get("history_entry")) and policy.layer_enabled("episodic", "write"):
                memory.append_history(entry, max_entries=history_max_entries)
                date_str = datetime.now(timezone.utc).date().isoformat()
                memory.append_l2_daily(date_str, entry)
            if (update := result.get("memory_update")) and policy.layer_enabled("long_term", "write"):
                if update != current_memory:
                    memory.write_long_term(
                        update, updated_at=datetime.now(timezone.utc).isoformat()
                    )
            if (profile_update := result.get("profile_update")) and policy.layer_enabled("profile", "write"):
                if profile_update != raw_profile:
                    memory.write_profile(
                        str(profile_update), updated_at=datetime.now(timezone.utc).isoformat()
                    )
            if (l0_update := result.get("l0_update")) and policy.layer_enabled(
                "episodic", "write"
            ):
                if isinstance(l0_update, str) and l0_update.strip():
                    memory.update_l0_abstract(l0_update.strip())

            if archive_all:
                session.last_consolidated = 0
            else:
                session.last_consolidated = len(session.messages) - keep_count
            logger.info(
                f"Memory consolidation done: {len(session.messages)} messages, last_consolidated={session.last_consolidated}"
            )
        except json.JSONDecodeError as e:
            logger.error(f"Memory consolidation JSON parse error: {e}")
        except LLMError as e:
            logger.error(f"Memory consolidation LLM error [{e.code}]: {e.message}")
        except ConnectionError as e:
            logger.error(f"Memory consolidation connection error: {sanitize_error_message(str(e))}")
        except Exception as e:
            code, category, _ = classify_exception(e)
            logger.error(f"Memory consolidation failed [{code}]: {sanitize_error_message(str(e))}")
