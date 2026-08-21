"""Versioned configuration for deterministic Knowledge embeddings."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_REVISION_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}:v[1-9][0-9]*$")


def normalize_embedding_profile(profile_id: str, value: dict[str, Any]) -> dict[str, Any]:
    """Validate one immutable embedding profile revision."""
    normalized_id = str(profile_id).strip().lower()
    if not _PROFILE_ID.fullmatch(normalized_id):
        raise ValueError("embedding profile id is invalid")
    if not isinstance(value, dict):
        raise ValueError("embedding profile configuration must be an object")
    allowed = {
        "provider_id",
        "provider_revision_id",
        "model_id",
        "dimensions",
        "normalization",
        "batch_size",
        "max_input_tokens",
        "max_cost_usd",
        "requests_per_minute",
        "tokens_per_minute",
        "ann_min_rows",
        "hnsw_m",
        "hnsw_ef_construction",
        "hnsw_ef_search",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(
            "embedding profile contains unsupported fields: " + ", ".join(unknown)
        )
    provider_id = str(value.get("provider_id") or "").strip().lower()
    if not provider_id or not _PROFILE_ID.fullmatch(provider_id):
        raise ValueError("embedding profile provider_id is invalid")
    provider_revision_id = str(value.get("provider_revision_id") or "").strip()
    if not _REVISION_ID.fullmatch(provider_revision_id):
        raise ValueError("embedding profile requires an exact provider revision id")
    if not provider_revision_id.startswith(f"{provider_id}:v"):
        raise ValueError("embedding profile provider revision does not match provider_id")
    model_id = str(value.get("model_id") or "").strip()
    if not model_id or len(model_id) > 256 or not model_id.startswith(f"{provider_id}/"):
        raise ValueError(f"embedding profile model_id must use the exact {provider_id}/ prefix")
    dimensions = int(value.get("dimensions") or 0)
    if not 1 <= dimensions <= 16_000:
        raise ValueError("embedding profile dimensions must be between 1 and 16000")
    normalization = str(value.get("normalization") or "none").strip().lower()
    if normalization not in {"none", "l2"}:
        raise ValueError("embedding profile normalization must be none or l2")
    batch_size = int(value.get("batch_size") or 32)
    if not 1 <= batch_size <= 256:
        raise ValueError("embedding profile batch_size must be between 1 and 256")
    max_input_tokens = int(value.get("max_input_tokens") or 8192)
    if not 1 <= max_input_tokens <= 1_000_000:
        raise ValueError("embedding profile max_input_tokens is invalid")
    max_cost_usd = float(value.get("max_cost_usd") or 0)
    if not 0 <= max_cost_usd <= 10_000:
        raise ValueError("embedding profile max_cost_usd is invalid")
    requests_per_minute = int(value.get("requests_per_minute") or 60)
    if not 1 <= requests_per_minute <= 100_000:
        raise ValueError("embedding profile requests_per_minute is invalid")
    tokens_per_minute = int(value.get("tokens_per_minute") or 1_000_000)
    if not 1 <= tokens_per_minute <= 1_000_000_000:
        raise ValueError("embedding profile tokens_per_minute is invalid")
    ann_min_rows = int(value.get("ann_min_rows") or 10_000)
    if not 100 <= ann_min_rows <= 100_000_000:
        raise ValueError("embedding profile ann_min_rows is invalid")
    hnsw_m = int(value.get("hnsw_m") or 16)
    if not 2 <= hnsw_m <= 100:
        raise ValueError("embedding profile hnsw_m is invalid")
    hnsw_ef_construction = int(value.get("hnsw_ef_construction") or 64)
    if not 4 <= hnsw_ef_construction <= 1000:
        raise ValueError("embedding profile hnsw_ef_construction is invalid")
    hnsw_ef_search = int(value.get("hnsw_ef_search") or 40)
    if not 1 <= hnsw_ef_search <= 1000:
        raise ValueError("embedding profile hnsw_ef_search is invalid")
    return {
        "provider_id": provider_id,
        "provider_revision_id": provider_revision_id,
        "model_id": model_id,
        "dimensions": dimensions,
        "normalization": normalization,
        "batch_size": batch_size,
        "max_input_tokens": max_input_tokens,
        "max_cost_usd": max_cost_usd,
        "requests_per_minute": requests_per_minute,
        "tokens_per_minute": tokens_per_minute,
        "ann_min_rows": ann_min_rows,
        "hnsw_m": hnsw_m,
        "hnsw_ef_construction": hnsw_ef_construction,
        "hnsw_ef_search": hnsw_ef_search,
    }


def embedding_profile_fingerprint(configuration: dict[str, Any]) -> str:
    body = json.dumps(
        configuration, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


__all__ = ["embedding_profile_fingerprint", "normalize_embedding_profile"]
