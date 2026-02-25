"""Context builder for assembling agent prompts."""

import base64
import mimetypes
import platform
from pathlib import Path
from typing import Any

from loguru import logger

from joyhousebot.agent.memory import MemoryStore
from joyhousebot.agent.skills import SkillsLoader


class ContextBuilder:
    """
    Builds the context (system prompt + messages) for the agent.
    
    Assembles bootstrap files, memory, skills, and conversation history
    into a coherent prompt for the LLM.
    """
    
    BOOTSTRAP_FILES = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "IDENTITY.md"]
    
    def __init__(self, workspace: Path):
        self.workspace = workspace
        self.memory = MemoryStore(workspace)
        self.skills = SkillsLoader(workspace)
    
    def build_system_prompt(self, skill_names: list[str] | None = None, scope_key: str | None = None) -> str:
        """
        Build the system prompt from bootstrap files, memory, and skills.
        
        Args:
            skill_names: Optional list of skills to include.
            scope_key: When set, use per-session/per-user memory (memory/<scope_key>/); else shared memory.
        
        Returns:
            Complete system prompt.
        """
        parts = []
        
        # Core identity
        parts.append(self._get_identity())
        
        # Bootstrap files
        bootstrap = self._load_bootstrap_files()
        if bootstrap:
            parts.append(bootstrap)
        
        # Memory context: legacy = MEMORY.md only; with memory_use_l0 = L0 + MEMORY.md; scope_key => scoped memory
        memory = self._get_memory_context(scope_key=scope_key)
        if memory:
            parts.append(f"# Memory\n\n{memory}")
        
        # Skills - progressive loading (respect config.skills.entries enabled)
        enabled_skill_names = self._get_enabled_skill_names()
        # 1. Always-loaded skills: include full content
        always_skills = self.skills.get_always_skills(allowed_names=enabled_skill_names)
        if always_skills:
            logger.debug(f"Building context: always-loaded skills={always_skills}")
            always_content = self.skills.load_skills_for_context(always_skills)
            if always_content:
                parts.append(f"# Active Skills\n\n{always_content}")
        
        # 2. Available skills: only show summary (agent uses read_file to load)
        skills_summary = self.skills.build_skills_summary(allowed_names=enabled_skill_names)
        if skills_summary:
            all_skills = self.skills.list_skills(filter_unavailable=False, allowed_names=enabled_skill_names)
            logger.debug(f"Building context: skills summary for {len(all_skills)} skills (names: {[s['name'] for s in all_skills]})")
            parts.append(f"""# Skills

The following skills extend your capabilities. To use a skill, read its SKILL.md file using the read_file tool.
Skills with available="false" need dependencies installed first - you can try installing them with apt/brew.

{skills_summary}""")

        # Installed Apps and Plugin Tools (so agent knows what app_id / plugin_invoke tool_name to use)
        installed_apps = self._get_installed_apps_for_context()
        if installed_apps:
            lines = [
                "Use open_app with one of these app_id values; do not guess.",
                "",
            ]
            for a in installed_apps:
                app_id = (a.get("app_id") or "").strip()
                name = (a.get("name") or app_id).strip()
                route = (a.get("route") or "").strip()
                if app_id:
                    lines.append(f"- app_id: {app_id} (name: {name}, route: {route})")
            if len(lines) > 2:
                parts.append("# Installed Apps\n\n" + "\n".join(lines))

        plugin_tools = self._get_plugin_tool_names_for_context()
        if plugin_tools:
            parts.append(
                "# Plugin Tools\n\n"
                "Use plugin_invoke with these tool names; do not guess. "
                "Available: " + ", ".join(plugin_tools)
            )

        return "\n\n---\n\n".join(parts)

    def _get_memory_context(self, scope_key: str | None = None) -> str:
        """Resolve memory block: config.memory_use_l0 -> L0 + MEMORY.md; else MEMORY.md only.
        Optionally add today+yesterday daily logs (memory_include_daily_in_context) and memory_first hint.
        When scope_key is set, use MemoryStore(workspace, scope_key) for per-session/per-user memory."""
        use_l0 = False
        memory_first = False
        include_daily = False
        try:
            from joyhousebot.config.access import get_config
            config = get_config()
            retrieval = getattr(getattr(config, "tools", None), "retrieval", None)
            if retrieval is not None:
                use_l0 = getattr(retrieval, "memory_use_l0", False)
                memory_first = getattr(retrieval, "memory_first", False)
                include_daily = getattr(retrieval, "memory_include_daily_in_context", False)
        except Exception:
            pass
        store = MemoryStore(self.workspace, scope_key=scope_key) if scope_key else self.memory
        if use_l0:
            memory = store.get_memory_context_with_l0()
        else:
            memory = store.get_memory_context()
        if include_daily:
            daily = store.read_daily_logs_today_yesterday()
            if daily:
                memory = (memory + "\n\n## Recent daily log (today + yesterday)\n\n" + daily) if memory else ("## Recent daily log (today + yesterday)\n\n" + daily)
        if memory and memory_first:
            memory = memory + "\n\nWhen answering, consider consulting memory first: read memory/.abstract or use retrieve(scope=\"memory\", query=...) before searching the knowledge base."
        return memory

    def _get_enabled_skill_names(self) -> set[str] | None:
        """Get set of enabled skill names from config (None = all enabled)."""
        try:
            from joyhousebot.config.access import get_config
            config = get_config()
            entries = getattr(config, "skills", None) and getattr(config.skills, "entries", None) or {}
            if not entries:
                return None
            return {k for k, v in entries.items() if getattr(v, "enabled", True)}
        except Exception:
            return None

    def _get_installed_apps_for_context(self) -> list[dict[str, Any]]:
        """Get enabled plugin apps for agent context (app_id, name, route)."""
        try:
            from joyhousebot.plugins.discovery import get_installed_apps_for_agent
            from joyhousebot.config.access import get_config
            config = get_config()
            return get_installed_apps_for_agent(self.workspace, config)
        except Exception:
            return []

    def _get_plugin_tool_names_for_context(self) -> list[str]:
        """Get plugin tool names from loaded plugins for agent context."""
        try:
            from joyhousebot.plugins.discovery import get_plugin_tool_names_for_agent
            return get_plugin_tool_names_for_agent()
        except Exception:
            return []

    def _get_identity(self) -> str:
        """Get the core identity section."""
        from datetime import datetime
        import time as _time
        now = datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
        tz = _time.strftime("%Z") or "UTC"
        workspace_path = str(self.workspace.expanduser().resolve())
        system = platform.system()
        runtime = f"{'macOS' if system == 'Darwin' else system} {platform.machine()}, Python {platform.python_version()}"
        
        return f"""# joyhousebot 🐈

You are joyhousebot, a helpful AI assistant. You have access to tools that allow you to:
- Read, write, and edit files
- Execute shell commands
- Search the web and fetch web pages
- Send messages to users on chat channels
- Spawn subagents for complex background tasks

## Current Time
{now} ({tz})

## Runtime
{runtime}

## Workspace
Your workspace is at: {workspace_path}
- Long-term memory: {workspace_path}/memory/MEMORY.md
- History log: {workspace_path}/memory/HISTORY.md (grep-searchable)
- Knowledge base: put PDF/text/image files in {workspace_path}/knowledgebase; they are converted and indexed. Use retrieve(scope="knowledge", query=...) to search. For web pages, fetch content into knowledgebase first (e.g. with a fetch tool), then it will be processed.
- Custom skills: {workspace_path}/skills/{{skill-name}}/SKILL.md

IMPORTANT: When responding to direct questions or conversations, reply directly with your text response.
Only use the 'message' tool when you need to send a message to a specific chat channel (like WhatsApp).
For normal conversation, just respond with text - do not call the message tool.

Always be helpful, accurate, and concise. When using tools, think step by step: what you know, what you need, and why you chose this tool.
When remembering something important, write to {workspace_path}/memory/MEMORY.md
To recall past events, grep {workspace_path}/memory/HISTORY.md"""
    
    def _load_bootstrap_files(self) -> str:
        """Load all bootstrap files from workspace."""
        parts = []
        
        for filename in self.BOOTSTRAP_FILES:
            file_path = self.workspace / filename
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                parts.append(f"## {filename}\n\n{content}")
        
        return "\n\n".join(parts) if parts else ""

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
            skill_names: Optional skills to include.
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
        system_prompt = self.build_system_prompt(skill_names=skill_names, scope_key=scope_key)
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
        self,
        messages: list[dict[str, Any]],
        tool_call_id: str,
        tool_name: str,
        result: str
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
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": result
        })
        return messages
    
    def add_assistant_message(
        self,
        messages: list[dict[str, Any]],
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
        reasoning_content: str | None = None,
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
        
        messages.append(msg)
        return messages
