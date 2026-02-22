"""Shared control-plane state service."""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable

from joyhousebot.services.errors import ServiceError


async def try_handle_control_state(
    *,
    method: str,
    params: dict[str, Any],
    config: Any,
    app_state: dict[str, Any],
    emit_event: Callable[[str, Any], Awaitable[None]] | None,
    load_persistent_state: Callable[[str, Any], Any],
    save_persistent_state: Callable[[str, Any], None],
    now_ms: Callable[[], int],
    build_skills_status_report: Callable[[Any], dict[str, Any]],
    build_channels_status_snapshot: Callable[[Any, Any], dict[str, Any]],
    get_cached_config: Callable[..., Any],
    save_config: Callable[[Any], None],
) -> dict[str, Any] | None:
    if method == "skills.status":
        return build_skills_status_report(config)
    if method == "skills.update":
        return _skills_update(
            params=params,
            app_state=app_state,
            get_cached_config=get_cached_config,
            save_config=save_config,
        )
    if method == "skills.install":
        return {"ok": True, "message": "install queued (compat mode)"}
    if method == "talk.config":
        return _talk_config(params=params, load_persistent_state=load_persistent_state, save_persistent_state=save_persistent_state)
    if method == "voicewake.get":
        return load_persistent_state("rpc.voicewake", {"enabled": False, "keyword": "hey joyhouse"})
    if method == "voicewake.set":
        return await _voicewake_set(
            params=params,
            load_persistent_state=load_persistent_state,
            save_persistent_state=save_persistent_state,
            emit_event=emit_event,
        )
    if method == "wizard.start":
        return _wizard_start(now_ms=now_ms, save_persistent_state=save_persistent_state)
    if method == "wizard.next":
        return _wizard_next(now_ms=now_ms, load_persistent_state=load_persistent_state, save_persistent_state=save_persistent_state)
    if method == "tts.status":
        return load_persistent_state("rpc.tts", {"enabled": False, "provider": "none"})
    if method == "tts.providers":
        return {"providers": [{"id": "none", "name": "Disabled"}], "default": "none"}
    if method == "tts.enable":
        state = load_persistent_state("rpc.tts", {"enabled": False, "provider": "none"})
        state["enabled"] = True
        save_persistent_state("rpc.tts", state)
        return {"ok": True, **state}
    if method == "tts.disable":
        state = load_persistent_state("rpc.tts", {"enabled": False, "provider": "none"})
        state["enabled"] = False
        save_persistent_state("rpc.tts", state)
        return {"ok": True, **state}
    if method == "tts.convert":
        text = str(params.get("text") or "").strip()
        if not text:
            raise ServiceError(code="INVALID_REQUEST", message="text required")
        return {"ok": True, "audioBase64": "", "format": "wav", "message": "tts provider not configured"}
    if method == "channels.status":
        return build_channels_status_snapshot(config, app_state.get("channel_manager"))
    if method == "channels.logout":
        channel = params.get("channel")
        return {"ok": True, "channel": channel, "logged_out": False, "message": "logout not supported"}
    return None


def _skills_update(
    *,
    params: dict[str, Any],
    app_state: dict[str, Any],
    get_cached_config: Callable[..., Any],
    save_config: Callable[[Any], None],
) -> dict[str, Any]:
    skill_key = str(params.get("skillKey") or params.get("name") or "").strip()
    if not skill_key:
        raise ServiceError(code="INVALID_REQUEST", message="skills.update requires skillKey")
    enabled = params.get("enabled")
    if enabled is not None:
        from joyhousebot.config.schema import SkillEntryConfig

        cfg = get_cached_config(force_reload=True)
        if cfg.skills.entries is None:
            cfg.skills.entries = {}
        existing = cfg.skills.entries.get(skill_key)
        existing_env = getattr(existing, "env", None) if existing else None
        cfg.skills.entries[skill_key] = SkillEntryConfig(enabled=bool(enabled), env=existing_env)
        save_config(cfg)
        app_state["config"] = cfg
    return {"ok": True, "skillKey": skill_key}


def _talk_config(
    *,
    params: dict[str, Any],
    load_persistent_state: Callable[[str, Any], Any],
    save_persistent_state: Callable[[str, Any], None],
) -> dict[str, Any]:
    state = load_persistent_state("rpc.talk_config", {"enabled": True, "voice": "default"})
    if isinstance(params, dict) and params:
        state.update({k: v for k, v in params.items() if k in {"enabled", "voice", "language", "speed"}})
        save_persistent_state("rpc.talk_config", state)
    return state


async def _voicewake_set(
    *,
    params: dict[str, Any],
    load_persistent_state: Callable[[str, Any], Any],
    save_persistent_state: Callable[[str, Any], None],
    emit_event: Callable[[str, Any], Awaitable[None]] | None,
) -> dict[str, Any]:
    state = load_persistent_state("rpc.voicewake", {"enabled": False, "keyword": "hey joyhouse"})
    state["enabled"] = bool(params.get("enabled", state.get("enabled", False)))
    if "keyword" in params:
        state["keyword"] = str(params.get("keyword") or state.get("keyword") or "hey joyhouse")
    save_persistent_state("rpc.voicewake", state)
    if emit_event:
        await emit_event("voicewake", state)
    return {"ok": True, **state}


def _wizard_start(*, now_ms: Callable[[], int], save_persistent_state: Callable[[str, Any], None]) -> dict[str, Any]:
    wizard = {
        "sessionId": f"wiz_{uuid.uuid4().hex[:8]}",
        "step": "welcome",
        "done": False,
        "ts": now_ms(),
    }
    save_persistent_state("rpc.wizard", wizard)
    return wizard


def _wizard_next(
    *,
    now_ms: Callable[[], int],
    load_persistent_state: Callable[[str, Any], Any],
    save_persistent_state: Callable[[str, Any], None],
) -> dict[str, Any]:
    wizard = load_persistent_state(
        "rpc.wizard",
        {"sessionId": f"wiz_{uuid.uuid4().hex[:8]}", "step": "welcome", "done": False},
    )
    current = str(wizard.get("step") or "welcome")
    flow = ["welcome", "pair-device", "select-agent", "done"]
    idx = flow.index(current) if current in flow else 0
    nxt = flow[min(idx + 1, len(flow) - 1)]
    wizard["step"] = nxt
    wizard["done"] = nxt == "done"
    wizard["ts"] = now_ms()
    save_persistent_state("rpc.wizard", wizard)
    return wizard

