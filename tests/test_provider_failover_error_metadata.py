import pytest

import joyhousebot.providers.litellm_provider as lp
from joyhousebot.providers.litellm_provider import LiteLLMProvider, _build_error_meta


class _Err(Exception):
    def __init__(self, msg: str, status_code: int | None = None):
        super().__init__(msg)
        self.status_code = status_code


def test_build_error_meta_classifies_rate_limit_and_billing() -> None:
    meta = _build_error_meta(_Err("429 too many requests"))
    assert meta["error_kind"] == "rate_limit"
    assert meta["retryable"] is True
    assert meta["error_status"] == 429

    meta = _build_error_meta(_Err("insufficient credits"))
    assert meta["error_kind"] == "billing"
    assert meta["retryable"] is False


@pytest.mark.asyncio
async def test_chat_returns_structured_error_metadata(monkeypatch) -> None:
    async def _raise(**kwargs):
        raise _Err("401 unauthorized", status_code=401)

    monkeypatch.setattr(lp, "acompletion", _raise)
    provider = LiteLLMProvider(api_key="x", default_model="openai/gpt-4o-mini")
    resp = await provider.chat(messages=[{"role": "user", "content": "hi"}], model="openai/gpt-4o-mini")
    assert resp.finish_reason == "error"
    assert resp.error_kind == "auth"
    assert resp.error_status == 401
    assert resp.retryable is False
    assert resp.error_code

