
import httpx
import pytest
from porthouse_capability_groq_transcription import (
    GroqTranscriptionPlugin,
    GroqTranscriptionProvider,
)

from porthouse.capabilities import CapabilityPluginRegistry
from porthouse.contracts import CapabilityContext


def test_groq_transcription_is_versioned_capability_plugin() -> None:
    registry = CapabilityPluginRegistry()
    registry.register_plugin(GroqTranscriptionPlugin())
    definition, _ = registry.get("media.transcribe.groq", "1.0.0")
    assert definition.permissions == ("media.transcribe", "filesystem.read")
    assert definition.side_effect == "read"
    assert definition.ref.plugin_id == "capability-groq-transcription"


@pytest.mark.asyncio
async def test_groq_transcription_requires_explicit_permissions(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"audio")
    registry = CapabilityPluginRegistry()
    registry.register_plugin(GroqTranscriptionPlugin())
    result = await registry.invoke(
        "media.transcribe.groq",
        {"file_path": str(audio)},
        context=CapabilityContext("user", "session", "run", metadata={"permissions": []}),
    )
    assert result.success is False
    assert result.error["code"] == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_groq_provider_uses_configured_model(monkeypatch, tmp_path) -> None:
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"audio")

    async def respond(request: httpx.Request) -> httpx.Response:
        assert b"whisper-large-v3-turbo" in request.content
        return httpx.Response(200, json={"text": "hello"})

    class Client:
        async def __aenter__(self):
            self.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            return self.client

        async def __aexit__(self, *args):
            await self.client.aclose()

    monkeypatch.setattr(
        "porthouse_capability_groq_transcription.plugin.TrackedAsyncClient", Client
    )
    provider = GroqTranscriptionProvider(api_key="test-key")
    assert (
        await provider.transcribe(audio, model="whisper-large-v3-turbo") == "hello"
    )
