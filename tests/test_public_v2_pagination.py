import pytest
from fastapi import HTTPException

from joyhousebot.api.public_v2_pagination import paginate_public_items


def test_public_cursor_pagination_is_stable_and_opaque() -> None:
    rows = [
        {"created_at": "2026-08-18T00:00:00Z", "id": "b"},
        {"created_at": "2026-08-18T00:00:00Z", "id": "a"},
        {"created_at": "2026-08-18T00:00:01Z", "id": "c"},
    ]
    def key(item):  # noqa: ANN001, ANN201
        return item["created_at"], item["id"]
    first, cursor = paginate_public_items(rows, key=key, limit=2, cursor=None)
    assert [item["id"] for item in first] == ["a", "b"]
    assert cursor and "2026" not in cursor
    second, final_cursor = paginate_public_items(
        rows, key=key, limit=2, cursor=cursor
    )
    assert [item["id"] for item in second] == ["c"]
    assert final_cursor is None


def test_public_cursor_rejects_malformed_values() -> None:
    with pytest.raises(HTTPException, match="invalid pagination cursor"):
        paginate_public_items([], key=lambda item: item, limit=10, cursor="not-json")
