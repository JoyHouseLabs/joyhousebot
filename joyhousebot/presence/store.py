"""In-memory presence store: connected clients and gateway (OpenClaw-style)."""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PresenceEntry:
    """A single presence record."""

    instance_id: str
    ts: int  # last update ms
    reason: str  # self, connect, periodic, ...
    mode: str  # ui, webchat, cli, backend, probe, test, node
    last_input_seconds: int | None = None
    ip: str | None = None
    host: str | None = None
    version: str | None = None
    device_family: str | None = None
    model_identifier: str | None = None
    # Internal: connection key for WS clients so we can remove on disconnect
    _connection_key: str | None = None


def _now_ms() -> int:
    return int(time.time() * 1000)


class PresenceStore:
    """
    Lightweight, best-effort presence: clients connected to Gateway + Gateway itself.
    In-memory, max 200 entries, 5-minute TTL. Keys are case-insensitive instance_id.
    """

    TTL_MS = 5 * 60 * 1000
    MAX_ENTRIES = 200

    def __init__(self) -> None:
        self._entries: dict[str, PresenceEntry] = {}
        self._connection_to_key: dict[str, str] = {}  # connection_key -> instance_id (lower)

    def _prune(self) -> None:
        now = _now_ms()
        stale = [
            k for k, e in self._entries.items()
            if e.reason != "self" and (now - e.ts) > self.TTL_MS
        ]
        for k in stale:
            e = self._entries.pop(k, None)
            if e and e._connection_key:
                self._connection_to_key.pop(e._connection_key, None)
        if len(self._entries) > self.MAX_ENTRIES:
            by_ts = sorted(self._entries.items(), key=lambda x: x[1].ts)
            for k, _ in by_ts[: len(self._entries) - self.MAX_ENTRIES]:
                e = self._entries.pop(k, None)
                if e and e._connection_key:
                    self._connection_to_key.pop(e._connection_key, None)

    def _normalize_key(self, instance_id: str) -> str:
        return (instance_id or "").strip().lower() or str(uuid.uuid4())

    def upsert(
        self,
        instance_id: str,
        *,
        reason: str = "connect",
        mode: str = "webchat",
        last_input_seconds: int | None = None,
        ip: str | None = None,
        host: str | None = None,
        version: str | None = None,
        device_family: str | None = None,
        model_identifier: str | None = None,
        connection_key: str | None = None,
    ) -> PresenceEntry:
        key = self._normalize_key(instance_id)
        now = _now_ms()
        if connection_key:
            old_key = self._connection_to_key.get(connection_key)
            if old_key and old_key != key and old_key in self._entries:
                del self._entries[old_key]
            self._connection_to_key[connection_key] = key
        existing = self._entries.get(key)
        entry = PresenceEntry(
            instance_id=instance_id.strip() or key,
            ts=now,
            reason=reason,
            mode=mode,
            last_input_seconds=last_input_seconds or (existing.last_input_seconds if existing else None),
            ip=ip or (existing.ip if existing else None),
            host=host or (existing.host if existing else None),
            version=version or (existing.version if existing else None),
            device_family=device_family or (existing.device_family if existing else None),
            model_identifier=model_identifier or (existing.model_identifier if existing else None),
            _connection_key=connection_key or (existing._connection_key if existing else None),
        )
        self._entries[key] = entry
        self._prune()
        return entry

    def remove_by_connection(self, connection_key: str) -> bool:
        """Remove presence for a connection (e.g. WebSocket disconnect)."""
        key = self._connection_to_key.pop(connection_key, None)
        if key and key in self._entries:
            del self._entries[key]
            return True
        return False

    def register_gateway(self, host: str = "127.0.0.1", port: int = 18790) -> PresenceEntry:
        """Register the gateway itself as a presence entry (reason=self)."""
        instance_id = f"gateway:{host}:{port}"
        return self.upsert(
            instance_id,
            reason="self",
            mode="backend",
            host=host,
        )

    def list_entries(self) -> list[dict[str, Any]]:
        """Return current presence list (after prune), as JSON-serializable dicts."""
        self._prune()
        out = []
        for e in self._entries.values():
            out.append({
                "instance_id": e.instance_id,
                "ts": e.ts,
                "reason": e.reason,
                "mode": e.mode,
                "last_input_seconds": e.last_input_seconds,
                "ip": e.ip,
                "host": e.host,
                "version": e.version,
                "device_family": e.device_family,
                "model_identifier": e.model_identifier,
            })
        return sorted(out, key=lambda x: (x["ts"]), reverse=True)
