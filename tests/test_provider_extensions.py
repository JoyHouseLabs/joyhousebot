from joyhousebot_provider_anthropic import (
    ANTHROPIC_PROVIDER_EXTENSION,
    AnthropicProvider,
)
from joyhousebot_provider_openai_compatible import (
    OPENAI_COMPATIBLE_PROVIDER_EXTENSION,
    OpenAICompatibleProvider,
)

from joyhousebot.config.schema import Config, ExtensionsConfig, ProviderConfig
from joyhousebot.providers.factory import create_model_provider
from joyhousebot.providers.registry import ModelProviderRegistry


def test_anthropic_extension_declares_endpoint_and_manifest() -> None:
    extension = ANTHROPIC_PROVIDER_EXTENSION
    assert extension.manifest.extension_id == "provider-anthropic"
    assert extension.manifest.extension_types == ("model_provider",)
    assert [item.name for item in extension.providers] == ["anthropic"]
    assert extension.providers[0].env_key == "ANTHROPIC_API_KEY"


def test_provider_registry_discovers_anthropic_entry_point() -> None:
    registry = ModelProviderRegistry(enabled=["provider-anthropic"])
    assert registry.ensure_provider("anthropic") is ANTHROPIC_PROVIDER_EXTENSION.providers[0]
    assert registry.source_for("anthropic") == "entry-point:provider-anthropic"


def test_factory_builds_anthropic_through_extension_registry() -> None:
    config = Config(extensions=ExtensionsConfig(enabled=["provider-anthropic"]))
    config.providers.settings["anthropic"] = ProviderConfig(api_key="test-key")
    provider = create_model_provider(config=config, model="anthropic/claude-test")
    try:
        assert isinstance(provider, AnthropicProvider)
    finally:
        import asyncio

        asyncio.run(provider.close())


def test_openai_compatible_extension_owns_endpoint_catalog() -> None:
    extension = OPENAI_COMPATIBLE_PROVIDER_EXTENSION
    assert extension.manifest.extension_id == "provider-openai-compatible"
    assert {item.name for item in extension.providers} >= {
        "openai",
        "openrouter",
        "deepseek",
        "vllm",
    }
    openrouter = next(item for item in extension.providers if item.name == "openrouter")
    assert openrouter.is_gateway is True
    assert openrouter.default_api_base == "https://openrouter.ai/api/v1"


def test_provider_registry_discovers_openai_compatible_entry_point() -> None:
    registry = ModelProviderRegistry(enabled=["provider-openai-compatible"])
    assert registry.ensure_provider("deepseek") is not None
    assert registry.source_for("deepseek") == "entry-point:provider-openai-compatible"


def test_factory_builds_openai_compatible_through_extension_registry() -> None:
    config = Config(
        extensions=ExtensionsConfig(enabled=["provider-openai-compatible"])
    )
    config.providers.settings["openrouter"] = ProviderConfig(api_key="test-key")
    config.providers.default_provider = "openrouter"
    provider = create_model_provider(config=config, model="openrouter/openai/gpt-test")
    try:
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.provider_name == "openrouter"
    finally:
        import asyncio

        asyncio.run(provider.close())


def test_factory_routes_credential_free_local_provider_without_default_alias() -> None:
    config = Config(
        extensions=ExtensionsConfig(enabled=["provider-openai-compatible"])
    )
    config.providers.settings["vllm"] = ProviderConfig(
        api_base="http://127.0.0.1:11434/v1"
    )

    provider = create_model_provider(config=config, model="vllm/qwen3:1.7b")
    try:
        assert isinstance(provider, OpenAICompatibleProvider)
        assert provider.provider_name == "vllm"
        assert provider.api_key == ""
    finally:
        import asyncio

        asyncio.run(provider.close())
