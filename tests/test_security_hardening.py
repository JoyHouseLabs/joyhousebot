from pathlib import Path

import pytest

from joyhousebot.agent.tools.filesystem import _resolve_path
from joyhousebot.agent.tools.shell import ExecTool
from joyhousebot.agent.tools.web import _validate_url
from joyhousebot.config.loader import load_config


def test_resolve_path_blocks_outside_allowed_dir(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    target = tmp_path / "outside.txt"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(PermissionError):
        _resolve_path(str(target), allowed)


@pytest.mark.asyncio
async def test_exec_tool_blocks_shell_metacharacters_when_restricted(tmp_path: Path) -> None:
    tool = ExecTool(restrict_to_workspace=True, working_dir=str(tmp_path))
    result = await tool.execute("echo hello | wc -c", working_dir=str(tmp_path))
    assert "shell metacharacters are not allowed" in result


def test_load_config_raises_on_invalid_json(tmp_path: Path) -> None:
    bad = tmp_path / "config.json"
    bad.write_text("{bad json", encoding="utf-8")

    with pytest.raises(ValueError):
        load_config(bad)


def test_validate_url_blocks_localhost_and_private_ip() -> None:
    ok, _ = _validate_url("https://localhost/a")
    assert not ok

    ok, _ = _validate_url("https://192.168.1.10/a")
    assert not ok
