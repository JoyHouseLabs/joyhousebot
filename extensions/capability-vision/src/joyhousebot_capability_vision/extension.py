"""Fail-closed OCR and visual understanding for a Run-bound image asset."""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

import httpx

from joyhousebot.extension_sdk import (
    CapabilityContext,
    CapabilityDefinition,
    CapabilityExtensionManifest,
    CapabilityKind,
    CapabilityRef,
    CapabilityResult,
)
from joyhousebot.extension_sdk.manifest import source_tree_digest
from joyhousebot.extension_sdk.network import (
    SsrfProtectedTransport,
    TrackedAsyncClient,
    sanitize_error_message,
    validate_url,
)

_DEFAULT_URL = "https://api.openai.com/v1/chat/completions"
_DEFAULT_MODEL = "gpt-4.1-mini"
_MAX_ASSET_BYTES = 10 * 1024 * 1024
_MAX_OBSERVATIONS = 50
_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")

CONFIGURATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "api_url": {
            "type": "string",
            "format": "uri",
            "default": _DEFAULT_URL,
            "description": "OpenAI-compatible chat-completions endpoint; never supplied by Agent input.",
        },
        "model": {"type": "string", "minLength": 1, "maxLength": 256, "default": _DEFAULT_MODEL},
        "api_key_env": {
            "type": "string",
            "pattern": "^[A-Z_][A-Z0-9_]{0,127}$",
            "default": "OPENAI_API_KEY",
        },
        "max_asset_bytes": {"type": "integer", "minimum": 1024, "maximum": _MAX_ASSET_BYTES, "default": 5242880},
    },
}

INPUT_SCHEMA = {
    "type": "object",
    "required": ["asset_id", "task"],
    "properties": {
        "asset_id": {"type": "string", "minLength": 1, "maxLength": 256},
        "task": {"type": "string", "enum": ["ocr", "understand"]},
        "instruction": {"type": "string", "maxLength": 4000},
    },
    "additionalProperties": False,
}


def _failure(code: str, message: str, *, retryable: bool = False) -> CapabilityResult:
    return CapabilityResult(
        success=False,
        error={"code": code, "message": message, "retryable": retryable},
    )


def _configuration(context: CapabilityContext) -> dict[str, Any]:
    value = dict(context.metadata.get("capability_configuration") or {})
    api_url = str(value.get("api_url") or _DEFAULT_URL).strip()
    valid, message = validate_url(api_url)
    if not valid:
        raise ValueError(f"invalid vision api_url: {message}")
    if not api_url.startswith("https://"):
        raise ValueError("vision api_url must use HTTPS")
    api_key_env = str(value.get("api_key_env") or "OPENAI_API_KEY").strip()
    if not _ENV_NAME.fullmatch(api_key_env):
        raise ValueError("vision api_key_env is invalid")
    return {
        "api_url": api_url,
        "model": str(value.get("model") or _DEFAULT_MODEL).strip(),
        "api_key_env": api_key_env,
        "max_asset_bytes": min(
            _MAX_ASSET_BYTES,
            max(1024, int(value.get("max_asset_bytes") or 5242880)),
        ),
    }


