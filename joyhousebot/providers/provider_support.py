"""Provider-neutral request normalization and error classification."""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from joyhousebot.utils.exceptions import sanitize_error_message

_TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


class ProviderHTTPError(RuntimeError):
    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        code: str | None = None,
        raw_response: Any = None,
        provider_request_id: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = code or f"http_{status_code}"
        self.raw_response = raw_response
        self.provider_request_id = provider_request_id


def sanitize_messages(
    messages: list[dict[str, Any]],
    *,
    original_to_alias: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    allowed = {"role", "content", "name", "tool_call_id", "tool_calls"}
    name_map = original_to_alias or {}
    result = []
    for raw in messages:
        message = {key: value for key, value in dict(raw).items() if key in allowed}
        if message.get("content") is None:
            message["content"] = ""
        calls = message.get("tool_calls")
        if isinstance(calls, list):
            message["tool_calls"] = _sanitize_tool_calls(calls, name_map=name_map)
        result.append(message)
    return result


def _sanitize_tool_calls(
    calls: list[Any], *, name_map: dict[str, str]
) -> list[Any]:
    normalized = []
    for call in calls:
        current = dict(call) if isinstance(call, dict) else call
        if not isinstance(current, dict) or not isinstance(current.get("function"), dict):
            normalized.append(current)
            continue
        function = dict(current["function"])
        name = str(function.get("name") or "")
        if name:
            function["name"] = name_map.get(name) or safe_tool_name(name)
        current["function"] = function
        normalized.append(current)
    return normalized


def safe_tool_name(name: str) -> str:
    if _TOOL_NAME_PATTERN.fullmatch(name):
        return name
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name).strip("_") or "tool"


def sanitize_tools(
    tools: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]] | None, dict[str, str]]:
    if not tools:
        return tools, {}
    result = []
    aliases: dict[str, str] = {}
    used: set[str] = set()
    for index, raw in enumerate(tools):
        tool = dict(raw)
        function = dict(tool.get("function") or {})
        original = str(function.get("name") or f"tool_{index}")
        alias = safe_tool_name(original)
        suffix = 2
        base = alias
        while alias in used and aliases.get(alias) != original:
            alias = f"{base}_{suffix}"
            suffix += 1
        used.add(alias)
        if alias != original:
            aliases[alias] = original
        function["name"] = alias
        tool["function"] = function
        result.append(tool)
    return result, aliases


def restore_tool_name(name: str, aliases: dict[str, str]) -> str:
    return aliases.get(name, name)


def extract_status_code(exc: Exception) -> int | None:
    for attribute in ("status_code", "status", "http_status"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    text = str(exc)
    for status in (401, 403, 404, 408, 409, 425, 429, 500, 502, 503, 504):
        if str(status) in text:
            return status
    return None


def classify_error(exc: Exception) -> tuple[str, bool]:
    text = str(exc).lower()
    status = extract_status_code(exc)
    if status == 429 or any(value in text for value in ("rate limit", "too many requests")):
        return "rate_limit", True
    if any(value in text for value in ("insufficient", "credit", "billing", "payment")):
        return "billing", False
    if status in {401, 403} or any(
        value in text for value in ("unauthorized", "invalid api key", "forbidden")
    ):
        return "auth", False
    if status == 408 or any(value in text for value in ("timeout", "timed out")):
        return "timeout", True
    return "unknown", status is None or status >= 500 or status in {409, 425}


def error_metadata(exc: Exception) -> dict[str, Any]:
    kind, retryable = classify_error(exc)
    return {
        "error_kind": kind,
        "error_code": str(getattr(exc, "code", None) or type(exc).__name__),
        "error_status": extract_status_code(exc),
        "retryable": retryable,
    }


def user_friendly_error(exc: Exception, *, model: str | None = None) -> str:
    kind, _ = classify_error(exc)
    status = extract_status_code(exc)
    if status == 404:
        message = "Error calling model: model or endpoint not found (404)."
    elif kind == "rate_limit":
        message = "Error calling model: rate limited by the provider. Retry later."
    elif kind == "billing":
        message = "Error calling model: insufficient provider credits or quota."
    elif kind == "auth":
        message = "Error calling model: provider authentication failed."
    elif kind == "timeout":
        message = "Error calling model: the provider request timed out. Retry later."
    else:
        message = "Error calling model: provider request failed."
    logger.warning(
        "Model call failed: model={} category={} detail={}",
        model or "unknown",
        kind,
        sanitize_error_message(str(exc))[:1200],
    )
    return message
