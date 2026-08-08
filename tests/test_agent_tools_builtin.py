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
from joyhousebot.capabilities.tool_adapter import ToolInvocationError
from joyhousebot.runtime.context import ToolExecutionContext
from tests.support.postgres_store import PostgresTestStore


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


@pytest.mark.asyncio
async def test_run_scoped_files_are_isolated_between_users(tmp_path: Path) -> None:
    writer = WriteFileTool(allowed_dir=tmp_path, workspace=tmp_path)
    reader = ReadFileTool(allowed_dir=tmp_path, workspace=tmp_path)
    context_a = ToolExecutionContext(
        run_id="run-a",
        root_run_id="root-a",
        session_key="a",
        channel="api",
        chat_id="chat-a",
        user_id="user-a",
    )
    context_b = ToolExecutionContext(
        run_id="run-b",
        root_run_id="root-b",
        session_key="b",
        channel="api",
        chat_id="chat-b",
        user_id="user-b",
    )

    await writer.execute(path="result.txt", content="private-a", tool_context=context_a)
    assert await reader.execute(path="result.txt", tool_context=context_a) == "private-a"
    denied = await reader.execute(path="result.txt", tool_context=context_b)
    assert "not found" in denied.lower()


@pytest.mark.asyncio
async def test_memory_paths_are_virtual_durable_and_scope_isolated(tmp_path: Path) -> None:
    runtime_store = PostgresTestStore(tmp_path / "runtime.db")
    writer = WriteFileTool(
        allowed_dir=tmp_path,
        workspace=tmp_path,
        runtime_store=runtime_store,
    )
    reader = ReadFileTool(
        allowed_dir=tmp_path,
        workspace=tmp_path,
        runtime_store=runtime_store,
    )
    lister = ListDirTool(
        allowed_dir=tmp_path,
        workspace=tmp_path,
        runtime_store=runtime_store,
    )
    context_a = ToolExecutionContext(
        run_id="run-a",
        session_key="a",
        channel="api",
        chat_id="chat-a",
        user_id="user-a",
        memory_scope="user:user-a:agent:default",
    )
    context_b = ToolExecutionContext(
        run_id="run-b",
        session_key="b",
        channel="api",
        chat_id="chat-b",
        user_id="user-b",
        memory_scope="user:user-b:agent:default",
    )

    await writer.execute(
        path="memory/notes/private.md",
        content="private-a",
        tool_context=context_a,
    )

    assert (
        await reader.execute(
            path="memory/notes/private.md",
            tool_context=context_a,
        )
        == "private-a"
    )
    assert "private.md" in await lister.execute(
        path="memory/notes",
        tool_context=context_a,
    )
    assert (
        "not found"
        in (
            await reader.execute(
                path="memory/notes/private.md",
                tool_context=context_b,
            )
        ).lower()
    )
    assert not (tmp_path / "memory" / "notes" / "private.md").exists()


@pytest.mark.asyncio
async def test_memory_paths_without_scope_fail_closed_and_touch_no_host_fs(
    tmp_path: Path,
) -> None:
    """memory_scope=None means memory is not configured: fail with a clear
    error and never create or read host directories."""
    runtime_store = PostgresTestStore(tmp_path / "runtime.db")
    writer = WriteFileTool(allowed_dir=tmp_path, workspace=tmp_path, runtime_store=runtime_store)
    reader = ReadFileTool(allowed_dir=tmp_path, workspace=tmp_path, runtime_store=runtime_store)
    editor = EditFileTool(allowed_dir=tmp_path, workspace=tmp_path, runtime_store=runtime_store)
    lister = ListDirTool(allowed_dir=tmp_path, workspace=tmp_path, runtime_store=runtime_store)
    context = ToolExecutionContext(
        run_id="run-a",
        session_key="a",
        channel="api",
        chat_id="chat-a",
        user_id="user-a",
    )

    out = await writer.execute(
        path="memory/notes/x.md", content="secret", tool_context=context
    )
    assert "memory write is unavailable" in out
    out = await reader.execute(path="memory/notes/x.md", tool_context=context)
    assert "memory read is unavailable" in out
    out = await editor.execute(
        path="memory/notes/x.md", old_text="a", new_text="b", tool_context=context
    )
    assert "memory write is unavailable" in out
    out = await lister.execute(path="memory/notes", tool_context=context)
    assert "memory read is unavailable" in out
    assert not (tmp_path / "memory").exists()


