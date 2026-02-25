"""HTTP and config helpers for modular CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib import error, request

from joyhousebot.config.loader import get_config_path, load_config


def get_gateway_base_url() -> str:
    """Build gateway base URL from local config."""
    config = load_config()
    host = getattr(config.gateway, "host", "127.0.0.1")
    port = getattr(config.gateway, "port", 18790)
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    return f"http://{host}:{port}"


def get_http_api_headers() -> dict[str, str]:
    """Return Authorization header when gateway.control_token is set (for /api requests; same as WS control auth)."""
    config = load_config()
    token = (getattr(config.gateway, "control_token", None) or "").strip()
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def http_json(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    timeout: float = 5.0,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Send an HTTP request and parse JSON response."""
    body = None
    req_headers: dict[str, str] = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        req_headers["Content-Type"] = "application/json"
    if headers:
        req_headers.update(headers)
    req = request.Request(url=url, data=body, method=method.upper(), headers=req_headers)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            return json.loads(text) if text else {}
    except error.HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        detail: Any = text
        try:
            detail = json.loads(text)
        except Exception:
            pass
        raise RuntimeError(f"{exc.code} {exc.reason}: {detail}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Gateway unavailable: {exc.reason}") from exc


def load_config_json() -> dict[str, Any]:
    """Read raw config JSON from disk."""
    path = get_config_path()
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_config_json(data: dict[str, Any]) -> Path:
    """Write raw config JSON to default config path."""
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def parse_value(raw: str) -> Any:
    """Parse CLI input value as JSON if possible; fallback to string."""
    text = raw.strip()
    if text == "":
        return ""
    try:
        return json.loads(text)
    except Exception:
        lowered = text.lower()
        if lowered in {"true", "false"}:
            return lowered == "true"
        return text


def deep_get(data: dict[str, Any], dotted_key: str) -> Any:
    """Get value by dotted path."""
    cur: Any = data
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            raise KeyError(dotted_key)
        cur = cur[part]
    return cur


def deep_set(data: dict[str, Any], dotted_key: str, value: Any) -> None:
    """Set value by dotted path."""
    parts = dotted_key.split(".")
    cur: dict[str, Any] = data
    for part in parts[:-1]:
        node = cur.get(part)
        if not isinstance(node, dict):
            node = {}
            cur[part] = node
        cur = node
    cur[parts[-1]] = value


def deep_unset(data: dict[str, Any], dotted_key: str) -> bool:
    """Delete key by dotted path; return True if removed."""
    parts = dotted_key.split(".")
    cur: Any = data
    for part in parts[:-1]:
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    if not isinstance(cur, dict) or parts[-1] not in cur:
        return False
    del cur[parts[-1]]
    return True

