"""Local browser control HTTP server (OpenClaw-compatible API)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import JSONResponse

from joyhousebot.browser.snapshot import (
    DEFAULT_AI_SNAPSHOT_MAX_CHARS,
    accessibility_tree_from_cdp_get_full_ax_tree,
    snapshot_from_accessibility,
)
from joyhousebot.browser.state import get_browser_state


async def _get_accessibility_tree(page: Any) -> dict[str, Any]:
    """
    Get accessibility tree for the page. Uses page.accessibility.snapshot() if
    available (Playwright Node), else CDP Accessibility.getFullAXTree (e.g. Python).
    """
    acc = getattr(page, "accessibility", None)
    if acc is not None:
        try:
            tree = await acc.snapshot()
            if tree is not None:
                return tree
        except Exception:
            pass
    try:
        cdp = await page.context.new_cdp_session(page)
        raw = await cdp.send("Accessibility.getFullAXTree")
        return accessibility_tree_from_cdp_get_full_ax_tree(raw)
    except Exception:
        return {"role": "generic", "name": "", "value": "", "children": []}

# Media dir for screenshots/PDFs (under ~/.joyhousebot/browser)
def _media_dir() -> Path:
    d = Path.home() / ".joyhousebot" / "browser"
    d.mkdir(parents=True, exist_ok=True)
    return d


def create_browser_app(
    *,
    executable_path: str = "",
    headless: bool = False,
    default_profile: str = "default",
) -> FastAPI:
    """Create FastAPI app for browser control (loopback only)."""
    app = FastAPI(title="Joyhousebot Browser Control")

    def _profile_from_query(req: Request) -> str:
        return (req.query_params.get("profile") or default_profile).strip() or default_profile

    @app.get("/")
    async def status(request: Request) -> dict:
        state = get_browser_state()
        profile = _profile_from_query(request)
        return {
            "enabled": True,
            "profile": profile,
            "running": state.running,
            "cdpReady": state.running,
            "cdpHttp": state.running,
        }

    @app.post("/start")
    async def start(request: Request) -> dict:
        state = get_browser_state()
        await state.ensure_browser(executable_path=executable_path, headless=headless)
        return {"ok": True}

    @app.post("/stop")
    async def stop() -> dict:
        state = get_browser_state()
        await state.stop()
        return {"ok": True}

    @app.get("/profiles")
    async def profiles(request: Request) -> dict:
        return {"profiles": [default_profile]}

    @app.get("/tabs")
    async def tabs(request: Request) -> dict:
        state = get_browser_state()
        if not state.running or not state.tabs:
            return {"running": state.running, "tabs": []}
        tab_list = [
            {"targetId": t.target_id, "url": t.url or t.page.url}
            for t in state.tabs
        ]
        return {"running": True, "tabs": tab_list}

    @app.post("/tabs/open")
    async def tabs_open(request: Request) -> dict:
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        url = (body.get("url") or "").strip() or "about:blank"
        state = get_browser_state()
        await state.ensure_browser(executable_path=executable_path, headless=headless)
        tab = await state.add_tab(url=url)
        return {"targetId": tab.target_id, "url": tab.url or url}

    @app.post("/tabs/focus")
    async def tabs_focus(request: Request) -> dict:
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        target_id = (body.get("targetId") or "").strip()
        if not target_id:
            return JSONResponse({"error": "targetId is required"}, status_code=400)
        state = get_browser_state()
        await state.focus_tab(target_id)
        return {"ok": True}

    @app.delete("/tabs/{target_id:path}")
    async def tab_close(target_id: str) -> dict:
        state = get_browser_state()
        await state.close_tab(target_id)
        return {"ok": True}

    @app.get("/snapshot")
    async def snapshot(request: Request) -> dict:
        state = get_browser_state()
        tab = state.get_tab_info(request.query_params.get("targetId"))
        if not tab:
            return JSONResponse({"error": "no tab available"}, status_code=409)
        max_chars = int(request.query_params.get("maxChars") or DEFAULT_AI_SNAPSHOT_MAX_CHARS)
        try:
            acc = await _get_accessibility_tree(tab.page)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        if not acc:
            return {"format": "ai", "snapshot": "", "targetId": tab.target_id, "url": tab.url, "refs": {}}
        text, refs = snapshot_from_accessibility(acc, format="ai", max_chars=max_chars)
        return {
            "format": "ai",
            "snapshot": text,
            "targetId": tab.target_id,
            "url": tab.url,
            "refs": refs,
            "truncated": len(text) >= max_chars,
        }

    def _resolve_locator(page: Any, ref: str, refs: dict[str, dict]) -> Any:
        """Get Playwright locator for ref from current snapshot refs (must be filled by snapshot)."""
        info = refs.get(ref)
        if not info:
            raise ValueError(f"ref not found: {ref}")
        role = info.get("role") or "generic"
        name = (info.get("name") or "").strip()
        nth = int(info.get("nth") or 0)
        if role == "generic" and not name:
            raise ValueError("ref has no role/name")
        loc = page.get_by_role(role, name=name if name else None)
        return loc.nth(nth)

    @app.post("/act")
    async def act(request: Request) -> dict:
        state = get_browser_state()
        tab = state.get_tab_info(None)
        if not tab:
            return JSONResponse({"error": "no tab available"}, status_code=409)
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        kind = (body.get("kind") or "").strip().lower()
        if not kind:
            return JSONResponse({"error": "kind is required"}, status_code=400)
        target_id = (body.get("targetId") or "").strip() or tab.target_id
        tab = state.get_tab_info(target_id) or tab
        page = tab.page

        try:
            if kind == "click":
                ref = (body.get("ref") or "").strip()
                if not ref:
                    return JSONResponse({"error": "ref is required"}, status_code=400)
                acc = await _get_accessibility_tree(page)
                _, refs = snapshot_from_accessibility(acc or {}, format="ai", max_chars=50000)
                loc = _resolve_locator(page, ref, refs)
                double = bool(body.get("doubleClick"))
                if double:
                    await loc.dblclick()
                else:
                    await loc.click()
                return {"ok": True, "targetId": tab.target_id, "url": page.url}
            if kind == "type":
                ref = (body.get("ref") or "").strip()
                if not ref:
                    return JSONResponse({"error": "ref is required"}, status_code=400)
                text = body.get("text")
                if text is None:
                    return JSONResponse({"error": "text is required"}, status_code=400)
                acc = await _get_accessibility_tree(page)
                _, refs = snapshot_from_accessibility(acc or {}, format="ai", max_chars=50000)
                loc = _resolve_locator(page, ref, refs)
                await loc.fill(str(text))
                if body.get("submit"):
                    await loc.press("Enter")
                return {"ok": True, "targetId": tab.target_id}
            if kind == "press":
                key = (body.get("key") or "").strip()
                if not key:
                    return JSONResponse({"error": "key is required"}, status_code=400)
                await page.keyboard.press(key)
                return {"ok": True, "targetId": tab.target_id}
            if kind == "hover":
                ref = (body.get("ref") or "").strip()
                if not ref:
                    return JSONResponse({"error": "ref is required"}, status_code=400)
                acc = await _get_accessibility_tree(page)
                _, refs = snapshot_from_accessibility(acc or {}, format="ai", max_chars=50000)
                loc = _resolve_locator(page, ref, refs)
                await loc.hover()
                return {"ok": True, "targetId": tab.target_id}
            if kind == "close":
                await state.close_tab(tab.target_id)
                return {"ok": True}
            if kind == "navigate" or kind == "resize" or kind == "wait":
                pass  # navigate has own route; others optional
            return JSONResponse({"error": f"unsupported kind: {kind}"}, status_code=400)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    @app.post("/navigate")
    async def navigate(request: Request) -> dict:
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        url = (body.get("url") or "").strip()
        if not url:
            return JSONResponse({"error": "url is required"}, status_code=400)
        state = get_browser_state()
        tab = state.get_tab_info((body.get("targetId") or "").strip())
        if not tab:
            return JSONResponse({"error": "no tab available"}, status_code=409)
        await tab.page.goto(url, wait_until="domcontentloaded")
        tab.url = tab.page.url
        return {"ok": True, "targetId": tab.target_id, "url": tab.page.url}

    @app.post("/screenshot")
    async def screenshot(request: Request) -> dict:
        state = get_browser_state()
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        target_id = (body.get("targetId") or "").strip() or None
        tab = state.get_tab_info(target_id)
        if not tab:
            return JSONResponse({"error": "no tab available"}, status_code=409)
        full_page = bool(body.get("fullPage"))
        img_type = "jpeg" if body.get("type") == "jpeg" else "png"
        ext = ".jpg" if img_type == "jpeg" else ".png"
        out_path = _media_dir() / f"screenshot-{id(tab.page)}{ext}"
        try:
            await tab.page.screenshot(path=str(out_path), full_page=full_page, type=img_type)
            return {"ok": True, "path": str(out_path), "targetId": tab.target_id, "url": tab.url or tab.page.url}
        except Exception as e:
            return JSONResponse({"error": f"screenshot failed: {e}"}, status_code=500)

    @app.post("/pdf")
    async def pdf(request: Request) -> dict:
        state = get_browser_state()
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        target_id = (body.get("targetId") or "").strip() or None
        tab = state.get_tab_info(target_id)
        if not tab:
            return JSONResponse({"error": "no tab available"}, status_code=409)
        out_path = _media_dir() / f"page-{id(tab.page)}.pdf"
        try:
            await tab.page.pdf(path=str(out_path))
            return {"ok": True, "path": str(out_path), "targetId": tab.target_id, "url": tab.url or tab.page.url}
        except Exception as e:
            return JSONResponse({"error": f"pdf failed: {e}"}, status_code=500)

    @app.get("/console")
    async def console(request: Request) -> dict:
        state = get_browser_state()
        tab = state.get_tab_info(request.query_params.get("targetId"))
        if not tab:
            return {"ok": True, "targetId": "", "messages": []}
        return {"ok": True, "targetId": tab.target_id, "messages": []}

    return app
