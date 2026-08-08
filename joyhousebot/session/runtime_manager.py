"""Database-backed, multi-user conversation session persistence."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from joyhousebot.session.models import Session

if TYPE_CHECKING:
    from joyhousebot.storage.runtime_store import RuntimeStore


# conversation_sessions.state is a consolidation cache, not the system of
# record: durable Run history is the source of truth for what happened.  The
# cached message tail is therefore bounded — only the newest messages are kept
# (the consolidation window is memory_window/2 ≈ 25 with a 50-message default
# memory window, so 200 leaves ample headroom) — and last_consolidated is
# shifted so it stays a valid index into the truncated list.
SESSION_STATE_MAX_MESSAGES = 200


class RuntimeSessionManager:
    """Stateless manager backed by the shared runtime store."""

    def __init__(self, store: RuntimeStore, *, namespace: str = "default") -> None:
        self.store = store
        self.namespace = namespace or "default"

    def _storage_key(self, key: str) -> str:
        return f"{len(self.namespace)}:{self.namespace}:{key}"

    def get_or_create(self, key: str) -> Session:
        state = self.store.get_session_state(self._storage_key(key))
        if state is None:
            return Session(key=key)
        try:
            created_at = datetime.fromisoformat(str(state.get("created_at") or ""))
        except ValueError:
            created_at = datetime.now()
        try:
            updated_at = datetime.fromisoformat(str(state.get("updated_at") or ""))
        except ValueError:
            updated_at = created_at
        return Session(
            key=key,
            messages=list(state.get("messages") or []),
            created_at=created_at,
            updated_at=updated_at,
            metadata=dict(state.get("metadata") or {}),
            last_consolidated=int(state.get("last_consolidated") or 0),
        )

    def save(self, session: Session) -> None:
        # Persist only the bounded tail; the live Session object keeps its
        # full in-memory list, the durable cache stays small.
        messages = list(session.messages)
        last_consolidated = session.last_consolidated
        dropped = max(0, len(messages) - SESSION_STATE_MAX_MESSAGES)
        if dropped:
            messages = messages[dropped:]
            last_consolidated = max(0, last_consolidated - dropped)
        self.store.save_session_state(
            self._storage_key(session.key),
            session_key=session.key,
            namespace=self.namespace,
            state={
                "messages": messages,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "metadata": session.metadata,
                "last_consolidated": last_consolidated,
            },
        )

    def invalidate(self, key: str) -> None:
        del key

    def delete(self, key: str) -> bool:
        return self.store.delete_session_state(self._storage_key(key))

    def list_sessions(self) -> list[dict[str, Any]]:
        return [
            {
                "key": row.get("session_key"),
                "created_at": row.get("created_at"),
                "updated_at": row.get("updated_at"),
            }
            for row in self.store.list_session_states(namespace=self.namespace)
        ]
