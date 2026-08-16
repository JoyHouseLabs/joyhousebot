"""Cross-language canonical JSON and content identity helpers."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

import rfc8785


def canonical_json(value: Any) -> bytes:
    """Encode one I-JSON value according to RFC 8785 JCS.

    ``rfc8785`` rejects NaN, infinity, integers outside its interoperable
    range, and unsupported Python values.  Those failures are protocol
    validation errors rather than a reason to fall back to another encoding.
    """

    try:
        return bytes(rfc8785.dumps(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"value is not RFC 8785 canonical JSON: {exc}") from exc


def canonical_sha256(value: Any) -> str:
    return f"sha256:{sha256(canonical_json(value)).hexdigest()}"


def bytes_sha256(value: bytes) -> str:
    return f"sha256:{sha256(value).hexdigest()}"


def parse_strict_json(value: bytes | str) -> Any:
    """Parse JSON while rejecting duplicate keys and invalid constants."""

    def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise ValueError(f"duplicate JSON property: {key}")
            result[key] = item
        return result

    def reject_constant(name: str) -> None:
        raise ValueError(f"invalid JSON constant: {name}")

    try:
        return json.loads(
            value,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid UTF-8 JSON payload") from exc
