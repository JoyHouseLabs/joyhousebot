from datetime import date, datetime, time, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from uuid import UUID

from joyhousebot.storage.json_codec import json_value


class State(Enum):
    READY = "ready"


def test_json_value_normalizes_database_and_runtime_native_scalars():
    value = json_value(
        {
            "at": datetime(2026, 8, 5, 12, 30, tzinfo=timezone.utc),
            "day": date(2026, 8, 5),
            "time": time(12, 30),
            "amount": Decimal("1.25"),
            "id": UUID("12345678-1234-5678-1234-567812345678"),
            "path": Path("/tmp/catalog"),
            "state": State.READY,
            "binary": b"joy",
            "nested": ({"not_finite": float("nan")},),
        }
    )

    assert value == {
        "at": "2026-08-05T12:30:00+00:00",
        "day": "2026-08-05",
        "time": "12:30:00",
        "amount": 1.25,
        "id": "12345678-1234-5678-1234-567812345678",
        "path": "/tmp/catalog",
        "state": "ready",
        "binary": {"encoding": "base64", "data": "am95"},
        "nested": [{"not_finite": "nan"}],
    }
