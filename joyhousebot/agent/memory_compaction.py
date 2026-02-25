"""Scheduled memory compaction: L2 (daily logs / HISTORY) -> L1 (insights/lessons), then L1 + MEMORY -> L0 (.abstract)."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import json_repair
from loguru import logger

from joyhousebot.agent.memory import INSIGHTS_DIR, LESSONS_DIR, MemoryStore


def _parse_iso_date(s: str) -> date | None:
    """Parse YYYY-MM-DD from filename or string."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


MEMORY_SCOPE_RESERVED = {"insights", "lessons", "archive"}  # Top-level dirs under memory/ that are not scope keys


async def _run_memory_compaction_one(
    workspace: Path,
    provider: Any,
    model: str,
    store: MemoryStore,
    *,
    older_than_days: int = 2,
    max_daily_logs: int = 14,
    max_history_paragraphs: int = 15,
    max_l1_files_for_l0: int = 20,
) -> str:
    """Run compaction for a single MemoryStore (one scope)."""
    store.ensure_memory_structure()
    today = date.today()
    cutoff = today - timedelta(days=older_than_days)
    run_date_str = today.isoformat()

    # --- L2 -> L1: collect old daily logs ---
    daily_log_paths: list[tuple[date, Path]] = []
    for p in store.memory_dir.glob("*.md"):
        if p.name in ("MEMORY.md", "HISTORY.md"):
            continue
        d = _parse_iso_date(p.name)
        if d and d < cutoff:
            daily_log_paths.append((d, p))
    daily_log_paths.sort(key=lambda x: x[0])
    # Take oldest up to max_daily_logs (so we process in order and don't overload context)
    selected_daily = daily_log_paths[:max_daily_logs]
    daily_content_parts: list[str] = []
    for d, p in selected_daily:
        try:
            daily_content_parts.append(f"## {d.isoformat()}\n{p.read_text(encoding='utf-8').strip()}")
        except Exception:
            continue

    # Optionally add recent HISTORY paragraphs (as additional L2 source)
    if store.history_file.exists() and max_history_paragraphs > 0:
        try:
            text = store.history_file.read_text(encoding="utf-8")
            blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
            for b in blocks[-max_history_paragraphs:]:
                daily_content_parts.append(f"## HISTORY\n{b}")
        except Exception:
            pass

    combined_l2 = "\n\n".join(daily_content_parts).strip()
    insights_written = 0
    lessons_written = 0

    if combined_l2 and len(combined_l2) > 200:
        # LLM: summarize into insights and lessons
        prompt = f"""Summarize the following daily logs / history into two lists.

1. insights: Key takeaways, decisions, or notable facts (1-5 short bullets).
2. lessons: Operational lessons learned or patterns to remember (0-3 short bullets).

Return ONLY valid JSON with keys "insights" and "lessons", each an array of strings. No markdown fences.

## Input

{combined_l2[:12000]}

Respond with ONLY valid JSON."""

        try:
            response = await provider.chat(
                messages=[
                    {"role": "system", "content": "You output only valid JSON with keys insights and lessons (arrays of strings)."},
                    {"role": "user", "content": prompt},
                ],
                model=model,
            )
            text = (response.content or "").strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            result = json_repair.loads(text)
            if isinstance(result, dict):
                insights = result.get("insights") or []
                lessons = result.get("lessons") or []
                if isinstance(insights, list):
                    insights = [str(x).strip() for x in insights if str(x).strip()]
                if isinstance(lessons, list):
                    lessons = [str(x).strip() for x in lessons if str(x).strip()]

                insights_dir = store.memory_dir / INSIGHTS_DIR
                insights_dir.mkdir(parents=True, exist_ok=True)
                if insights:
                    out_insights = insights_dir / f"insights-{run_date_str}.md"
                    out_insights.write_text(
                        f"# Insights {run_date_str}\n\n" + "\n".join(f"- {i}" for i in insights),
                        encoding="utf-8",
                    )
                    insights_written = 1
                if lessons:
                    lessons_dir = store.memory_dir / LESSONS_DIR
                    lessons_dir.mkdir(parents=True, exist_ok=True)
                    out_lessons = lessons_dir / f"lessons-{run_date_str}.md"
                    out_lessons.write_text(
                        f"# Lessons {run_date_str}\n\n" + "\n".join(f"- {l}" for l in lessons),
                        encoding="utf-8",
                    )
                    lessons_written = 1
        except Exception as e:
            logger.warning(f"Memory compaction L2->L1 failed: {e}")

    # --- L1 + MEMORY -> L0: refresh .abstract ---
    raw_memory = store.read_long_term()
    if raw_memory.startswith("<!-- updated_at=") and " -->" in raw_memory:
        memory_body = raw_memory.split(" -->", 1)[-1].lstrip("\n")
    else:
        memory_body = raw_memory

    l1_parts: list[str] = []
    for subdir in (INSIGHTS_DIR, LESSONS_DIR):
        d = store.memory_dir / subdir
        if not d.is_dir():
            continue
        files = sorted(d.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in files[: max_l1_files_for_l0 // 2]:
            try:
                l1_parts.append(f"### {p.name}\n{p.read_text(encoding='utf-8').strip()}")
            except Exception:
                continue

    l1_block = "\n\n".join(l1_parts[:max_l1_files_for_l0]).strip()
    prompt_l0 = f"""Given the long-term memory and recent insights/lessons below, produce a short memory index (about 100-300 tokens) for retrieval.

Use exactly this structure (keep the headers):
## active topics
(bullet list of current active topics or themes)

## retrieval hints
(keywords or phrases that help find relevant memory)

## recency
(last updated: {run_date_str})

## Long-term memory (excerpt)
{memory_body[:4000] or "(empty)"}

{f"## Recent insights / lessons\n{l1_block[:3000]}" if l1_block else ""}

Respond with ONLY the memory index text (no JSON, no extra commentary)."""

    try:
        response = await provider.chat(
            messages=[
                {"role": "system", "content": "You output only the memory index in the requested format (## active topics, ## retrieval hints, ## recency)."},
                {"role": "user", "content": prompt_l0},
            ],
            model=model,
        )
        l0_text = (response.content or "").strip()
        if l0_text:
            # Ensure recency line
            if "## recency" not in l0_text.lower():
                l0_text += f"\n\n## recency\n(last updated: {run_date_str})"
            store.update_l0_abstract(l0_text)
            logger.info("Memory compaction: L0 (.abstract) updated")
    except Exception as e:
        logger.warning(f"Memory compaction L1+MEMORY->L0 failed: {e}")
        return f"compaction L0 failed: {e}"

    return f"ok: insights={insights_written}, lessons={lessons_written}, L0 refreshed"


async def run_memory_compaction(
    workspace: Path,
    provider: Any,
    model: str,
    *,
    config: Any = None,
    older_than_days: int = 2,
    max_daily_logs: int = 14,
    max_history_paragraphs: int = 15,
    max_l1_files_for_l0: int = 20,
) -> str:
    """
    Run L2->L1 then L1+MEMORY->L0 compaction.
    When config has memory_scope in (session, user), runs compaction for each scope subdir under memory/.
    Otherwise runs once for shared memory.
    """
    memory_scope = "shared"
    if config:
        retrieval = getattr(getattr(config, "tools", None), "retrieval", None)
        if retrieval:
            memory_scope = getattr(retrieval, "memory_scope", "shared") or "shared"

    if memory_scope == "shared":
        store = MemoryStore(workspace)
        return await _run_memory_compaction_one(
            workspace, provider, model, store,
            older_than_days=older_than_days,
            max_daily_logs=max_daily_logs,
            max_history_paragraphs=max_history_paragraphs,
            max_l1_files_for_l0=max_l1_files_for_l0,
        )

    base = workspace / "memory"
    if not base.is_dir():
        return "ok: no memory dir"
    scope_dirs = [d for d in base.iterdir() if d.is_dir() and d.name not in MEMORY_SCOPE_RESERVED]
    results = []
    for d in scope_dirs:
        store = MemoryStore(workspace, scope_key=d.name)
        try:
            out = await _run_memory_compaction_one(
                workspace, provider, model, store,
                older_than_days=older_than_days,
                max_daily_logs=max_daily_logs,
                max_history_paragraphs=max_history_paragraphs,
                max_l1_files_for_l0=max_l1_files_for_l0,
            )
            results.append(f"{d.name}:{out}")
        except Exception as e:
            logger.warning(f"Memory compaction failed for scope {d.name}: {e}")
            results.append(f"{d.name}:error")
    return "; ".join(results)
