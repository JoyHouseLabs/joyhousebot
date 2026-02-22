import pytest
from fastapi import HTTPException

from joyhousebot.api.http.chat_methods import build_chat_response


class _Agent:
    def __init__(self, *, fail: bool = False):
        self.fail = fail

    async def process_direct(self, **_kwargs):
        if self.fail:
            raise RuntimeError("boom")
        return "ok"


@pytest.mark.asyncio
async def test_build_chat_response_success():
    payload = await build_chat_response(
        agent=_Agent(),
        message="hello",
        session_id="s1",
        log_error=lambda _msg: None,
        error_detail=lambda e: str(e),
    )
    assert payload == {"ok": True, "response": "ok", "session_id": "s1"}


@pytest.mark.asyncio
async def test_build_chat_response_error():
    with pytest.raises(HTTPException) as exc:
        await build_chat_response(
            agent=_Agent(fail=True),
            message="hello",
            session_id="s1",
            log_error=lambda _msg: None,
            error_detail=lambda e: str(e),
        )
    assert exc.value.status_code == 500
    assert exc.value.detail == "boom"

