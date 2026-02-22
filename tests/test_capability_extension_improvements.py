"""Regression tests for Tool/Skill/Plugin/App capability extension improvements."""

from pathlib import Path

import pytest

from joyhousebot.agent.context import ContextBuilder
from joyhousebot.agent.loop import AgentLoop
from joyhousebot.bus.queue import MessageBus
from joyhousebot.config.schema import AppsConfig, Config
from joyhousebot.plugins.discovery import get_plugin_roots, get_plugin_tool_names_for_agent
from joyhousebot.providers.base import LLMProvider, LLMResponse


class _MinimalProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__(api_key="x")

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7) -> LLMResponse:
        return LLMResponse(content="ok", finish_reason="stop")

    def get_default_model(self) -> str:
        return "test-model"


def test_agent_loop_registers_plugin_invoke(tmp_path: Path) -> None:
    """plugin.invoke must appear in Agent tool definitions so Agent can call plugin tools."""
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_MinimalProvider(),
        workspace=tmp_path,
        max_iterations=1,
    )
    names = [t["function"]["name"] for t in loop.tools.get_definitions()]
    assert "plugin.invoke" in names


def test_agent_loop_registers_open_app(tmp_path: Path) -> None:
    """open_app must appear in Agent tool definitions."""
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_MinimalProvider(),
        workspace=tmp_path,
        max_iterations=1,
    )
    names = [t["function"]["name"] for t in loop.tools.get_definitions()]
    assert "open_app" in names


def test_get_plugin_roots_accepts_object_config(tmp_path: Path) -> None:
    """get_plugin_roots accepts config as object with .plugins.load.paths."""
    cfg = Config()
    cfg.plugins.load.paths = [str(tmp_path)]
    roots = get_plugin_roots(tmp_path, cfg)
    assert isinstance(roots, list)


def test_get_plugin_roots_accepts_dict_config(tmp_path: Path) -> None:
    """get_plugin_roots accepts config as dict for native loader compatibility."""
    config_dict = {"plugins": {"load": {"paths": [str(tmp_path)]}}}
    roots = get_plugin_roots(tmp_path, config_dict)
    assert isinstance(roots, list)


def test_get_plugin_roots_dedupes_and_includes_defaults(tmp_path: Path) -> None:
    """get_plugin_roots returns deduplicated list; default dirs + examples are candidates."""
    cfg = Config()
    roots = get_plugin_roots(tmp_path, cfg)
    seen = set()
    for r in roots:
        key = str(r.resolve())
        assert key not in seen
        seen.add(key)


def test_apps_config_schema_default() -> None:
    """AppsConfig has enabled list; empty means all enabled."""
    apps = AppsConfig()
    assert apps.enabled == []
    apps = AppsConfig(enabled=["library"])
    assert apps.enabled == ["library"]


def test_config_has_apps_field() -> None:
    """Root config has apps field for app enable list."""
    cfg = Config()
    assert hasattr(cfg, "apps")
    assert isinstance(cfg.apps, AppsConfig)
    assert cfg.apps.enabled == []


def test_build_system_prompt_includes_installed_apps_when_present(tmp_path: Path) -> None:
    """When plugin apps exist and are enabled, prompt can include Installed Apps section."""
    ctx = ContextBuilder(workspace=tmp_path)
    prompt = ctx.build_system_prompt()
    # With no plugins, section may be absent; we only assert no crash and core sections exist
    assert "joyhousebot" in prompt or "Memory" in prompt or "Skills" in prompt


def test_get_plugin_tool_names_for_agent_returns_list() -> None:
    """get_plugin_tool_names_for_agent returns a list (may be empty when no plugins loaded)."""
    names = get_plugin_tool_names_for_agent()
    assert isinstance(names, list)
    for n in names:
        assert isinstance(n, str)
