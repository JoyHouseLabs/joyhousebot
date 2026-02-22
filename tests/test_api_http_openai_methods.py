from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from joyhousebot.api.http.openai_methods import (
    build_openai_chat_completion_response_content,
    build_openai_non_streaming_response,
    build_openai_prompt,
    build_openai_sse_chunk,
    build_openai_sse_error_chunk,
    build_openai_streaming_response,
    openai_message_content_to_text,
)


def test_openai_message_content_to_text_str_and_parts():
    assert openai_message_content_to_text("hi") == "hi"
    assert openai_message_content_to_text([{"type": "text", "text": "a"}, "b"]) == "a\nb"


def test_build_openai_prompt_prefers_last_user_message():
    messages = [
        SimpleNamespace(role="system", content="sys"),
        SimpleNamespace(role="user", content="u1"),
        SimpleNamespace(role="user", content=[{"text": "u2"}]),
    ]
    assert build_openai_prompt(messages) == "u2"


def test_build_openai_chat_completion_response_content():
    payload = build_openai_chat_completion_response_content(
        completion_id="id1",
        created=1,
        model="m1",
        response_text="hello",
    )
    assert payload["id"] == "id1"
    assert payload["choices"][0]["message"]["content"] == "hello"
    assert payload["usage"]["total_tokens"] == 0


def test_build_openai_sse_chunk_and_error_chunk():
    chunk = build_openai_sse_chunk(
        completion_id="id1",
        model="m1",
        created=1,
        delta_content="hello",
    )
    assert chunk.startswith("data: ")
    assert '"finish_reason": null' in chunk
    assert '"content": "hello"' in chunk

    err = build_openai_sse_error_chunk(
        completion_id="id1",
        model="m1",
        created=1,
        error_message="boom",
    )
    assert err.startswith("data: ")
    assert '"finish_reason": "error"' in err
    assert '"content": "Error: boom"' in err


class _StreamAgent:
    def __init__(self, *, fail: bool = False):
        self.fail = fail

    async def process_direct(self, *, stream_callback, **_kwargs):
        if self.fail:
            raise RuntimeError("boom")
        await stream_callback("a")
        await stream_callback("b")
        return "ab"


class _NonStreamAgent:
    def __init__(self, *, fail: bool = False):
        self.fail = fail

    async def process_direct(self, **_kwargs):
        if self.fail:
            raise RuntimeError("boom")
        return "hello"


@pytest.mark.asyncio
async def test_build_openai_streaming_response_success():
    response = build_openai_streaming_response(
        agent=_StreamAgent(),
        prompt="p",
        session_key="s",
        completion_id="id1",
        model="m1",
        created=1,
        log_exception=lambda _fmt, _exc: None,
    )
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    text = "".join(chunks)
    assert '"content": "a"' in text
    assert '"content": "b"' in text
    assert "data: [DONE]" in text


@pytest.mark.asyncio
async def test_build_openai_streaming_response_error():
    response = build_openai_streaming_response(
        agent=_StreamAgent(fail=True),
        prompt="p",
        session_key="s",
        completion_id="id1",
        model="m1",
        created=1,
        log_exception=lambda _fmt, _exc: None,
    )
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    text = "".join(chunks)
    assert '"finish_reason": "error"' in text
    assert '"content": "Error: boom"' in text
    assert "data: [DONE]" in text


@pytest.mark.asyncio
async def test_build_openai_non_streaming_response_success():
    response = await build_openai_non_streaming_response(
        agent=_NonStreamAgent(),
        prompt="p",
        session_key="s",
        completion_id="id1",
        created=1,
        model="m1",
        log_error=lambda _msg: None,
        error_detail=lambda e: str(e),
    )
    assert response.status_code == 200
    assert response.body
    assert b'"id":"id1"' in response.body
    assert b'"content":"hello"' in response.body


@pytest.mark.asyncio
async def test_build_openai_non_streaming_response_error():
    with pytest.raises(HTTPException) as exc:
        await build_openai_non_streaming_response(
            agent=_NonStreamAgent(fail=True),
            prompt="p",
            session_key="s",
            completion_id="id1",
            created=1,
            model="m1",
            log_error=lambda _msg: None,
            error_detail=lambda e: str(e),
        )
    assert exc.value.status_code == 500
    assert exc.value.detail == "boom"

