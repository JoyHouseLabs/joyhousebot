"""Dedicated FastAPI process for model access from untrusted Hosts."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse

from joyhousebot.config.access import get_config
from joyhousebot.model_gateway.schemas import (
    HostModelChatRequest,
    OpenAIChatCompletionRequest,
)
from joyhousebot.model_gateway.service import HostModelGatewayService, ModelGatewayError
from joyhousebot.storage.factory import create_runtime_store


def _bearer(authorization: str) -> str:
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


def create_model_gateway_app(
    *,
    store: Any | None = None,
    config: Any | None = None,
    service: HostModelGatewayService | None = None,
) -> FastAPI:
    injected_store = store
    injected_config = config
    injected_service = service

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        active_config = injected_config or get_config()
        active_store = injected_store or create_runtime_store(active_config)
        app.state.store = active_store
        app.state.gateway = injected_service or HostModelGatewayService(
            store=active_store,
            config=active_config,
        )
        try:
            yield
        finally:
            if injected_store is None:
                active_store.close()

    app = FastAPI(
        title="JoyhouseBot Host Model Gateway",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.exception_handler(ModelGatewayError)
    async def gateway_error_handler(_, exc: ModelGatewayError):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": str(exc)}},
        )

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "role": "host-model-gateway"}

    @app.get("/readyz")
    async def readyz():
        try:
            health = app.state.store.healthcheck()
        except Exception as exc:
            raise HTTPException(status_code=503, detail="PostgreSQL is unavailable") from exc
        if not health.get("ok"):
            raise HTTPException(status_code=503, detail="PostgreSQL is unavailable")
        return {"status": "ready", "role": "host-model-gateway"}

    @app.post("/v1/chat")
    async def chat(
        body: HostModelChatRequest,
        authorization: str = Header(default=""),
    ):
        token = _bearer(authorization)
        if not token:
            raise ModelGatewayError(
                "Host model grant Bearer token is required",
                status_code=401,
                code="unauthorized",
            )
        return await app.state.gateway.chat(token=token, request=body.model_dump())

    @app.post("/v1/chat/completions")
    async def openai_chat_completions(
        body: OpenAIChatCompletionRequest,
        authorization: str = Header(default=""),
        idempotency_key: str = Header(default="", alias="Idempotency-Key"),
        x_request_id: str = Header(default="", alias="X-Request-ID"),
    ):
        token = _bearer(authorization)
        if not token:
            raise ModelGatewayError(
                "Host model grant Bearer token is required",
                status_code=401,
                code="unauthorized",
            )
        request_id = (idempotency_key or x_request_id).strip()
        if not request_id:
            request_id = f"model_request_{uuid4().hex}"
        if not 16 <= len(request_id) <= 256:
            raise ModelGatewayError("model request id must contain 16-256 characters")
        internal = {
            "request_id": request_id,
            "model": body.model,
            "messages": [item.model_dump(exclude_none=True) for item in body.messages],
            "tools": [dict(item) for item in body.tools],
            "max_tokens": body.max_completion_tokens or body.max_tokens or 4096,
            "temperature": body.temperature,
        }
        result = await app.state.gateway.chat(token=token, request=internal)
        completion = _openai_completion(result)
        if not body.stream:
            return completion

        async def stream_response():
            chunk = {
                "id": completion["id"],
                "object": "chat.completion.chunk",
                "created": completion["created"],
                "model": completion["model"],
                "choices": [
                    {
                        "index": 0,
                        "delta": completion["choices"][0]["message"],
                        "finish_reason": completion["choices"][0]["finish_reason"],
                    }
                ],
                "usage": completion["usage"],
            }
            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream_response(), media_type="text/event-stream")

    return app


def _openai_completion(result: dict[str, Any]) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "content": result.get("content"),
    }
    tool_calls = []
    for item in result.get("tool_calls") or ():
        arguments = item.get("arguments")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments or {}, ensure_ascii=False, separators=(",", ":"))
        tool_calls.append(
            {
                "id": item.get("id"),
                "type": "function",
                "function": {"name": item.get("name"), "arguments": arguments},
            }
        )
    if tool_calls:
        message["tool_calls"] = tool_calls
    usage = dict(result.get("usage") or {})
    return {
        "id": result["request_id"],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": result["model"],
        "provider_revision_id": result["provider_revision_id"],
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": result.get("finish_reason") or "stop",
            }
        ],
        "usage": {
            "prompt_tokens": int(usage.get("input_tokens") or 0),
            "completion_tokens": int(usage.get("output_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        },
    }


app = create_model_gateway_app()
