from pathlib import Path

import pytest

from porthouse.capabilities.services import CapabilityServiceBroker
from porthouse.config.loader import load_config
from porthouse.extension_sdk import CapabilityContext
from porthouse.extension_sdk.network import validate_url


@pytest.mark.asyncio
async def test_run_scratch_blocks_path_traversal(tmp_path: Path) -> None:
    services = CapabilityServiceBroker(None, scratch_root=tmp_path)
    with pytest.raises(PermissionError):
        await services.scratch.write(
            CapabilityContext(
                user_id="user-a",
                session_id="session-a",
                run_id="run-a",
                agent_id="agent-a",
            ),
            path="../outside.txt",
            content="x",
        )


@pytest.mark.asyncio
async def test_exec_tool_blocks_shell_metacharacters_when_restricted(tmp_path: Path) -> None:
    services = CapabilityServiceBroker(None, scratch_root=tmp_path)
    result = await services.sandbox.execute(
        CapabilityContext(
            user_id="user-a",
            session_id="session-a",
            run_id="run-a",
            agent_id="agent-a",
        ),
        command="echo hello | wc -c",
    )
    assert result["code"] == "COMMAND_BLOCKED"


def test_load_config_raises_on_invalid_json(tmp_path: Path) -> None:
    bad = tmp_path / "config.json"
    bad.write_text("{bad json", encoding="utf-8")

    with pytest.raises(ValueError):
        load_config(bad)


def test_validate_url_blocks_localhost_and_private_ip() -> None:
    ok, _ = validate_url("https://localhost/a")
    assert not ok

    ok, _ = validate_url("https://192.168.1.10/a")
    assert not ok
