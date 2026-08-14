from joyhousebot_capability_rerank import RerankCapabilityPlugin

from joyhousebot.capabilities import CapabilityPluginRegistry
from joyhousebot.contracts import CapabilityContext


def test_rerank_extension_is_a_scoped_read_capability() -> None:
    registry = CapabilityPluginRegistry()
    registry.register_plugin(RerankCapabilityPlugin())
    definition, _ = registry.get("retrieval.rerank", "0.1.0")
    assert definition.permissions == ("context.read",)
    assert definition.side_effect == "read"
    assert definition.ref.plugin_id == "capability-rerank"


async def test_rerank_returns_only_ranked_candidate_ids() -> None:
    registry = CapabilityPluginRegistry()
    registry.register_plugin(RerankCapabilityPlugin())
    result = await registry.invoke(
        "retrieval.rerank",
        {
            "query": "AI 产品增长",
            "candidates": [
                {"candidate_id": "unrelated", "text": "今天天气很好"},
                {"candidate_id": "relevant", "text": "AI 产品的增长策略与用户研究"},
            ],
        },
        context=CapabilityContext(
            "user", "session", "run", metadata={"permissions": ["context.read"]}
        ),
    )
    assert result.success is True
    assert result.output["ranked"][0]["candidate_id"] == "relevant"
    assert "text" not in result.output["ranked"][0]
