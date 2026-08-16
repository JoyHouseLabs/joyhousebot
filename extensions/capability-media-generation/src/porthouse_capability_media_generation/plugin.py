"""Provider-neutral image and video generation capabilities."""

from __future__ import annotations

from typing import Any

from porthouse.extension_sdk import (
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
    PluginManifest,
    PluginQuickstart,
)
from porthouse.extension_sdk.manifest import source_tree_digest

from .media_provider import credential
from .providers import (
    JimengAdapter,
    MediaGenerationHandler,
    MediaProviderRegistry,
    VolcengineArkAdapter,
)

PROVIDERS = ("volcengine_ark", "jimeng")
RATIOS = ("adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16")

COMMON_PROPERTIES: dict[str, Any] = {
    "prompt": {"type": "string", "minLength": 1, "maxLength": 8000},
    "provider": {"type": "string", "enum": list(PROVIDERS)},
    "model": {
        "type": "string",
        "minLength": 1,
        "description": "Provider model ID, endpoint ID, or Jimeng req_key.",
    },
    "seed": {"type": "integer", "minimum": -1},
}

CONFIGURATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "default_provider": {
            "type": "string",
            "title": "默认供应商",
            "description": "该能力未显式指定 provider 时使用的媒体供应商。",
            "enum": list(PROVIDERS),
            "default": "volcengine_ark",
        },
        "ark_image_model": {
            "type": "string",
            "title": "Seedream 模型或 Endpoint",
            "description": "火山方舟图片 Model ID 或已开通的推理 Endpoint ID。",
            "minLength": 1,
            "default": "doubao-seedream-4-0-250828",
        },
        "ark_video_model": {
            "type": "string",
            "title": "Seedance 模型或 Endpoint",
            "description": "火山方舟视频 Model ID 或已开通的推理 Endpoint ID。",
            "minLength": 1,
            "default": "doubao-seedance-1-0-pro-250528",
        },
        "jimeng_image_req_key": {
            "type": "string",
            "title": "即梦图片 req_key",
            "description": "即梦图片生成/编辑能力的 req_key。",
            "minLength": 1,
            "default": "t2i_v40_jimeng",
        },
        "jimeng_video_text_req_key": {
            "type": "string",
            "title": "即梦文生视频 req_key",
            "description": "即梦文生视频能力的 req_key。",
            "minLength": 1,
            "default": "jimeng_t2v_v30",
        },
        "jimeng_video_image_req_key": {
            "type": "string",
            "title": "即梦图生视频 req_key",
            "description": "即梦首帧图生视频能力的 req_key。",
            "minLength": 1,
            "default": "jimeng_i2v_first_v30",
        },
    },
}


def _image_schema(*, edit: bool) -> dict[str, Any]:
    properties = {
        **COMMON_PROPERTIES,
        "size": {
            "type": "string",
            "description": "Provider size such as 2K or 2048x2048.",
        },
        "count": {"type": "integer", "minimum": 1, "maximum": 15, "default": 1},
        "watermark": {"type": "boolean", "default": True},
    }
    required = ["prompt"]
    if edit:
        properties["image_urls"] = {
            "type": "array",
            "minItems": 1,
            "maxItems": 10,
            "items": {"type": "string", "format": "uri"},
        }
        required.append("image_urls")
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": properties,
    }


VIDEO_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["prompt"],
    "properties": {
        **COMMON_PROPERTIES,
        "image_urls": {
            "type": "array",
            "maxItems": 8,
            "items": {"type": "string", "format": "uri"},
            "description": "Optional first/reference frames for image-to-video.",
        },
        "ratio": {"type": "string", "enum": list(RATIOS)},
        "duration_seconds": {"type": "integer", "minimum": 4, "maximum": 15},
        "return_last_frame": {"type": "boolean", "default": False},
    },
}


def _definition(
    capability_id: str,
    version: str,
    *,
    name: str,
    description: str,
    input_schema: dict[str, Any],
    duration: int,
) -> CapabilityDefinition:
    return CapabilityDefinition(
        ref=CapabilityRef(capability_id, version, CapabilityKind.TOOL),
        name=name,
        description=description,
        input_schema=input_schema,
        output_schema={
            "type": "object",
            "properties": {
                "provider": {"type": "string"},
                "model": {"type": "string"},
                "task_id": {"type": "string"},
                "artifact_ids": {"type": "array", "items": {"type": "string"}},
            },
        },
        adapter="plugin",
        tags=("media", "generation", "cost-bearing"),
        execution_mode="external_operation",
        expected_duration_seconds=duration,
        timeout_seconds=90,
        idempotent=False,
        retryable=False,
        side_effect="external",
        invocation_concurrency="sequential",
        max_concurrent_invocations=1,
        permissions=("media.generate",),
        data_classification="confidential",
        cost_policy={"approval_required": True, "metered_external_service": True},
        configuration_schema=CONFIGURATION_SCHEMA,
    )


