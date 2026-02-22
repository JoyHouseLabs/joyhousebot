"""Library native plugin: books, tags, list, search."""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any


def _db_path(plugin_config: dict[str, Any]) -> str:
    data_dir = (plugin_config or {}).get("data_dir") or os.path.expanduser("~/.joyhouse/plugins/library")
    Path(data_dir).mkdir(parents=True, exist_ok=True)
    return str(Path(data_dir) / "library.db")


def _ensure_schema(conn: sqlite3.Connection, plugin_root: Path) -> None:
    migrations_dir = plugin_root / "migrations"
    if not migrations_dir.exists():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS books (
              id TEXT PRIMARY KEY, title TEXT NOT NULL, isbn TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS book_tags (
              book_id TEXT NOT NULL, tag TEXT NOT NULL,
              PRIMARY KEY (book_id, tag),
              FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_books_title ON books(title);
            CREATE INDEX IF NOT EXISTS idx_book_tags_tag ON book_tags(tag);
            """
        )
        return
    for p in sorted(migrations_dir.glob("*.sql")):
        sql = p.read_text(encoding="utf-8")
        conn.executescript(sql)
    conn.commit()


def _get_conn(plugin_config: dict[str, Any], plugin_root: Path | None = None) -> sqlite3.Connection:
    path = _db_path(plugin_config)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _ensure_schema(conn, plugin_root or Path(__file__).resolve().parent)
    return conn


class LibraryPlugin:
    def __init__(self) -> None:
        self._plugin_root: Path | None = None
        self._config: dict[str, Any] = {}

    def register(self, api: Any) -> None:
        self._config = getattr(api, "plugin_config", {}) or {}
        try:
            self._plugin_root = Path(__file__).resolve().parent
        except Exception:
            self._plugin_root = None

        api.register_tool("library.create_book", self._create_book)
        api.register_tool("library.tag_book", self._tag_book)
        api.register_tool("library.list_books", self._list_books)
        api.register_tool("library.search_books", self._search_books)

    def _conn(self) -> sqlite3.Connection:
        return _get_conn(self._config, self._plugin_root)

    def _create_book(
        self,
        title: str,
        isbn: str | None = None,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not (title or "").strip():
            return {"ok": False, "error": "title is required"}
        bid = str(uuid.uuid4())[:8]
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO books (id, title, isbn, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (bid, (title or "").strip(), (isbn or "").strip() or None, now, now),
            )
            for tag in (tags or []):
                t = (tag or "").strip()
                if t:
                    conn.execute("INSERT OR IGNORE INTO book_tags (book_id, tag) VALUES (?, ?)", (bid, t))
            conn.commit()
            return {"ok": True, "id": bid, "title": (title or "").strip(), "isbn": (isbn or "").strip() or None}
        except sqlite3.IntegrityError as e:
            return {"ok": False, "error": str(e)}
        finally:
            conn.close()

    def _tag_book(
        self,
        book_id: str,
        tag: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if not (book_id or "").strip() or not (tag or "").strip():
            return {"ok": False, "error": "book_id and tag are required"}
        conn = self._conn()
        try:
            conn.execute("INSERT OR IGNORE INTO book_tags (book_id, tag) VALUES (?, ?)", (book_id.strip(), tag.strip()))
            conn.commit()
            return {"ok": True, "book_id": book_id.strip(), "tag": tag.strip()}
        except Exception as e:
            return {"ok": False, "error": str(e)}
        finally:
            conn.close()

    def _list_books(
        self,
        limit: int = 50,
        offset: int = 0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        conn = self._conn()
        try:
            cur = conn.execute(
                "SELECT id, title, isbn, created_at, updated_at FROM books ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (max(0, limit), max(0, offset)),
            )
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                r["tags"] = [
                    row[0]
                    for row in conn.execute("SELECT tag FROM book_tags WHERE book_id = ?", (r["id"],)).fetchall()
                ]
            return {"ok": True, "books": rows, "count": len(rows)}
        finally:
            conn.close()

    def _search_books(
        self,
        q: str | None = None,
        tag: str | None = None,
        limit: int = 50,
        **kwargs: Any,
    ) -> dict[str, Any]:
        conn = self._conn()
        try:
            if tag and (tag or "").strip():
                cur = conn.execute(
                    """
                    SELECT b.id, b.title, b.isbn, b.created_at, b.updated_at
                    FROM books b
                    JOIN book_tags t ON t.book_id = b.id
                    WHERE t.tag = ?
                    ORDER BY b.updated_at DESC
                    LIMIT ?
                    """,
                    (tag.strip(), max(0, limit)),
                )
            elif q and (q or "").strip():
                pattern = f"%{(q or '').strip()}%"
                cur = conn.execute(
                    """
                    SELECT id, title, isbn, created_at, updated_at
                    FROM books
                    WHERE title LIKE ? OR (isbn IS NOT NULL AND isbn LIKE ?)
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (pattern, pattern, max(0, limit)),
                )
            else:
                cur = conn.execute(
                    "SELECT id, title, isbn, created_at, updated_at FROM books ORDER BY updated_at DESC LIMIT ?",
                    (max(0, limit),),
                )
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                r["tags"] = [
                    row[0]
                    for row in conn.execute("SELECT tag FROM book_tags WHERE book_id = ?", (r["id"],)).fetchall()
                ]
            return {"ok": True, "books": rows, "count": len(rows)}
        finally:
            conn.close()


plugin = LibraryPlugin()