class VisionProvider:
    """Minimal OpenAI-compatible transport; provider responses stay bounded."""

    async def analyze(
        self,
        *,
        api_url: str,
        api_key: str,
        model: str,
        media_type: str,
        body: bytes,
        task: str,
        instruction: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        encoded = base64.b64encode(body).decode("ascii")
        user_instruction = (
            "Return JSON only with an observations array. Each item must have kind, value, "
            "confidence (0 through 1), and optional page/region evidence. Do not invent facts. "
            + ("Extract readable text faithfully." if task == "ocr" else "Describe relevant visual evidence.")
        )
        if instruction:
            user_instruction += " Additional bounded instruction: " + instruction
        payload = {
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": "You produce bounded evidence JSON, never hidden reasoning."},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_instruction},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{media_type};base64,{encoded}"},
                        },
                    ],
                },
            ],
        }
        try:
            async with TrackedAsyncClient(
                transport=SsrfProtectedTransport(), follow_redirects=False, timeout=60.0
            ) as client:
                response = await client.post(
                    api_url,
                    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                    json=payload,
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise RuntimeError("VISION_TIMEOUT") from exc
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(f"VISION_HTTP_{exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise RuntimeError("VISION_CONNECTION_FAILED") from exc
        body = dict(response.json() or {})
        choices = list(body.get("choices") or [])
        content = str(dict(choices[0].get("message") or {}).get("content") or "") if choices else ""
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("vision provider returned non-JSON content") from exc
        return dict(parsed or {}), dict(body.get("usage") or {})


def _observations(raw: dict[str, Any], *, asset_id: str) -> list[dict[str, Any]]:
    value: list[dict[str, Any]] = []
    for item in list(raw.get("observations") or [])[:_MAX_OBSERVATIONS]:
        observation = dict(item or {})
        text = str(observation.get("value") or "").strip()[:4000]
        if not text:
            continue
        try:
            confidence = float(observation.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        evidence = dict(observation.get("evidence") or {})
        region = evidence.get("region")
        if not (isinstance(region, list) and len(region) == 4):
            region = None
        value.append(
            {
                "kind": str(observation.get("kind") or "scene")[:64],
                "value": text,
                "confidence": max(0.0, min(1.0, confidence)),
                "evidence": {
                    "asset_id": asset_id,
                    "page": max(1, int(evidence.get("page") or 1)),
                    "region": region,
                },
            }
        )
    return value


class VisionHandler:
    def __init__(self, provider: VisionProvider | None = None) -> None:
        self.provider = provider or VisionProvider()

    async def execute(self, context: CapabilityContext, input: dict[str, Any]) -> CapabilityResult:
        if context.services is None:
            return _failure("CONTEXT_REQUIRED", "Runtime context service is unavailable")
        asset_id = str(input.get("asset_id") or "").strip()
        task = str(input.get("task") or "").strip()
        if not asset_id or task not in {"ocr", "understand"}:
            return _failure("INVALID_PARAMETERS", "asset_id and task (ocr or understand) are required")
        try:
            settings = _configuration(context)
        except ValueError as exc:
            return _failure("INVALID_CONFIGURATION", str(exc))
        api_key = str(os.environ.get(settings["api_key_env"]) or "").strip()
        if not api_key:
            return _failure("CREDENTIAL_NOT_CONFIGURED", f"{settings['api_key_env']} is not configured")
        try:
            asset = await context.services.context.read_input_asset(
                context, asset_id=asset_id, max_bytes=settings["max_asset_bytes"]
            )
        except PermissionError as exc:
            return _failure("INPUT_ASSET_ACCESS_DENIED", str(exc))
        except Exception as exc:
            return _failure("INPUT_ASSET_UNAVAILABLE", sanitize_error_message(str(exc)))
        media_type = str(asset.get("media_type") or "").lower()
        if not media_type.startswith("image/"):
            return _failure("UNSUPPORTED_MEDIA_TYPE", "vision requires a frozen image Input Asset")
        try:
            response, usage = await self.provider.analyze(
                api_url=settings["api_url"],
                api_key=api_key,
                model=settings["model"],
                media_type=media_type,
                body=bytes(asset["body"]),
                task=task,
                instruction=str(input.get("instruction") or "").strip()[:4000],
            )
        except RuntimeError as exc:
            code = str(exc)
            return _failure(code, "vision provider request failed", retryable=code in {"VISION_TIMEOUT", "VISION_CONNECTION_FAILED"})
        except ValueError as exc:
            return _failure("VISION_RESPONSE_INVALID", str(exc))
        except Exception as exc:
            return _failure("VISION_FAILED", sanitize_error_message(str(exc)), retryable=True)
        return CapabilityResult(
            success=True,
            output={
                "asset_id": asset_id,
                "task": task,
                "observations": _observations(response, asset_id=asset_id),
                "model": {"provider": "openai-compatible", "model": settings["model"], "version": "0.1.0"},
            },
            usage={"provider": "openai-compatible", "model": settings["model"], **usage},
        )


class VisionCapabilityExtension:
    extension_id = "capability-vision"
    version = "0.1.0"

    def manifest(self) -> CapabilityExtensionManifest:
        return CapabilityExtensionManifest(
            extension_id=self.extension_id,
            version=self.version,
            name="Frozen Asset Vision",
            description="OCR and visual understanding over one Runtime-bound image asset.",
            distribution_name="joyhousebot-capability-vision",
            build_digest=source_tree_digest(__file__),
            runtime_contract_version=2,
            required_permissions=("context.read", "vision.read"),
            dependencies=(
                {"id": "runtime-context-services", "kind": "service", "required": True},
                {"id": "openai-compatible-vision", "kind": "http", "required": True},
                {"id": "vision-api-key", "kind": "credential", "required": True},
            ),
        )

    def register(self, registry: Any) -> None:
        registry.register_capability(
            CapabilityDefinition(
                ref=CapabilityRef("vision.understand", self.version, CapabilityKind.CAPABILITY),
                name="Understand a frozen image",
                description="OCR or understand one authorized Runtime image asset with evidence.",
                input_schema=INPUT_SCHEMA,
                output_schema={"type": "object"},
                adapter="extension",
                tags=("vision", "ocr", "input-assets"),
                expected_duration_seconds=15,
                timeout_seconds=90,
                idempotent=True,
                retryable=True,
                side_effect="read",
                permissions=("context.read", "vision.read"),
                data_classification="confidential",
                configuration_schema=CONFIGURATION_SCHEMA,
            ),
            VisionHandler(),
        )

    def health_checks(self) -> tuple[Any, ...]:
        return ()


def create_extension() -> VisionCapabilityExtension:
    return VisionCapabilityExtension()
