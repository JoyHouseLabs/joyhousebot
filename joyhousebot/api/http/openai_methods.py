"""Helpers for OpenAI-compatible HTTP endpoints."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse


def openai_message_content_to_text(content: str | list[Any]) -> str:
    """Extract plain text from OpenAI message content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") == "text" and "text" in part:
                    parts.append(part["text"])
                elif "text" in part:
                    parts.append(part["text"])
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts) if parts else ""
    return ""


def build_openai_prompt(messages: list[Any]) -> str:
    """Build prompt from OpenAI chat messages."""
    user_texts: list[str] = []
    for m in messages:
        text = openai_message_content_to_text(m.content)
        if not text:
            continue
        if m.role == "user":
            user_texts.append(text)
        elif m.role == "system":
            user_texts.append(f"[System] {text}")
    return user_texts[-1] if user_texts else ""


def build_openai_chat_completion_response_content(
    *,
    completion_id: str,
    created: int,
    model: str,
    response_text: str | None,
) -> dict[str, Any]:
    """Build non-stream OpenAI chat completion JSON payload."""
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": response_text or ""},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


def build_openai_sse_chunk(
    *,
    completion_id: str,
    model: str,
    created: int,
    delta_content: str,
) -> str:
    """Build one SSE chat.completion.chunk line."""
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": {"content": delta_content}, "finish_reason": None}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def build_openai_sse_error_chunk(
    *,
    completion_id: str,
    model: str,
    created: int,
    error_message: str | None,
) -> str:
    """Build one SSE error chunk line."""
    err_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"content": f"Error: {error_message}"},
                "finish_reason": "error",
            }
        ],
    }
    return f"data: {json.dumps(err_chunk, ensure_ascii=False)}\n\n"


def build_openai_streaming_response(
    *,
    agent: Any,
    prompt: str,
    session_key: str,
    completion_id: str,
    model: str,
    created: int,
    log_exception: Any,
) -> StreamingResponse:
    """Build OpenAI-compatible streaming response."""
    queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
    open_app_result: dict[str, Any] | None = None

    async def stream_callback(content: str) -> None:
        await queue.put(("delta", content))

    async def execution_stream_callback(etype: str, payload: dict[str, Any]) -> None:
        if etype == "tool_end" and payload.get("tool") == "open_app":
            r = payload.get("result")
            if isinstance(r, str):
                try:
                    out = json.loads(r)
                    if isinstance(out, dict) and out.get("ok") and out.get("navigate_to"):
                        nonlocal open_app_result
                        open_app_result = out
                except (json.JSONDecodeError, TypeError):
                    pass

    async def run_agent() -> None:
        try:
            full = await agent.process_direct(
                content=prompt,
                session_key=session_key,
                channel="api",
                chat_id="openai",
                stream_callback=stream_callback,
                execution_stream_callback=execution_stream_callback,
            )
            if open_app_result is not None:
                await queue.put(("open_app", open_app_result))
            await queue.put(("done", full))
        except Exception as e:
            log_exception("OpenAI stream agent error: {}", e)
            await queue.put(("error", str(e)))

    async def event_stream():
        task = asyncio.create_task(run_agent())
        try:
            while True:
                kind, payload = await queue.get()
                if kind == "delta" and payload:
                    yield build_openai_sse_chunk(
                        completion_id=completion_id,
                        model=model,
                        created=created,
                        delta_content=payload,
                    )
                elif kind == "open_app":
                    yield f"data: {json.dumps({'open_app_navigate': payload}, ensure_ascii=False)}\n\n"
                elif kind == "done":
                    break
                elif kind == "error":
                    yield build_openai_sse_error_chunk(
                        completion_id=completion_id,
                        model=model,
                        created=created,
                        error_message=payload,
                    )
                    break
            yield "data: [DONE]\n\n"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


async def build_openai_non_streaming_response(
    *,
    agent: Any,
    prompt: str,
    session_key: str,
    completion_id: str,
    created: int,
    model: str,
    log_error: Any,
    error_detail: Any,
) -> JSONResponse:
    """Build OpenAI-compatible non-streaming JSON response."""
    try:
        response_text = await agent.process_direct(
            content=prompt,
            session_key=session_key,
            channel="api",
            chat_id="openai",
        )
    except Exception as e:
        log_error(f"OpenAI chat completions error: {e}")
        raise HTTPException(status_code=500, detail=error_detail(e))
    return JSONResponse(
        status_code=200,
        content=build_openai_chat_completion_response_content(
            completion_id=completion_id,
            created=created,
            model=model,
            response_text=response_text,
        ),
    )

