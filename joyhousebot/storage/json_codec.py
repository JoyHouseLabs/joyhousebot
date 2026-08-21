"""One JSONB boundary for database-backed runtime payloads.

Capabilities and Extensions may return database-native scalar values. PostgreSQL
JSONB accepts JSON only, so normalize at the storage boundary rather than
requiring every business adapter to remember every Python scalar type.
"""

from __future__ import annotations

import base64
import math
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb as PsycopgJsonb


def json_value(value: Any, *, _depth: int = 0) -> Any:
    """Return a loss-aware, JSON-serializable representation of ``value``."""
    if _depth >= 32:
        return "<max-json-depth>"
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (UUID, Path)):
        return str(value)
    if isinstance(value, Enum):
        return json_value(value.value, _depth=_depth + 1)
    if isinstance(value, bytes):
        return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    if is_dataclass(value):
        return json_value(asdict(value), _depth=_depth + 1)
    if isinstance(value, Mapping):
        return {str(key): json_value(item, _depth=_depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_value(item, _depth=_depth + 1) for item in value]
    return str(value)


def Jsonb(value: Any) -> PsycopgJsonb:  # noqa: N802
    """Build a psycopg JSONB parameter after normalizing Python scalars."""
    return PsycopgJsonb(json_value(value))
