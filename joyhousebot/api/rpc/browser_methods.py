"""RPC handlers for browser request proxy methods."""

from __future__ import annotations

import json
import uuid
from typing import Any, Callable

import httpx


RpcResult = tuple[bool, Any | None, dict[str, Any] | None]

# Error dict for run_browser_request: {"code": str, "message": str, "details": dict | None}
BrowserRequestError = dict[str, Any]


async def run_browser_request(
    *,
    config: Any,
    node_registry: Any,
    resolve_browser_node: Callable[[list[Any], str], Any | None],
    resolve_node_command_allowlist: Callable[[Any, Any], list[str] | None],
    is_node_command_allowed: Callable[[str, list[str], list[str] | None], tuple[bool, str]],
    persist_browser_proxy_files: Callable[[list[dict[str, Any]] | None], dict[str, str]],
    apply_browser_proxy_paths: Callable[[dict[str, Any], dict[str, str]], None],
    browser_control_url: str,
    method_raw: str,
    path: str,
    query: dict[str, Any] | None,
    body: Any,
    timeout_ms: int,
    idempotency_key: str | None = None,
    requested_node: str = "",
) -> tuple[bool, Any, BrowserRequestError | None]:
    """
    Execute one browser request (node proxy or HTTP to browser_control_url).
    Returns (ok, result, error_dict). When ok is False, error_dict has "code", "message", "details".
    requested_node: optional nodeId from the request (RPC param); used as resolve target when set.
    """
    browser_mode = str(getattr(config.gateway, "node_browser_mode", "auto") or "auto").strip().lower()
    browser_target = str(getattr(config.gateway, "node_browser_target", "") or "").strip()
    target = (requested_node or browser_target).strip()
    if browser_mode == "off":
        return False, None, {"code": "UNAVAILABLE", "message": "browser control is disabled", "details": None}

    connected_nodes = node_registry.list_connected()
    node_target = resolve_browser_node(connected_nodes, target)
    if node_target is None and browser_mode == "manual":
        return False, None, {"code": "UNAVAILABLE", "message": "no browser node selected", "details": None}
    if node_target is None:
        browser_nodes = [
            n for n in connected_nodes
            if ("browser" in (n.caps or [])) or ("browser.proxy" in (n.commands or []))
        ]
        if len(browser_nodes) == 1:
            node_target = browser_nodes[0]

    if node_target is None:
        if browser_control_url:
            target_url = f"{browser_control_url.rstrip('/')}/{path.lstrip('/')}"
            try:
                async with httpx.AsyncClient(timeout=max(1, timeout_ms) / 1000.0) as http:
                    res = await http.request(
                        method_raw,
                        target_url,
                        params=query,
                        json=body if method_raw != "GET" else None,
                    )
                if res.status_code >= 400:
                    return (
                        False,
                        None,
                        {
                            "code": "UNAVAILABLE" if res.status_code >= 500 else "INVALID_REQUEST",
                            "message": f"browser request failed ({res.status_code})",
                            "details": {"status": res.status_code, "body": res.text[:2000]},
                        },
                    )
                try:
                    return True, res.json(), None
                except Exception:
                    return True, {"ok": True, "text": res.text}, None
            except Exception as exc:
                return False, None, {"code": "UNAVAILABLE", "message": str(exc), "details": None}
        return False, None, {"code": "UNAVAILABLE", "message": "No connected browser-capable nodes", "details": None}

    allowlist = resolve_node_command_allowlist(config, node_target)
    allowed, reason = is_node_command_allowed("browser.proxy", node_target.commands, allowlist)
    if not allowed:
        return (
            False,
            None,
            {"code": "INVALID_REQUEST", "message": "node command not allowed", "details": {"reason": reason, "command": "browser.proxy"}},
        )

    proxy_params = {
        "method": method_raw,
        "path": path,
        "query": query,
        "body": body,
        "timeoutMs": timeout_ms,
    }
    res = await node_registry.invoke(
        node_id=node_target.node_id,
        command="browser.proxy",
        params=proxy_params,
        timeout_ms=max(100, timeout_ms),
        idempotency_key=idempotency_key or uuid.uuid4().hex,
    )
    if not res.ok:
        err = res.error or {"code": "UNAVAILABLE", "message": "browser proxy failed"}
        return (
            False,
            None,
            {
                "code": str(err.get("code") or "UNAVAILABLE"),
                "message": str(err.get("message") or "browser proxy failed"),
                "details": {"nodeId": node_target.node_id, "command": "browser.proxy"},
            },
        )

    payload = res.payload
    if res.payload_json and not isinstance(payload, dict):
        try:
            payload = json.loads(res.payload_json)
        except Exception:
            payload = res.payload
    if isinstance(payload, dict) and "result" in payload:
        result = payload.get("result")
        files = payload.get("files")
        files_list = files if isinstance(files, list) else None
        mapping = persist_browser_proxy_files(files_list)
        if isinstance(result, dict) and mapping:
            apply_browser_proxy_paths(result, mapping)
        return True, result, None
    return True, payload, None


async def try_handle_browser_method(
    *,
    method: str,
    params: dict[str, Any],
    config: Any,
    node_registry: Any,
    rpc_error: Callable[[str, str, dict[str, Any] | None], dict[str, Any]],
    resolve_browser_node: Callable[[list[Any], str], Any | None],
    resolve_node_command_allowlist: Callable[[Any, Any], list[str] | None],
    is_node_command_allowed: Callable[[str, list[str], list[str] | None], tuple[bool, str]],
    persist_browser_proxy_files: Callable[[list[dict[str, Any]] | None], dict[str, str]],
    apply_browser_proxy_paths: Callable[[dict[str, Any], dict[str, str]], None],
    browser_control_url: str,
) -> RpcResult | None:
    """Handle browser.request method. Return None for unrelated methods."""
    if method != "browser.request":
        return None

    method_raw = str(params.get("method") or "").strip().upper()
    path = str(params.get("path") or "").strip()
    if method_raw not in {"GET", "POST", "DELETE"}:
        return False, None, rpc_error("INVALID_REQUEST", "method must be GET, POST, or DELETE", None)
    if not path:
        return False, None, rpc_error("INVALID_REQUEST", "path is required", None)

    timeout_ms = int(params.get("timeoutMs") or 30000)
    query = params.get("query") if isinstance(params.get("query"), dict) else None
    body = params.get("body")

    ok, result, err = await run_browser_request(
        config=config,
        node_registry=node_registry,
        resolve_browser_node=resolve_browser_node,
        resolve_node_command_allowlist=resolve_node_command_allowlist,
        is_node_command_allowed=is_node_command_allowed,
        persist_browser_proxy_files=persist_browser_proxy_files,
        apply_browser_proxy_paths=apply_browser_proxy_paths,
        browser_control_url=browser_control_url,
        method_raw=method_raw,
        path=path,
        query=query,
        body=body,
        timeout_ms=timeout_ms,
        idempotency_key=str(params.get("idempotencyKey") or uuid.uuid4().hex),
        requested_node=str(params.get("nodeId") or "").strip(),
    )
    if not ok and err:
        return False, None, rpc_error(err["code"], err["message"], err.get("details"))
    return True, result, None

