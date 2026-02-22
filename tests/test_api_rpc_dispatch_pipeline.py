import pytest

from joyhousebot.api.rpc.dispatch_pipeline import run_handler_pipeline


@pytest.mark.asyncio
async def test_run_handler_pipeline_returns_first_non_none():
    calls = []

    async def _h1():
        calls.append("h1")
        return None

    def _h2():
        calls.append("h2")
        return (True, {"ok": True}, None)

    async def _h3():
        calls.append("h3")
        return (True, {"later": True}, None)

    result = await run_handler_pipeline((_h1, _h2, _h3))
    assert result == (True, {"ok": True}, None)
    assert calls == ["h1", "h2"]


@pytest.mark.asyncio
async def test_run_handler_pipeline_returns_none_when_no_match():
    async def _none1():
        return None

    def _none2():
        return None

    result = await run_handler_pipeline((_none1, _none2))
    assert result is None

