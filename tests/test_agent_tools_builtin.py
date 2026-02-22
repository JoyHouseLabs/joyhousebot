"""Tests for built-in agent tools: read_file, write_file, edit_file, list_dir, message."""

from pathlib import Path

import pytest

from joyhousebot.agent.tools.filesystem import (
    EditFileTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from joyhousebot.agent.tools.message import MessageTool


# ---- read_file ----
@pytest.mark.asyncio
async def test_read_file_success(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    tool = ReadFileTool(allowed_dir=tmp_path)
    out = await tool.execute(path=str(tmp_path / "a.txt"))
    assert out == "hello"


@pytest.mark.asyncio
async def test_read_file_not_found(tmp_path: Path) -> None:
    tool = ReadFileTool(allowed_dir=tmp_path)
    out = await tool.execute(path="nonexistent.txt")
    assert "not found" in out or "Error" in out


@pytest.mark.asyncio
async def test_read_file_outside_allowed_dir(tmp_path: Path) -> None:
    tool = ReadFileTool(allowed_dir=tmp_path)
    out = await tool.execute(path="/etc/hostname")
    assert "Error" in out or "outside" in out.lower()


# ---- write_file ----
@pytest.mark.asyncio
async def test_write_file_success(tmp_path: Path) -> None:
    tool = WriteFileTool(allowed_dir=tmp_path)
    out = await tool.execute(path=str(tmp_path / "out.txt"), content="written")
    assert "Successfully" in out or "wrote" in out.lower()
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "written"


@pytest.mark.asyncio
async def test_write_file_creates_parent_dirs(tmp_path: Path) -> None:
    tool = WriteFileTool(allowed_dir=tmp_path)
    await tool.execute(path=str(tmp_path / "sub" / "dir" / "file.txt"), content="ok")
    assert (tmp_path / "sub" / "dir" / "file.txt").read_text(encoding="utf-8") == "ok"


@pytest.mark.asyncio
async def test_write_file_outside_allowed_dir(tmp_path: Path) -> None:
    tool = WriteFileTool(allowed_dir=tmp_path)
    out = await tool.execute(path="/tmp/outside.txt", content="x")
    assert "Error" in out or "outside" in out.lower()


# ---- edit_file ----
@pytest.mark.asyncio
async def test_edit_file_success(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("hello world", encoding="utf-8")
    tool = EditFileTool(allowed_dir=tmp_path)
    out = await tool.execute(path=str(tmp_path / "f.txt"), old_text="hello", new_text="hi")
    assert "Successfully" in out or "edited" in out.lower()
    assert (tmp_path / "f.txt").read_text(encoding="utf-8") == "hi world"


@pytest.mark.asyncio
async def test_edit_file_old_text_not_found(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("hello", encoding="utf-8")
    tool = EditFileTool(allowed_dir=tmp_path)
    out = await tool.execute(path=str(tmp_path / "f.txt"), old_text="xyz", new_text="a")
    assert "not found" in out or "Error" in out


# ---- list_dir ----
@pytest.mark.asyncio
async def test_list_dir_success(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("")
    (tmp_path / "b.txt").write_text("")
    (tmp_path / "sub").mkdir()
    tool = ListDirTool(allowed_dir=tmp_path)
    out = await tool.execute(path=str(tmp_path))
    assert "a.txt" in out and "b.txt" in out and "sub" in out


@pytest.mark.asyncio
async def test_list_dir_not_a_directory(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("")
    tool = ListDirTool(allowed_dir=tmp_path)
    out = await tool.execute(path=str(tmp_path / "file.txt"))
    assert "Error" in out or "directory" in out.lower()


# ---- message ----
@pytest.mark.asyncio
async def test_message_calls_callback() -> None:
    seen: list[str] = []

    async def send(msg: object) -> None:
        if hasattr(msg, "content"):
            seen.append(getattr(msg, "content", ""))

    tool = MessageTool(
        send_callback=send,
        default_channel="test",
        default_chat_id="test-chat",
    )
    out = await tool.execute(content="test content")
    assert "test content" in seen
    assert "sent" in out.lower() or "Message" in out


@pytest.mark.asyncio
async def test_message_no_callback_returns_error() -> None:
    tool = MessageTool(send_callback=None, default_channel="c", default_chat_id="id")
    out = await tool.execute(content="hello")
    assert "Error" in out or "not configured" in out.lower()


@pytest.mark.asyncio
async def test_message_no_target_returns_error() -> None:
    async def noop(_: object) -> None:
        pass

    tool = MessageTool(send_callback=noop)
    out = await tool.execute(content="hello")
    assert "Error" in out or "target" in out.lower() or "channel" in out.lower()
