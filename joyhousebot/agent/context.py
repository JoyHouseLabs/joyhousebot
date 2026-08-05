"""Context builder for assembling agent prompts."""

import base64
import mimetypes
from pathlib import Path
from typing import Any

from loguru import logger

from joyhousebot.agent.memory import MemoryStore
from joyhousebot.agent.memory_policy import EffectiveMemoryPolicy
from joyhousebot.agent.skills import SkillsLoader
from joyhousebot.domain.agents import AgentRevision


class ContextBuilder:
    """
    Builds the context (system prompt + messages) for the agent.

    Assembles a published Agent revision, memory, skills, and conversation
    history into a coherent prompt for the LLM.
    """

    def __init__(
        self,
        scratch_root: Path,
        runtime_store: Any,
        agent_revision: AgentRevision | None = None,
    ):
        self.scratch_root = scratch_root
        self.runtime_store = runtime_store
        self.agent_revision = agent_revision
        self.memory_policy = EffectiveMemoryPolicy.from_dict(
            getattr(agent_revision, "memory_policy", None)
        )
        self.memory = MemoryStore(runtime_store)
        self.skills = SkillsLoader(runtime_store)

    def build_system_prompt(
        self,
        skill_names: list[str] | None = None,
        scope_key: str | None = None,
        skill_refs: list[dict[str, str]] | None = None,
    ) -> str:
        """
        Build the system prompt from bootstrap files, memory, and skills.

        Args:
            skill_names: Optional list of skills to include.
            scope_key: Durable per-session/per-user memory scope; otherwise shared memory.

        Returns:
            Complete system prompt.
        """
        parts = []

        parts.append(self._get_identity())
        profile = self._get_agent_profile()
        if profile:
            parts.append(profile)

        # Durable memory documents are resolved from the shared runtime store.
        memory = self._get_memory_context(scope_key=scope_key)
        if memory:
            parts.append(f"# Memory\n\n{memory}")

        # Skills are coordinator-selected prompt policies, not model-callable tools.
        enabled_skill_names = self._get_enabled_skill_names()
        always_skills = self.skills.get_always_skills(allowed_names=enabled_skill_names)
        selected = list(dict.fromkeys([*always_skills, *(skill_names or [])]))
        if enabled_skill_names is not None:
            selected = [name for name in selected if name in enabled_skill_names]
        pinned_skill_versions = {
            str(item.get("capability_id") or "").removeprefix("skill."): str(item.get("version") or "")
            for item in (skill_refs or [])
            if str(item.get("capability_id") or "").startswith("skill.")
            and str(item.get("version") or "")
        }
        available = {
            item["name"] for item in self.skills.list_skills(filter_unavailable=True)
        }
        available.update(
            name for name, version in pinned_skill_versions.items()
            if self.skills.load_skill(name, version) is not None
        )
        selected = [name for name in selected if name in available]
        if selected:
            logger.debug(f"Building context: selected skills={selected}")
            content = self.skills.load_skills_for_context(
                selected, versions=pinned_skill_versions
            )
            if content:
                parts.append(f"# Active Skills\n\n{content}")

        skills_summary = self.skills.build_skills_summary(allowed_names=enabled_skill_names)
        if skills_summary:
            all_skills = self.skills.list_skills(
                filter_unavailable=False, allowed_names=enabled_skill_names
            )
            logger.debug(
                f"Building context: skills summary for {len(all_skills)} skills (names: {[s['name'] for s in all_skills]})"
            )
            parts.append(f"""# Skills

The coordinator has bound the applicable full skill instructions under Active Skills.
The catalog below is discovery metadata only; do not attempt to read host paths or invoke skills.

{skills_summary}""")

        return "\n\n---\n\n".join(parts)

    def _get_memory_context(self, scope_key: str | None = None) -> str:
        """Resolve scoped long-term memory and optional recent daily logs."""
        if not self.memory_policy.can_read_context:
            return ""
        memory_first = False
        include_daily = False
        try:
            from joyhousebot.config.access import get_config

            config = get_config()
            retrieval = getattr(getattr(config, "tools", None), "retrieval", None)
            if retrieval is not None:
                memory_first = getattr(retrieval, "memory_first", False)
                include_daily = getattr(retrieval, "memory_include_daily_in_context", False)
        except Exception:
            pass
        store = MemoryStore(self.runtime_store, scope_key=scope_key) if scope_key else self.memory
        sections: list[str] = []
        if self.memory_policy.layer_enabled("profile", "read"):
            profile = store.read_profile()
            if profile:
                sections.append(f"## User Profile\n{profile}")
        if self.memory_policy.layer_enabled("long_term", "read"):
            long_term = store.read_long_term()
            if long_term:
                sections.append(f"## Long-term Memory\n{long_term}")
        memory = "\n\n".join(sections)
        if include_daily and self.memory_policy.layer_enabled("episodic", "read"):
            daily = store.read_daily_logs_today_yesterday()
            if daily:
                memory = (
                    (memory + "\n\n## Recent daily log (today + yesterday)\n\n" + daily)
                    if memory
                    else ("## Recent daily log (today + yesterday)\n\n" + daily)
                )
        if memory and memory_first:
            memory = (
                memory
                + '\n\nWhen answering, consider consulting memory first: read memory/.abstract or use retrieve(scope="memory", query=...) before searching the knowledge base.'
            )
        return memory

    def _get_enabled_skill_names(self) -> set[str] | None:
        """Get set of enabled skill names from config (None = all enabled)."""
        try:
            from joyhousebot.config.access import get_config

            config = get_config()
            entries = (
                getattr(config, "skills", None) and getattr(config.skills, "entries", None) or {}
            )
            if not entries:
                return None
            return {k for k, v in entries.items() if getattr(v, "enabled", True)}
        except Exception:
            return None

    def _get_identity(self) -> str:
        """Get the core identity section."""
        import time as _time
        from datetime import datetime

        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = _time.strftime("%Z") or "UTC"
        memory_guidance = (
            "- Durable memory is enabled for this Agent. Use `memory_get` or `retrieve(scope=\"memory\")` to recall it."
            if self.memory_policy.can_read_context
            else "- Durable personal memory is disabled for this Agent; do not read or write user memory."
        )
        return f"""# joyhousebot 🐈

You are joyhousebot, a helpful AI assistant. You have access to tools that allow you to:
- Read, write, and edit files
- Execute shell commands
- Search the web and fetch web pages
- Send messages to users on chat channels
- Spawn subagents for complex background tasks

## Current Time
{now} ({tz})

## Durable data and workspace
- `memory/MEMORY.md`, `memory/PROFILE.md`, `memory/HISTORY.md` and other `memory/*` paths are virtual, durable, user-scoped database documents.
- Use `read_file`, `write_file`, `memory_get` or `retrieve` for Memory; shell commands cannot inspect virtual Memory documents.
- {memory_guidance}
- Other file paths are isolated scratch space for the current root Run, not shared host files.
- Knowledge is durable and user-scoped. Use `retrieve` and the URL knowledge tool.
- Agent skills are administrator-provided prompt policies selected by the coordinator.

IMPORTANT: When responding to direct questions or conversations, reply directly with your text response.
Only use the 'message' tool when you need to send a message to a specific chat channel (like WhatsApp).
For normal conversation, just respond with text - do not call the message tool.

Always be helpful, accurate, and concise. Report useful execution progress without exposing hidden chain-of-thought.
When remembering something important, follow the Agent memory policy. Personal attributes belong in `memory/PROFILE.md`; durable project facts belong in `memory/MEMORY.md`.
To recall past events, use `memory_get` or `retrieve` against Memory."""

    def _get_agent_profile(self) -> str:
        """Render the immutable database revision selected for this worker."""
        revision = self.agent_revision
        if revision is None:
            return ""
        parts = [
            f"# Agent Profile\n\nAgent ID: {revision.agent_id}",
            f"Revision: {revision.revision_id}",
        ]
        if revision.persona:
            persona = ", ".join(
                f"{key}={value}" for key, value in sorted(revision.persona.items())
            )
            parts.append(f"Persona: {persona}")
        if revision.instructions.strip():
            parts.append(f"## Instructions\n\n{revision.instructions.strip()}")
        if revision.output_policy:
            parts.append(f"Output policy: {revision.output_policy}")
        return "\n\n".join(parts)

    @staticmethod
    def _estimate_message_tokens(msg: dict[str, Any]) -> int:
        """Rough token count for one message (chars / 4 + overhead)."""
        n = 4  # role + structure
        content = msg.get("content")
        if isinstance(content, str):
            n += max(0, len(content)) // 4
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text" and "text" in part:
                    n += max(0, len(str(part["text"]))) // 4
                else:
                    n += 64  # placeholder for image/other
        if msg.get("tool_calls"):
            n += sum(max(0, len(str(t))) // 4 for t in msg.get("tool_calls", []))
        return n

    @classmethod
    def trim_history_by_tokens(
        cls,
        history: list[dict[str, Any]],
        max_tokens: int,
    ) -> list[dict[str, Any]]:
        """Trim history from the front so that total estimated tokens of kept messages <= max_tokens (keep tail)."""
        if max_tokens <= 0 or not history:
            return history
        total = 0
        start = len(history)
        for i in range(len(history) - 1, -1, -1):
            total += cls._estimate_message_tokens(history[i])
            if total > max_tokens:
                start = i + 1
                break
            start = i
        if start <= 0:
            return history
        return history[start:]

    def build_messages(
        self,
        history: list[dict[str, Any]],
        current_message: str,
        skill_names: list[str] | None = None,
        skill_refs: list[dict[str, str]] | None = None,
        media: list[str] | None = None,
        channel: str | None = None,
        chat_id: str | None = None,
        max_context_tokens: int | None = None,
        scope_key: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Build the complete message list for an LLM call.

        Args:
            history: Previous conversation messages.
            current_message: The new user message.
            skill_names: Coordinator-selected skills to bind as full instructions.
            media: Optional list of local file paths for images/media.
            channel: Current channel (telegram, feishu, etc.).
            chat_id: Current chat/user ID.
            max_context_tokens: When set, trim history from front so total history tokens <= this (in addition to memory_window).
            scope_key: When set, use per-session/per-user memory for system prompt.

        Returns:
            List of messages including system prompt.
        """
        messages = []

        # System prompt
        system_prompt = self.build_system_prompt(
            skill_names=skill_names, scope_key=scope_key, skill_refs=skill_refs
        )
        if channel and chat_id:
            system_prompt += f"\n\n## Current Session\nChannel: {channel}\nChat ID: {chat_id}"
        messages.append({"role": "system", "content": system_prompt})

        # History (optionally trimmed by token budget)
        if max_context_tokens is not None and max_context_tokens > 0:
            history = self.trim_history_by_tokens(history, max_context_tokens)
        messages.extend(history)

        # Current message (with optional image attachments)
        user_content = self._build_user_content(current_message, media)
        messages.append({"role": "user", "content": user_content})

        return messages

    def _build_user_content(self, text: str, media: list[str] | None) -> str | list[dict[str, Any]]:
        """Build user message content with optional base64-encoded images."""
        if not media:
            return text

        images = []
        for path in media:
            p = Path(path)
            mime, _ = mimetypes.guess_type(path)
            if not p.is_file() or not mime or not mime.startswith("image/"):
                continue
            b64 = base64.b64encode(p.read_bytes()).decode()
            images.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}})

        if not images:
            return text
        return images + [{"type": "text", "text": text}]

    def add_tool_result(
        self, messages: list[dict[str, Any]], tool_call_id: str, tool_name: str, result: str
    ) -> list[dict[str, Any]]:
        """
        Add a tool result to the message list.

        Args:
            messages: Current message list.
            tool_call_id: ID of the tool call.
            tool_name: Name of the tool.
            result: Tool execution result.

        Returns:
            Updated message list.
        """
        messages.append(
            {"role": "tool", "tool_call_id": tool_call_id, "name": tool_name, "content": result}
        )
        return messages

    def add_assistant_message(
        self,
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
        reasoning_blocks: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Add an assistant message to the message list.

        Args:
            messages: Current message list.
            content: Message content.
            tool_calls: Optional tool calls.
            reasoning_content: Thinking output (Kimi, DeepSeek-R1, etc.).

        Returns:
            Updated message list.
        """
        msg: dict[str, Any] = {"role": "assistant", "content": content or ""}

        if tool_calls:
            msg["tool_calls"] = tool_calls

        # Thinking models reject history without this
        if reasoning_content:
            msg["reasoning_content"] = reasoning_content
        if reasoning_blocks:
            msg["reasoning_blocks"] = reasoning_blocks

        messages.append(msg)
        return messages
