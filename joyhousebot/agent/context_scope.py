"""Authenticated Memory scope resolution shared by Agent message paths."""

from __future__ import annotations

from joyhousebot.runtime.context import RunContext


class ContextScopeMixin:
    def _resolve_memory_scope_key(
        self,
        session_key: str,
        sender_id: str = "",
        metadata: dict | None = None,
        run_context: RunContext | None = None,
    ) -> str | None:
        """Resolve shared, session, or authenticated user Memory scope."""
        if not self.config:
            return None
        retrieval = getattr(getattr(self.config, "tools", None), "retrieval", None)
        if not retrieval:
            return None
        scope = getattr(retrieval, "memory_scope", "user") or "user"
        if scope == "shared":
            return "shared"
        if scope == "session":
            return session_key
        if scope != "user":
            return None
        if run_context is not None and run_context.user_id:
            return f"user:{run_context.user_id}:agent:{run_context.agent_id}"
        from_id = getattr(retrieval, "memory_user_id_from", "sender_id") or "sender_id"
        meta_key = getattr(retrieval, "memory_user_id_metadata_key", "user_id") or "user_id"
        meta = metadata or {}
        candidate = (
            (meta.get(meta_key) or "").strip()
            if from_id == "metadata" and isinstance(meta.get(meta_key), str)
            else ""
        )
        if candidate and run_context is not None and candidate == run_context.user_id:
            user_id = candidate
        else:
            user_id = (sender_id or "").strip()
        if not user_id:
            user_id = session_key.split(":", 1)[-1] if ":" in session_key else session_key
        channel = session_key.split(":", 1)[0] if ":" in session_key else "unknown"
        return f"{channel}:{user_id}"
