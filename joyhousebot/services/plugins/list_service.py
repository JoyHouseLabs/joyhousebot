"""Shared helpers for plugin list/info rendering payloads."""

from __future__ import annotations

from typing import Any


def row_get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def filter_plugin_rows(rows: list[Any], keyword: str) -> list[Any]:
    needle = keyword.strip().lower()
    if not needle:
        return rows
    return [
        row
        for row in rows
        if needle in str(row_get(row, "id", "")).lower()
        or needle in str(row_get(row, "name", "")).lower()
        or needle in str(row_get(row, "source", "")).lower()
    ]


def plugin_table_row(row: Any) -> tuple[str, str, str, str, str]:
    return (
        str(row_get(row, "id", "")),
        str(row_get(row, "name", "")),
        str(row_get(row, "status", "")),
        str(row_get(row, "origin", "")),
        str(row_get(row, "source", "")),
    )


def native_plugin_table_row(row: Any) -> tuple[str, str, str, str, str, str, str, str]:
    return (
        str(row_get(row, "id", "")),
        str(row_get(row, "name", "")),
        str(row_get(row, "status", "")),
        str(row_get(row, "runtime", "") or "python-native"),
        ",".join(row_get(row, "capabilities", []) or []) or "-",
        str(len(row_get(row, "gateway_methods", []) or [])),
        str(len(row_get(row, "hook_names", []) or [])),
        str(row_get(row, "source", "")),
    )


def is_native_plugin_row(row: Any) -> bool:
    return str(row_get(row, "origin", "")) == "native"


def resolve_plugin_info_row(rows: list[Any], plugin_id: str) -> Any | None:
    for row in rows:
        if str(row_get(row, "id", "")) == plugin_id or str(row_get(row, "name", "")) == plugin_id:
            return row
    return None


def plugin_info_fields(row: Any) -> list[tuple[str, str]]:
    return [
        ("id", str(row_get(row, "id", ""))),
        ("status", str(row_get(row, "status", ""))),
        ("enabled", str(row_get(row, "enabled", ""))),
        ("origin", str(row_get(row, "origin", ""))),
        ("source", str(row_get(row, "source", ""))),
        ("version", str(row_get(row, "version", "") or "-")),
        ("description", str(row_get(row, "description", "") or "-")),
        ("runtime", str(row_get(row, "runtime", "") or "-")),
        ("capabilities", ", ".join(row_get(row, "capabilities", []) or []) or "-"),
        ("tools", ", ".join(row_get(row, "tool_names", []) or []) or "-"),
        ("gateway methods", ", ".join(row_get(row, "gateway_methods", []) or []) or "-"),
        ("cli commands", ", ".join(row_get(row, "cli_commands", []) or []) or "-"),
        ("services", ", ".join(row_get(row, "services", []) or []) or "-"),
    ]

