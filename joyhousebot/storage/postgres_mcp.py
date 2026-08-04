"""Durable external MCP server configuration for PostgreSQL deployments."""

from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb


class PostgresMCPStoreMixin:
    def migrate_mcp_servers(self) -> None:
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("""CREATE TABLE IF NOT EXISTS mcp_servers (
                name TEXT PRIMARY KEY, enabled BOOLEAN NOT NULL DEFAULT TRUE,
                command TEXT NOT NULL DEFAULT '', args JSONB NOT NULL DEFAULT '[]'::jsonb,
                env JSONB NOT NULL DEFAULT '{}'::jsonb, url TEXT NOT NULL DEFAULT ''
            )""")

    def list_mcp_servers(self) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute("SELECT * FROM mcp_servers ORDER BY name").fetchall()
        return [{"name": row["name"], "enabled": bool(row["enabled"]), "command": row["command"],
                 "args": list(row["args"] or []), "env": dict(row["env"] or {}), "url": row["url"]} for row in rows]

    def save_mcp_server(self, name: str, value: dict[str, Any]) -> None:
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("""INSERT INTO mcp_servers (name,enabled,command,args,env,url) VALUES (%s,%s,%s,%s,%s,%s)
                ON CONFLICT(name) DO UPDATE SET enabled=excluded.enabled,command=excluded.command,args=excluded.args,env=excluded.env,url=excluded.url""",
                (name, bool(value.get("enabled", True)), str(value.get("command") or ""), Jsonb(value.get("args") or []), Jsonb(value.get("env") or {}), str(value.get("url") or "")))

    def delete_mcp_server(self, name: str) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            return conn.execute("DELETE FROM mcp_servers WHERE name=%s", (name,)).rowcount == 1
