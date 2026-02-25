"""Memory janitor: move expired P1/P2 entries from MEMORY.md to archive (with dry-run)."""

from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from joyhousebot.agent.memory import ARCHIVE_DIR, MemoryStore


# Match [P1|expire:YYYY-MM-DD] or [P2|expire:YYYY-MM-DD] (optional trailing content)
P_EXPIRE_PATTERN = re.compile(r"\[P[12]\|expire:(\d{4}-\d{2}-\d{2})\](.*)$", re.MULTILINE)


def _parse_expiry_lines(content: str, today: date) -> list[tuple[str, str]]:
    """Return list of (full_line, expire_date_str) for lines that are expired."""
    expired = []
    for line in content.split("\n"):
        m = P_EXPIRE_PATTERN.search(line)
        if not m:
            continue
        exp_str = m.group(1)
        try:
            exp = datetime.strptime(exp_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if exp < today:
            expired.append((line.strip(), exp_str))
    return expired


MEMORY_SCOPE_RESERVED = {"insights", "lessons", "archive"}


def _run_janitor_one(
    store: MemoryStore,
    dry_run: bool,
    today: date,
) -> list[dict]:
    """Run janitor for a single MemoryStore (one scope). Returns list of actions."""
    store.ensure_memory_structure()
    raw = store.read_long_term()
    # Strip leading updated_at comment for parsing
    if raw.startswith("<!-- updated_at=") and " -->" in raw:
        body = raw.split(" -->", 1)[-1].lstrip("\n")
    else:
        body = raw

    expired = _parse_expiry_lines(body, today)
    if not expired:
        logger.debug("Memory janitor: no expired P1/P2 entries")
        return []

    actions = []
    for line, exp_str in expired:
        actions.append({"line": line, "expire": exp_str, "archived": False})

    if dry_run:
        for a in actions:
            logger.info(f"Memory janitor (dry-run): would archive: {a['line'][:80]}...")
        return actions

    # Archive: append expired lines to archive/expired-YYYY-MM-DD.md
    archive_dir = store.memory_dir / ARCHIVE_DIR
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_file = archive_dir / f"expired-{today.isoformat()}.md"
    with open(archive_file, "a", encoding="utf-8") as f:
        f.write(f"<!-- archived from MEMORY.md on {today.isoformat()} -->\n")
        for line, _ in expired:
            f.write(line + "\n")
        f.write("\n")

    # Remove expired lines from MEMORY.md (keep header comment if any)
    expired_lines_set = {ex[0] for ex in expired}
    lines_out = []
    for line in body.split("\n"):
        if line.strip() not in expired_lines_set:
            lines_out.append(line)
    new_body = "\n".join(lines_out).rstrip() + "\n" if lines_out else ""
    if raw.startswith("<!-- updated_at=") and " -->" in raw:
        prefix = raw.split(" -->", 1)[0] + " -->\n"
        new_body = prefix + new_body
    store.write_long_term(new_body, updated_at=None)  # preserve existing updated_at in content if desired

    for a in actions:
        a["archived"] = True
    logger.info(f"Memory janitor: archived {len(actions)} expired entries to {archive_file}")
    return actions


def run_janitor(
    workspace: Path,
    dry_run: bool = True,
    today: date | None = None,
    config: Any = None,
) -> list[dict]:
    """
    Scan MEMORY.md for P1/P2 entries with expire date in the past; optionally move to archive.
    When config has memory_scope in (session, user), runs for each scope subdir under memory/.
    Returns list of actions (each: {"line": str, "expire": str, "archived": bool}); may include scope in action when multi-scope.
    """
    today = today or date.today()
    memory_scope = "shared"
    if config:
        retrieval = getattr(getattr(config, "tools", None), "retrieval", None)
        if retrieval:
            memory_scope = getattr(retrieval, "memory_scope", "shared") or "shared"

    if memory_scope == "shared":
        store = MemoryStore(workspace)
        return _run_janitor_one(store, dry_run, today)

    base = workspace / "memory"
    if not base.is_dir():
        return []
    all_actions = []
    for d in base.iterdir():
        if not d.is_dir() or d.name in MEMORY_SCOPE_RESERVED:
            continue
        store = MemoryStore(workspace, scope_key=d.name)
        try:
            actions = _run_janitor_one(store, dry_run, today)
            for a in actions:
                a["scope"] = d.name
            all_actions.extend(actions)
        except Exception as e:
            logger.warning(f"Memory janitor failed for scope {d.name}: {e}")
    return all_actions