class MediaGenerationPlugin:
    plugin_id = "capability-media-generation"
    version = "1.0.0"

    def __init__(self, providers: MediaProviderRegistry | None = None) -> None:
        self.providers = providers or MediaProviderRegistry(
            (VolcengineArkAdapter(), JimengAdapter())
        )

    def manifest(self) -> PluginManifest:
        return PluginManifest(
            plugin_id=self.plugin_id,
            version=self.version,
            name="Media Generation",
            description=(
                "Provider-neutral text-to-image, image-to-image, and text/image-to-video "
                "capabilities with durable asynchronous reconciliation."
            ),
            distribution_name="porthouse-capability-media-generation",
            build_digest=source_tree_digest(__file__),
            runtime_contract_version=2,
            required_permissions=("media.generate",),
            dependencies=(
                {"id": "volcengine-ark-api-key", "kind": "credential", "required": False},
                {"id": "volcengine-access-key", "kind": "credential", "required": False},
                {"id": "volcengine-media-apis", "kind": "service", "required": True},
            ),
            quickstarts=(
                PluginQuickstart(
                    quickstart_id="generate-product-image",
                    title="生成产品视觉图",
                    description="使用已配置的图片模型生成一张可审阅的产品视觉图。",
                    prompt="为我的产品生成一张简洁、专业、适合官网首页的横版视觉图。",
                    capability_ids=("image.generate",),
                    expected_outcome="生成图片 Artifact，并保留模型与供应商证据。",
                ),
                PluginQuickstart(
                    quickstart_id="generate-short-video",
                    title="生成短视频",
                    description="使用 Seedance 或即梦生成一个短视频任务。",
                    prompt="生成一个 5 秒的 16:9 产品展示短视频，镜头缓慢推进。",
                    capability_ids=("video.generate",),
                    expected_outcome="异步视频任务完成后形成视频 Artifact。",
                ),
            ),
        )

    def register(self, registry: Any) -> None:
        definitions = (
            _definition(
                "image.generate",
                self.version,
                name="Generate image",
                description="Generate one or more images from a text prompt.",
                input_schema=_image_schema(edit=False),
                duration=30,
            ),
            _definition(
                "image.edit",
                self.version,
                name="Edit image",
                description="Generate edited images from text instructions and reference images.",
                input_schema=_image_schema(edit=True),
                duration=30,
            ),
            _definition(
                "video.generate",
                self.version,
                name="Generate video",
                description="Generate video from text and optional reference images.",
                input_schema=VIDEO_SCHEMA,
                duration=300,
            ),
        )
        for definition in definitions:
            registry.register_capability(
                definition,
                MediaGenerationHandler(definition.ref.capability_id, self.providers),
            )

    def health_checks(self) -> tuple[Any, ...]:
        ark_ready = bool(credential(("VOLCENGINE_ARK_API_KEY", "ARK_API_KEY")))
        jimeng_ready = bool(
            credential(("VOLC_ACCESSKEY", "VOLCENGINE_ACCESS_KEY_ID"))
            and credential(("VOLC_SECRETKEY", "VOLCENGINE_SECRET_ACCESS_KEY"))
        )
        return (
            {
                "name": "volcengine_ark_credentials",
                "status": "healthy" if ark_ready else "degraded",
                "summary": (
                    "Ark API credential is available to this Worker"
                    if ark_ready
                    else "Set VOLCENGINE_ARK_API_KEY or ARK_API_KEY on the Worker"
                ),
            },
            {
                "name": "jimeng_credentials",
                "status": "healthy" if jimeng_ready else "degraded",
                "summary": (
                    "Jimeng AK/SK credentials are available to this Worker"
                    if jimeng_ready
                    else "Set VOLC_ACCESSKEY and VOLC_SECRETKEY on the Worker"
                ),
            },
        )


def create_plugin() -> MediaGenerationPlugin:
    return MediaGenerationPlugin()


__all__ = ["MediaGenerationPlugin", "create_plugin"]
