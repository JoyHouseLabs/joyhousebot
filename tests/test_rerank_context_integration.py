from porthouse.capabilities.services.context import ContextPort
from porthouse.contracts import CapabilityContext
from porthouse.domain.capabilities import CapabilityResult, InvocationStatus


async def test_context_port_applies_only_frozen_rerank_policy() -> None:
    calls = []

    async def execute(context, **kwargs):  # noqa: ANN001
        calls.append(kwargs)
        return CapabilityResult(
            invocation_id="inv-rerank",
            status=InvocationStatus.SUCCEEDED,
            summary="reranked",
            data={
                "output": {
                    "ranked": [
                        {"candidate_id": "doc-b:rev:0", "score": 0.9, "rank": 1},
                        {"candidate_id": "doc-a:rev:0", "score": 0.2, "rank": 2},
                    ]
                }
            },
        )

    port = ContextPort(None)
    port.set_rerank_executor(execute)
    context = CapabilityContext(
        "user",
        "session",
        "run",
        memory_policy={
            "retrieval": {
                "rerank": {
                    "enabled": True,
                    "capability_id": "retrieval.rerank",
                    "version": "0.1.0",
                    "candidate_limit": 20,
                    "failure_mode": "fallback",
                }
            }
        },
    )
    hits = [
        {"doc_id": "doc-a", "revision_id": "rev", "chunk_index": 0, "content": "first"},
        {"doc_id": "doc-b", "revision_id": "rev", "chunk_index": 0, "content": "second"},
    ]
    ranked = await port._apply_rerank(context, query="question", hits=hits, scope="knowledge")

    assert [item["doc_id"] for item in ranked] == ["doc-b", "doc-a"]
    assert calls[0]["capability_id"] == "retrieval.rerank"
    assert context.metadata["retrieval_rerank"]["applied"] is True


async def test_context_port_keeps_original_order_when_fallback_is_allowed() -> None:
    async def unavailable(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("extension unavailable")

    port = ContextPort(None)
    port.set_rerank_executor(unavailable)
    context = CapabilityContext(
        "user",
        "session",
        "run",
        memory_policy={"retrieval": {"rerank": {"enabled": True, "version": "0.1.0"}}},
    )
    hits = [{"doc_id": "doc-a", "revision_id": "rev", "chunk_index": 0, "content": "first"}]
    ranked = await port._apply_rerank(context, query="question", hits=hits, scope="knowledge")

    assert ranked[0]["rerank_fallback"] == "RuntimeError"
    assert context.metadata["retrieval_rerank"]["fallback"] is True


async def test_context_port_fails_closed_when_policy_requires_rerank() -> None:
    port = ContextPort(None)
    context = CapabilityContext(
        "user",
        "session",
        "run",
        memory_policy={
            "retrieval": {
                "rerank": {
                    "enabled": True,
                    "version": "0.1.0",
                    "failure_mode": "fail_closed",
                }
            }
        },
    )
    hits = [{"doc_id": "doc-a", "revision_id": "rev", "chunk_index": 0, "content": "first"}]

    import pytest

    with pytest.raises(RuntimeError, match="rerank_executor_unavailable"):
        await port._apply_rerank(context, query="question", hits=hits, scope="knowledge")
