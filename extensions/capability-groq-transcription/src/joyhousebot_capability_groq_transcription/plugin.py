"""Optional Groq audio transcription capability extension."""

import os
from pathlib import Path
from typing import Any

from loguru import logger

from joyhousebot.extension_sdk import (
    CapabilityContext,
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
    CapabilityResult,
    PluginManifest,
)
from joyhousebot.extension_sdk.manifest import source_tree_digest
from joyhousebot.extension_sdk.network import TrackedAsyncClient


class GroqTranscriptionProvider:
    """
    Voice transcription provider using Groq's Whisper API.

    Groq offers extremely fast transcription with a generous free tier.
    """

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("GROQ_API_KEY")
        self.api_url = "https://api.groq.com/openai/v1/audio/transcriptions"

    async def transcribe(self, file_path: str | Path, *, model: str = "whisper-large-v3") -> str:
        """
        Transcribe an audio file using Groq.

        Args:
            file_path: Path to the audio file.

        Returns:
            Transcribed text.
        """
        if not self.api_key:
            logger.warning("Groq API key not configured for transcription")
            return ""

        path = Path(file_path)
        if not path.exists():
            logger.error(f"Audio file not found: {file_path}")
            return ""

        try:
            async with TrackedAsyncClient() as client:
                with open(path, "rb") as f:
                    files = {
                        "file": (path.name, f),
                        "model": (None, model),
                    }
                    headers = {
                        "Authorization": f"Bearer {self.api_key}",
                    }

                    response = await client.post(
                        self.api_url, headers=headers, files=files, timeout=60.0
                    )

                    response.raise_for_status()
                    data = response.json()
                    return data.get("text", "")

        except Exception as e:
            logger.error(f"Groq transcription error: {e}")
            return ""


class GroqTranscriptionHandler:
    async def execute(
        self, context: CapabilityContext, input: dict[str, Any]
    ) -> CapabilityResult:
        file_path = str(input.get("file_path") or "").strip()
        if not file_path:
            return CapabilityResult(
                success=False,
                error={"code": "FILE_PATH_REQUIRED", "message": "file_path is required"},
            )
        api_key = (os.environ.get("GROQ_API_KEY") or "").strip()
        if not api_key:
            return CapabilityResult(
                success=False,
                error={
                    "code": "CREDENTIAL_NOT_CONFIGURED",
                    "message": "GROQ_API_KEY is not configured",
                },
            )
        model = str(input.get("model") or "whisper-large-v3").strip()
        text = await GroqTranscriptionProvider(api_key=api_key).transcribe(
            file_path, model=model
        )
        if not text:
            return CapabilityResult(
                success=False,
                error={"code": "TRANSCRIPTION_FAILED", "message": "audio transcription failed"},
            )
        return CapabilityResult(
            success=True,
            output={"text": text, "model": model},
            usage={"provider": "groq", "model": model},
        )


class GroqTranscriptionPlugin:
    plugin_id = "capability-groq-transcription"
    version = "1.0.0"

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            plugin_id=self.plugin_id,
            version=self.version,
            name="Groq Audio Transcription",
            description="Explicitly authorized Groq Whisper audio transcription capability.",
            distribution_name="joyhousebot-capability-groq-transcription",
            build_digest=source_tree_digest(__file__),
            required_permissions=("media.transcribe", "filesystem.read"),
            dependencies=(
                {"id": "groq-api", "kind": "service", "required": True},
                {"id": "groq-api-key", "kind": "credential", "required": True},
            ),
        )

    def register(self, registry: Any) -> None:
        registry.register_capability(
            CapabilityDefinition(
                ref=CapabilityRef(
                    capability_id="media.transcribe.groq",
                    version=self.version,
                    kind=CapabilityKind.TOOL,
                ),
                name="Groq audio transcription",
                description="Transcribe an authorized local audio artifact with Groq Whisper.",
                input_schema={
                    "type": "object",
                    "required": ["file_path"],
                    "properties": {
                        "file_path": {"type": "string"},
                        "model": {"type": "string", "default": "whisper-large-v3"},
                    },
                },
                output_schema={
                    "type": "object",
                    "required": ["text", "model"],
                    "properties": {
                        "text": {"type": "string"},
                        "model": {"type": "string"},
                    },
                },
                adapter="plugin",
                tags=("media", "transcription", "groq"),
                expected_duration_seconds=30,
                timeout_seconds=90,
                idempotent=True,
                retryable=True,
                side_effect="read",
                permissions=("media.transcribe", "filesystem.read"),
                data_classification="confidential",
            ),
            GroqTranscriptionHandler(),
        )

    def health_checks(self) -> tuple[Any, ...]:
        return ()


def create_plugin() -> GroqTranscriptionPlugin:
    return GroqTranscriptionPlugin()
