"""Volcengine Ark adapter for Seedream and Seedance."""

from __future__ import annotations

import os
from typing import Any

from porthouse.extension_sdk import (
    CapabilityContext,
    CapabilityResult,
    OperationReconciliationResult,
)
from porthouse.extension_sdk.network import TrackedAsyncClient, sanitize_error_message

from .media_provider import (
    MediaProviderError,
    credential,
    failure,
    media_artifacts,
    require_identity,
    response_error,
    submission_unknown,
    write_receipt,
)


class VolcengineArkAdapter:
    provider_id = "volcengine_ark"

    @staticmethod
    def _api_key() -> str:
        return credential(("VOLCENGINE_ARK_API_KEY", "ARK_API_KEY"))

    @staticmethod
    def _base_url() -> str:
        return str(
            os.environ.get("VOLCENGINE_ARK_API_BASE")
            or "https://ark.cn-beijing.volces.com/api/v3"
        ).rstrip("/")

    async def execute(
        self,
        kind: str,
        context: CapabilityContext,
        input: dict[str, Any],
        settings: dict[str, Any],
    ) -> CapabilityResult:
        try:
            require_identity(context)
            if not self._api_key():
                raise MediaProviderError(
                    "CREDENTIAL_NOT_CONFIGURED",
                    "VOLCENGINE_ARK_API_KEY or ARK_API_KEY is not configured",
                )
            if kind in {"image.generate", "image.edit"}:
                return await self._image(kind, context, input, settings)
            if kind == "video.generate":
                return await self._video(context, input, settings)
            raise MediaProviderError("UNSUPPORTED_MEDIA_CAPABILITY", kind)
        except MediaProviderError as exc:
            if exc.outcome_unknown:
                return submission_unknown(
                    context=context,
                    provider=self.provider_id,
                    media_kind="video" if kind == "video.generate" else "image",
                    model=self._model(kind, input, settings),
                    message=str(exc),
                )
            return failure(
                MediaProviderError(exc.code, str(exc), retryable=False)
            )
        except Exception as exc:
            return submission_unknown(
                context=context,
                provider=self.provider_id,
                media_kind="video" if kind == "video.generate" else "image",
                model=self._model(kind, input, settings),
                message=sanitize_error_message(str(exc)),
            )

    @staticmethod
    def _model(kind: str, input: dict[str, Any], settings: dict[str, Any]) -> str:
        if kind == "video.generate":
            return str(
                input.get("model")
                or settings.get("ark_video_model")
                or "doubao-seedance-1-0-pro-250528"
            )
        return str(
            input.get("model")
            or settings.get("ark_image_model")
            or "doubao-seedream-4-0-250828"
        )

    async def _image(
        self,
        kind: str,
        context: CapabilityContext,
        input: dict[str, Any],
        settings: dict[str, Any],
    ) -> CapabilityResult:
        model = self._model(kind, input, settings)
        payload: dict[str, Any] = {
            "model": model,
            "prompt": str(input["prompt"]),
            "size": str(input.get("size") or "2K"),
            "response_format": "url",
            "stream": False,
            "watermark": bool(input.get("watermark", True)),
        }
        image_urls = [str(value) for value in input.get("image_urls") or ()]
        if kind == "image.edit":
            payload["image"] = image_urls[0] if len(image_urls) == 1 else image_urls
        count = int(input.get("count") or 1)
        if count > 1:
            payload["sequential_image_generation"] = "auto"
            payload["sequential_image_generation_options"] = {"max_images": count}
        if input.get("seed") is not None:
            payload["seed"] = int(input["seed"])
        response = await self._request(
            "POST", "/images/generations", json_body=payload, context=context
        )
        if error := response_error(response, "volcengine_ark"):
            raise error
        value = response.json()
        urls = [
            str(item.get("url"))
            for item in value.get("data") or ()
            if isinstance(item, dict) and item.get("url")
        ]
        if not urls:
            raise MediaProviderError(
                "MEDIA_RESULT_MISSING", "Volcengine Ark returned no image URL"
            )
        operation_id = str(
            response.headers.get("x-request-id") or value.get("id") or context.action_id
        )
        artifacts = media_artifacts(
            action_id=str(context.action_id),
            media_kind="image",
            urls=urls,
            provider=self.provider_id,
            model=model,
            operation_id=operation_id,
            source_expires_seconds=86_400,
        )
        return CapabilityResult(
            success=True,
            output={
                "provider": self.provider_id,
                "model": model,
                "count": len(artifacts),
                "artifact_ids": [item.artifact_id for item in artifacts],
            },
            artifacts=artifacts,
            usage={
                "provider": self.provider_id,
                "model": model,
                **dict(value.get("usage") or {}),
            },
            write_receipt=write_receipt(context, operation_id),
        )

    async def _video(
        self,
        context: CapabilityContext,
        input: dict[str, Any],
        settings: dict[str, Any],
    ) -> CapabilityResult:
        model = self._model("video.generate", input, settings)
        prompt = str(input["prompt"])
        if input.get("ratio"):
            prompt += f" --ratio {input['ratio']}"
        if input.get("duration_seconds"):
            prompt += f" --dur {int(input['duration_seconds'])}"
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        content.extend(
            {"type": "image_url", "image_url": {"url": str(url)}}
            for url in input.get("image_urls") or ()
        )
        payload: dict[str, Any] = {
            "model": model,
            "content": content,
            "return_last_frame": bool(input.get("return_last_frame", False)),
        }
        response = await self._request(
            "POST", "/contents/generations/tasks", json_body=payload, context=context
        )
        if error := response_error(response, "volcengine_ark"):
            raise error
        value = response.json()
        task_id = str(value.get("id") or "").strip()
        if not task_id:
            raise MediaProviderError(
                "MEDIA_TASK_ID_MISSING",
                "Volcengine Ark returned no video task ID",
                outcome_unknown=True,
            )
        operation = {
            "provider": self.provider_id,
            "provider_operation_id": task_id,
            "media_kind": "video",
            "model": model,
            "status": "queued",
        }
        return CapabilityResult(
            success=True,
            output={"provider": self.provider_id, "model": model, "task_id": task_id},
            status="accepted",
            operation=operation,
            write_receipt=write_receipt(context, task_id),
        )

    async def reconcile(
        self, context: CapabilityContext, operation: dict[str, Any]
    ) -> OperationReconciliationResult:
        if operation.get("status") == "submission_unknown":
            return OperationReconciliationResult(
                status="unknown",
                summary=(
                    "Seedance/Seedream submission outcome is unknown; manual provider "
                    "reconciliation is required"
                ),
                operation=operation,
            )
        task_id = str(operation.get("provider_operation_id") or "")
        try:
            if not self._api_key():
                raise MediaProviderError(
                    "CREDENTIAL_NOT_CONFIGURED",
                    "Volcengine Ark API key is not configured",
                    retryable=True,
                )
            response = await self._request(
                "GET", f"/contents/generations/tasks/{task_id}", context=context
            )
            if error := response_error(response, "volcengine_ark"):
                raise error
            value = response.json()
            status = str(value.get("status") or "").lower()
            current = {**operation, "status": status}
            if status in {"queued", "running"}:
                return OperationReconciliationResult(
                    status="pending",
                    summary=f"Seedance task is {status}",
                    operation=current,
                    retry_after_seconds=10,
                )
            if status == "cancelled":
                return OperationReconciliationResult(
                    status="failed",
                    summary="Seedance task was cancelled",
                    error={"code": "MEDIA_TASK_CANCELLED", "message": "video task cancelled"},
                    operation=current,
                )
            if status == "failed":
                provider_error = value.get("error") or {}
                return OperationReconciliationResult(
                    status="failed",
                    summary=str(provider_error.get("message") or "Seedance task failed"),
                    error={
                        "code": str(provider_error.get("code") or "MEDIA_TASK_FAILED"),
                        "message": str(provider_error.get("message") or "video task failed"),
                    },
                    operation=current,
                )
            if status != "succeeded":
                return OperationReconciliationResult(
                    status="unknown",
                    summary=f"unknown Seedance task status: {status or 'missing'}",
                    operation=current,
                )
            video_url = str((value.get("content") or {}).get("video_url") or "")
            if not video_url:
                return OperationReconciliationResult(
                    status="failed",
                    summary="Seedance task has no video URL",
                    error={"code": "MEDIA_RESULT_MISSING", "message": "video URL is missing"},
                    operation=current,
                )
            artifacts = media_artifacts(
                action_id=str(operation.get("action_id") or context.action_id or task_id),
                media_kind="video",
                urls=[video_url],
                provider=self.provider_id,
                model=str(operation.get("model") or value.get("model") or ""),
                operation_id=task_id,
                source_expires_seconds=86_400,
            )
            return OperationReconciliationResult(
                status="succeeded",
                summary="Seedance video generation completed",
                output={
                    "provider": self.provider_id,
                    "task_id": task_id,
                    "artifact_ids": [item.artifact_id for item in artifacts],
                    "usage": dict(value.get("usage") or {}),
                },
                artifacts=artifacts,
                operation=current,
            )
        except MediaProviderError as exc:
            return OperationReconciliationResult(
                status="pending" if exc.retryable else "failed",
                summary=sanitize_error_message(str(exc)),
                error=(
                    None
                    if exc.retryable
                    else {"code": exc.code, "message": sanitize_error_message(str(exc))}
                ),
                operation=operation,
                retry_after_seconds=30 if exc.retryable else None,
            )
        except Exception as exc:
            return OperationReconciliationResult(
                status="pending",
                summary=sanitize_error_message(str(exc)),
                operation=operation,
                retry_after_seconds=30,
            )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        context: CapabilityContext,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
            "Idempotency-Key": str(context.idempotency_key or ""),
        }
        async with TrackedAsyncClient() as client:
            return await client.request(
                method,
                f"{self._base_url()}{path}",
                headers=headers,
                json=json_body,
                timeout=60.0,
            )


__all__ = ["VolcengineArkAdapter"]