@pytest.mark.asyncio
async def test_memory_paths_without_runtime_store_fail_closed(tmp_path: Path) -> None:
    """Even with a scope key, a missing durable store must not downgrade to
    the host filesystem."""
    writer = WriteFileTool(allowed_dir=tmp_path, workspace=tmp_path)
    context = ToolExecutionContext(
        run_id="run-a",
        session_key="a",
        channel="api",
        chat_id="chat-a",
        user_id="user-a",
        memory_scope="user:user-a:agent:default",
    )
    out = await writer.execute(
        path="memory/notes/x.md", content="secret", tool_context=context
    )
    assert "memory write is unavailable" in out
    assert not (tmp_path / "memory").exists()


@pytest.mark.asyncio
async def test_memory_paths_shared_scope_is_db_backed_and_cross_user(tmp_path: Path) -> None:
    """memory_scope="shared" is an explicit project-wide opt-in backed by the
    DB "shared" scope: visible across users, isolated from per-user scopes,
    and never materialized on the host filesystem."""
    runtime_store = PostgresTestStore(tmp_path / "runtime.db")
    writer = WriteFileTool(allowed_dir=tmp_path, workspace=tmp_path, runtime_store=runtime_store)
    reader = ReadFileTool(allowed_dir=tmp_path, workspace=tmp_path, runtime_store=runtime_store)
    shared_a = ToolExecutionContext(
        run_id="run-a",
        session_key="a",
        channel="api",
        chat_id="chat-a",
        user_id="user-a",
        memory_scope="shared",
    )
    shared_b = ToolExecutionContext(
        run_id="run-b",
        session_key="b",
        channel="api",
        chat_id="chat-b",
        user_id="user-b",
        memory_scope="shared",
    )
    user_scoped = ToolExecutionContext(
        run_id="run-c",
        session_key="c",
        channel="api",
        chat_id="chat-c",
        user_id="user-b",
        memory_scope="user:user-b:agent:default",
    )

    await writer.execute(
        path="memory/notes/common.md", content="shared-fact", tool_context=shared_a
    )
    assert (
        await reader.execute(path="memory/notes/common.md", tool_context=shared_b)
        == "shared-fact"
    )
    out = await reader.execute(path="memory/notes/common.md", tool_context=user_scoped)
    assert "not found" in out.lower()
    assert not (tmp_path / "memory").exists()


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

    tool = MessageTool(send_callback=send)
    out = await tool.execute(
        content="test content",
        tool_context=ToolExecutionContext(
            run_id="run",
            session_key="session",
            channel="test",
            chat_id="test-chat",
            user_id="user",
        ),
    )
    assert "test content" in seen
    assert "sent" in out.lower() or "Message" in out


@pytest.mark.asyncio
async def test_message_no_callback_returns_error() -> None:
    tool = MessageTool(send_callback=None)
    with pytest.raises(ToolInvocationError, match="not configured") as captured:
        await tool.execute(
            content="hello",
            tool_context=ToolExecutionContext(
                run_id="run", session_key="session", channel="c", chat_id="id"
            ),
        )
    assert captured.value.code == "CAPABILITY_UNAVAILABLE"


@pytest.mark.asyncio
async def test_message_requires_run_context() -> None:
    async def noop(_: object) -> None:
        pass

    tool = MessageTool(send_callback=noop)
    with pytest.raises(ToolInvocationError, match="run context"):
        await tool.execute(content="hello")


# ---- atomic writes & unrestricted warnings ----
@pytest.mark.asyncio
async def test_write_file_is_atomic_and_leaves_no_temp_files(tmp_path: Path) -> None:
    tool = WriteFileTool(allowed_dir=tmp_path)
    target = tmp_path / "atomic.txt"
    await tool.execute(path=str(target), content="v1")
    await tool.execute(path=str(target), content="v2")
    assert target.read_text(encoding="utf-8") == "v2"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.asyncio
async def test_edit_file_is_atomic_and_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "edit.txt"
    target.write_text("hello world", encoding="utf-8")
    tool = EditFileTool(allowed_dir=tmp_path)
    out = await tool.execute(path=str(target), old_text="world", new_text="there")
    assert "Successfully" in out
    assert target.read_text(encoding="utf-8") == "hello there"
    assert not list(tmp_path.glob("*.tmp"))


def test_file_tools_construct_without_allowed_dir_logs_warning() -> None:
    """allowed_dir=None is allowed but must only warn, not fail."""
    ReadFileTool(allowed_dir=None)
    WriteFileTool(allowed_dir=None)
    EditFileTool(allowed_dir=None)
    ListDirTool(allowed_dir=None)
