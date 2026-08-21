"""Small opaque keyset cursor shared by bounded public resource lists."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from fastapi import HTTPException

T = TypeVar("T")


def paginate_public_items(
    items: Sequence[T],
    *,
    key: Callable[[T], tuple[str, str]],
    limit: int,
    cursor: str | None,
) -> tuple[list[T], str | None]:
    after = _decode_cursor(cursor) if cursor else None
    ordered = sorted(items, key=key)
    if after is not None:
        ordered = [item for item in ordered if key(item) > after]
    page = ordered[: limit + 1]
    has_more = len(page) > limit
    page = page[:limit]
    next_cursor = _encode_cursor(key(page[-1])) if has_more and page else None
    return page, next_cursor


def _encode_cursor(value: tuple[str, str]) -> str:
    payload = json.dumps(list(value), separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[str, str]:
    try:
        padding = "=" * (-len(value) % 4)
        decoded: Any = json.loads(base64.urlsafe_b64decode(value + padding))
        if (
            not isinstance(decoded, list)
            or len(decoded) != 2
            or not all(isinstance(item, str) for item in decoded)
        ):
            raise ValueError
        return decoded[0], decoded[1]
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="invalid pagination cursor") from exc


__all__ = ["paginate_public_items"]
