"""Immutable App Release descriptor validation."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

APP_RELEASE_MEDIA_TYPE = "application/vnd.porthouse.app.release.v1+json"
APP_MANIFEST_MEDIA_TYPE = "application/vnd.porthouse.app.manifest.v2+json"
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_PUBLISHER_ID = re.compile(r"^pub_[A-Za-z0-9][A-Za-z0-9_-]{5,127}$")
_APP_ID = re.compile(r"^app\.[a-z0-9][a-z0-9._-]{0,122}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}$")
_KINDS = {"agent", "team", "skill", "workflow", "scenario", "extension", "document"}


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_market_id(value: str) -> str:
    result = str(value or "").strip().rstrip("/")
    parsed = urlsplit(result)
    loopback_http = parsed.scheme == "http" and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }
    if (
        (parsed.scheme != "https" and not loopback_http)
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("market_id must be an HTTPS origin or loopback HTTP origin")
    if parsed.path or parsed.query or parsed.fragment:
        raise ValueError("market_id must not contain a path, query, or fragment")
    default_port = 443 if parsed.scheme == "https" else 80
    port = f":{parsed.port}" if parsed.port and parsed.port != default_port else ""
    hostname = parsed.hostname.lower()
    host = f"[{hostname}]" if ":" in hostname else hostname
    return f"{parsed.scheme}://{host}{port}"


def normalize_publisher_id(value: str) -> str:
    result = str(value or "").strip()
    if not _PUBLISHER_ID.fullmatch(result):
        raise ValueError("publisher_id must start with pub_ and be a stable identifier")
    return result


def normalize_app_id(value: str) -> str:
    result = str(value or "").strip().lower()
    if not _APP_ID.fullmatch(result):
        raise ValueError("app_id must start with 'app.' and use lowercase stable characters")
    return result


def normalize_app_version(value: str) -> str:
    result = str(value or "").strip()
    if not _VERSION.fullmatch(result):
        raise ValueError("App Pack version is required and must be a stable identifier")
    return result


def _descriptor(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    digest = str(value.get("digest") or "")
    size = value.get("size")
    if not _DIGEST.fullmatch(digest):
        raise ValueError(f"{field}.digest must be sha256")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0 or size > 1_000_000_000:
        raise ValueError(f"{field}.size must be an integer between 0 and 1GB")
    media_type = str(value.get("media_type") or "").strip()
    if not media_type or len(media_type) > 255:
        raise ValueError(f"{field}.media_type is required")
    return {"media_type": media_type, "digest": digest, "size": size}


def normalize_release_descriptor(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict) or str(value.get("schema_version") or "") != "1.0":
        raise ValueError("unsupported App Release schema_version")
    source = value.get("source")
    if not isinstance(source, dict):
        raise ValueError("release source is required")
    normalized_source = {
        "market_id": normalize_market_id(str(source.get("market_id") or "")),
        "publisher_id": normalize_publisher_id(str(source.get("publisher_id") or "")),
        "app_id": normalize_app_id(str(source.get("app_id") or "")),
    }
    manifest = _descriptor(value.get("app_manifest"), field="app_manifest")
    if manifest["media_type"] != APP_MANIFEST_MEDIA_TYPE:
        raise ValueError("release must reference an App Manifest v2")
    components = value.get("components") or []
    if not isinstance(components, list) or len(components) > 512:
        raise ValueError("release components must contain at most 512 items")
    normalized_components: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in components:
        if not isinstance(raw, dict):
            raise ValueError("release component must be an object")
        kind = str(raw.get("kind") or "")
        if kind not in _KINDS:
            raise ValueError(f"unsupported release component kind: {kind}")
        logical_id = str(raw.get("logical_id") or "").strip()
        version = normalize_app_version(str(raw.get("version") or ""))
        if not logical_id or len(logical_id) > 160:
            raise ValueError("release component logical_id is required")
        identity = (kind, logical_id, version)
        if identity in seen:
            raise ValueError(f"duplicate release component: {identity}")
        seen.add(identity)
        normalized_components.append(
            {
                "kind": kind,
                "logical_id": logical_id,
                "version": version,
                **_descriptor(raw, field=f"component {logical_id}"),
            }
        )
    compatibility = value.get("compatibility") or {}
    if not isinstance(compatibility, dict):
        raise ValueError("release compatibility must be an object")
    core = compatibility.get("core") or {}
    if not isinstance(core, dict):
        raise ValueError("release compatibility.core must be an object")
    licenses = value.get("licenses") or {}
    evidence = value.get("evidence") or {}
    if not isinstance(licenses, dict) or not isinstance(evidence, dict):
        raise ValueError("release licenses and evidence must be objects")
    released_at = str(value.get("released_at") or "")
    try:
        parsed_release_time = datetime.fromisoformat(released_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("released_at must be an RFC 3339 timestamp") from exc
    if parsed_release_time.tzinfo is None:
        raise ValueError("released_at must include a timezone")
    return {
        "schema_version": "1.0",
        "source": normalized_source,
        "version": normalize_app_version(str(value.get("version") or "")),
        "released_at": released_at,
        "app_manifest": manifest,
        "components": sorted(
            normalized_components,
            key=lambda item: (item["kind"], item["logical_id"], item["version"]),
        ),
        "compatibility": {
            "core": {
                "min_version": str(core.get("min_version") or ""),
                "max_version": str(core.get("max_version") or ""),
            },
            "platforms": sorted({str(item) for item in compatibility.get("platforms") or ["any"]}),
            "architectures": sorted(
                {str(item) for item in compatibility.get("architectures") or ["any"]}
            ),
        },
        "licenses": dict(licenses),
        "evidence": dict(evidence),
    }
