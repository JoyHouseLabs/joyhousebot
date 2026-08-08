"""Context builder for assembling agent prompts."""

import base64
import mimetypes
from pathlib import Path
from typing import Any

from loguru import logger

from joyhousebot.agent.context_budget import allocate_context, context_candidate
from joyhousebot.agent.context_manifest import source_entry
from joyhousebot.agent.context_media import media_sources
from joyhousebot.agent.context_memory import build_memory_context
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
        context_timestamp: str | None = None,
    ) -> str:
        """
        Build the system prompt from bootstrap files, memory, and skills.

        Args:
            skill_names: Optional list of skills to include.
            scope_key: Durable per-session/per-user memory scope; otherwise shared memory.

        Returns:
            Complete system prompt.
        """
        prompt, _sources = self.build_system_prompt_with_sources(
            skill_names=skill_names,
            scope_key=scope_key,
            skill_refs=skill_refs,
            context_timestamp=context_timestamp,
        )
        return prompt

    def build_system_prompt_with_sources(
        self,
        skill_names: list[str] | None = None,
        scope_key: str | None = None,
        skill_refs: list[dict[str, str]] | None = None,
        context_timestamp: str | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Build the system prompt and content-free provenance descriptors."""
        prompt, sources, _candidates = self._build_system_context(
            skill_names=skill_names,
            scope_key=scope_key,
            skill_refs=skill_refs,
            context_timestamp=context_timestamp,
        )
        return prompt, sources

    def _build_system_context(
        self,
        skill_names: list[str] | None = None,
        scope_key: str | None = None,
        skill_refs: list[dict[str, str]] | None = None,
        context_timestamp: str | None = None,
    ) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
        """Build system text, public descriptors, and private budget candidates."""
        parts: list[str] = []
        sources: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []

        def add_candidate(
            candidate_id: str,
            content: str,
            linked_sources: list[dict[str, Any]],
            *,
            priority: int,
            required: bool,
            separator: str = "\n\n---\n\n",
        ) -> None:
            candidates.append(
                context_candidate(
                    candidate_id=candidate_id,
                    target="system",
                    content=content,
                    source_keys=[
                        (str(item["source_kind"]), str(item["source_id"]))
                        for item in linked_sources
                    ],
                    priority=priority,
                    required=required,
                    order=len(candidates),
                    separator=separator,
                )
            )

        identity = self._get_identity(context_timestamp)
        parts.append(identity)
        identity_source = source_entry(
            source_kind="system_identity",
            source_id="joyhousebot:identity:v1",
            content=identity,
            classification="internal",
            authority="system",
            freshness="request",
            priority=100,
            included_reason="required_system_policy",
        )
        sources.append(identity_source)
        add_candidate(
            "system:identity",
            identity,
            [identity_source],
            priority=100,
            required=True,
            separator="",
        )
        profile = self._get_agent_profile()
        if profile:
            parts.append(profile)
            revision = self.agent_revision
            profile_source = source_entry(
                source_kind="agent_revision",
                source_id=f"agent:{revision.revision_id}" if revision else "agent:default",
                content=profile,
                classification="internal",
                authority="administrator",
                freshness="immutable_revision",
                priority=100,
                included_reason="selected_agent_revision",
            )
            sources.append(profile_source)
            add_candidate(
                "system:agent-revision",
                profile,
                [profile_source],
                priority=100,
                required=True,
            )

        # Durable memory documents are resolved from the shared runtime store.
        memory, memory_sources = self._get_memory_context_with_sources(scope_key=scope_key)
        if memory:
            memory_block = f"# Memory\n\n{memory}"
            parts.append(memory_block)
            sources.extend(memory_sources)
            add_candidate(
                "system:memory",
                memory_block,
                memory_sources,
                priority=min(int(item["priority"]) for item in memory_sources),
                required=False,
            )

        # Skills are coordinator-selected prompt policies, not model-callable tools.
        enabled_skill_names = self._get_enabled_skill_names()
        always_skills = self.skills.get_always_skills(allowed_names=enabled_skill_names)
        selected = list(dict.fromkeys([*always_skills, *(skill_names or [])]))
        requested_skills = list(selected)
        if enabled_skill_names is not None:
            selected = [name for name in selected if name in enabled_skill_names]
        pinned_skill_versions = {
            str(item.get("capability_id") or "").removeprefix("skill."): str(
                item.get("version") or ""
            )
            for item in (skill_refs or [])
            if str(item.get("capability_id") or "").startswith("skill.")
            and str(item.get("version") or "")
        }
        available = {item["name"] for item in self.skills.list_skills(filter_unavailable=True)}
        available.update(
            name
            for name, version in pinned_skill_versions.items()
            if self.skills.load_skill(name, version) is not None
        )
        selected = [name for name in selected if name in available]
        for name in requested_skills:
            if name in selected:
                continue
            sources.append(
                source_entry(
                    source_kind="skill",
                    source_id=f"skill:{name}:unadmitted",
                    content={"name": name},
                    classification="internal",
                    authority="platform",
                    freshness="configuration",
                    priority=90,
                    included=False,
                    excluded_reason="skill_not_admitted",
                )
            )
        if selected:
            logger.debug(f"Building context: selected skills={selected}")
            skill_parts: list[str] = []
            active_skill_sources: list[dict[str, Any]] = []
            for name in selected:
                version = pinned_skill_versions.get(name)
                raw_content = self.skills.load_skill(name, version)
                if not raw_content:
                    continue
                rendered = f"### Skill: {name}\n\n{self.skills._strip_frontmatter(raw_content)}"
                skill_parts.append(rendered)
                skill_source = source_entry(
                    source_kind="skill",
                    source_id=f"skill:{name}:{version or 'published'}",
                    content=rendered,
                    classification="internal",
                    authority="administrator",
                    freshness="immutable_revision" if version else "published_revision",
                    priority=90,
                    included_reason="coordinator_selected_skill",
                )
                sources.append(skill_source)
                active_skill_sources.append(skill_source)
            content = "\n\n---\n\n".join(skill_parts)
            if content:
                active_skills = f"# Active Skills\n\n{content}"
                parts.append(active_skills)
                add_candidate(
                    "system:active-skills",
                    active_skills,
                    active_skill_sources,
                    priority=90,
                    required=True,
                )

        skills_summary = self.skills.build_skills_summary(allowed_names=enabled_skill_names)
        if skills_summary:
            all_skills = self.skills.list_skills(
                filter_unavailable=False, allowed_names=enabled_skill_names
            )
            logger.debug(
                f"Building context: skills summary for {len(all_skills)} skills (names: {[s['name'] for s in all_skills]})"
            )
            catalog = f"""# Skills

The coordinator has bound the applicable full skill instructions under Active Skills.
The catalog below is discovery metadata only; do not attempt to read host paths or invoke skills.

{skills_summary}"""
            parts.append(catalog)
            catalog_source = source_entry(
                source_kind="skill_catalog",
                source_id="skills:published-catalog",
                content=catalog,
                classification="internal",
                authority="platform",
                freshness="published_revision",
                priority=40,
                included_reason="skill_discovery_metadata",
            )
            sources.append(catalog_source)
            add_candidate(
                "system:skill-catalog",
                catalog,
                [catalog_source],
                priority=40,
                required=False,
            )

        return "\n\n---\n\n".join(parts), sources, candidates

    def _get_memory_context(self, scope_key: str | None = None) -> str:
        """Resolve scoped long-term memory and optional recent daily logs."""
        return self._get_memory_context_with_sources(scope_key)[0]

    def _get_memory_context_with_sources(
        self, scope_key: str | None = None
    ) -> tuple[str, list[dict[str, Any]]]:
        """Resolve memory text together with source-level provenance."""
        return build_memory_context(self, scope_key)

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

    def _get_identity(self, context_timestamp: str | None = None) -> str:
        """Get the core identity section."""
        import time as _time
        from datetime import datetime

        instant = datetime.now()
        if context_timestamp:
            try:
                instant = datetime.fromisoformat(context_timestamp).astimezone()
            except ValueError:
                pass
        now = instant.strftime("%Y-%m-%d %H:%M (%A)")
        tz = instant.tzname() or _time.strftime("%Z") or "UTC"
        memory_guidance = (
            '- Durable memory is enabled for this Agent. Use `memory_get` or `retrieve(scope="memory")` to recall it.'
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
            persona = ", ".join(f"{key}={value}" for key, value in sorted(revision.persona.items()))
            parts.append(f"Persona: {persona}")
        if revision.instructions.strip():
            parts.append(f"## Instructions\n\n{revision.instructions.strip()}")
        if revision.output_policy:
            parts.append(f"Output policy: {revision.output_policy}")
        return "\n\n".join(parts)

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
        context_timestamp: str | None = None,
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
            max_context_tokens: Full model-input budget across all admitted context sources.
            scope_key: When set, use per-session/per-user memory for system prompt.

        Returns:
            List of messages including system prompt.
        """
        messages, _sources = self.build_messages_with_sources(
            history=history,
            current_message=current_message,
            skill_names=skill_names,
            skill_refs=skill_refs,
            media=media,
            channel=channel,
            chat_id=chat_id,
            max_context_tokens=max_context_tokens,
            scope_key=scope_key,
            context_timestamp=context_timestamp,
        )
        return messages

    def build_messages_with_sources(
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
        context_timestamp: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Build model messages plus source descriptors without storing source content."""
        messages, sources, _candidates = self.build_messages_with_candidates(
            history=history,
            current_message=current_message,
            skill_names=skill_names,
            skill_refs=skill_refs,
            media=media,
            channel=channel,
            chat_id=chat_id,
            max_context_tokens=max_context_tokens,
            scope_key=scope_key,
            context_timestamp=context_timestamp,
        )
        return messages, sources

    def build_messages_with_candidates(
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
        context_timestamp: str | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """Build messages and retain private candidates for per-Turn reallocation."""
        _system_prompt, sources, candidates = self._build_system_context(
            skill_names=skill_names,
            scope_key=scope_key,
            skill_refs=skill_refs,
            context_timestamp=context_timestamp,
        )
        if channel and chat_id:
            session_context = f"## Current Session\nChannel: {channel}\nChat ID: {chat_id}"
            session_source = source_entry(
                source_kind="session_metadata",
                source_id="session:routing",
                content=session_context,
                classification="internal",
                authority="runtime",
                freshness="request",
                priority=95,
                included_reason="channel_tool_routing",
            )
            sources.append(session_source)
            candidates.append(
                context_candidate(
                    candidate_id="system:session",
                    target="system",
                    content=session_context,
                    source_keys=[("session_metadata", "session:routing")],
                    priority=95,
                    required=True,
                    order=len(candidates),
                    separator="\n\n",
                )
            )

        for index, message in enumerate(history):
            role = str(message.get("role") or "unknown")
            history_source = source_entry(
                source_kind="conversation_history",
                source_id=f"history:{index}",
                content=message,
                classification="confidential",
                authority="user" if role == "user" else "runtime",
                freshness="session",
                priority=55 + min(index, 20),
                included_reason="conversation_window",
                metadata={"role": role, "history_index": index},
            )
            sources.append(history_source)
            candidates.append(
                context_candidate(
                    candidate_id=f"history:{index}",
                    target="message",
                    content=dict(message),
                    source_keys=[("conversation_history", f"history:{index}")],
                    priority=int(history_source["priority"]),
                    required=False,
                    order=10_000 + index,
                )
            )

        user_content = self._build_user_content(current_message, media)
        request_source = source_entry(
            source_kind="current_request",
            source_id="request:current",
            content=current_message,
            classification="confidential",
            authority="user",
            freshness="request",
            priority=100,
            included_reason="current_user_request",
        )
        media_sources = self._media_sources(media)
        sources.append(request_source)
        sources.extend(media_sources)
        linked = [request_source, *(item for item in media_sources if item["included"])]
        candidates.append(
            context_candidate(
                candidate_id="request:current",
                target="message",
                content={"role": "user", "content": user_content},
                source_keys=[(str(item["source_kind"]), str(item["source_id"])) for item in linked],
                priority=100,
                required=True,
                order=20_000,
            )
        )
        prepared = allocate_context(
            base_candidates=candidates,
            base_sources=sources,
            budget_tokens=max_context_tokens,
        )

        return prepared.messages, prepared.entries, candidates

    @staticmethod
    def _media_sources(media: list[str] | None) -> list[dict[str, Any]]:
        return media_sources(media)

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
