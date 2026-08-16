"""Volcengine Visual OpenAPI adapter for Jimeng media models."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
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
from .signing import sign_openapi_request


@dataclass(slots=True)
class JimengCredentials:
    access_key: str
    secret_key: str
    session_token: str = ""


class JimengAdapter:
    provider_id = "jimeng"
    _submit_action = "CVSync2AsyncSubmitTask"
    _result_action = "CVSync2AsyncGetResult"
    _version = "2022-08-31"

    @staticmethod
    def _credentials() -> JimengCredentials:
        return JimengCredentials(
            access_key=credential(("VOLC_ACCESSKEY", "VOLCENGINE_ACCESS_KEY_ID")),
            secret_key=credential(("VOLC_SECRETKEY", "VOLCENGINE_SECRET_ACCESS_KEY")),
            session_token=credential(("VOLC_SESSION_TOKEN", "VOLCENGINE_SESSION_TOKEN")),
        )

    @staticmethod
    def _base_url() -> str:
        return str(
            os.environ.get("VOLCENGINE_JIMENG_API_BASE")
            or "https://visual.volcengineapi.com"
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
            credentials = self._credentials()
            if not credentials.access_key or not credentials.secret_key:
                raise MediaProviderError(
                    "CREDENTIAL_NOT_CONFIGURED",
                    "VOLC_ACCESSKEY and VOLC_SECRETKEY are not configured",
                )
            req_key, body, media_kind = self._request_body(kind, input, settings)
            value = await self._call(
                self._submit_action,
                body,
                credentials,
                idempotency_key=str(context.idempotency_key),
            )
            task_id = str((value.get("data") or {}).get("task_id") or "").strip()
            if not task_id:
                raise MediaProviderError(
                    "MEDIA_TASK_ID_MISSING",
                    "Jimeng returned no media task ID",
                    outcome_unknown=True,
                )
            operation = {
                "provider": self.provider_id,
                "provider_operation_id": task_id,
                "media_kind": media_kind,
                "model": req_key,
                "req_key": req_key,
                "status": "in_queue",
                "watermark": bool(input.get("watermark", True)),
            }
            return CapabilityResult(
                success=True,
                output={"provider": self.provider_id, "model": req_key, "task_id": task_id},
                status="accepted",
                operation=operation,
                write_receipt=write_receipt(context, task_id),
            )
        except MediaProviderError as exc:
            if exc.outcome_unknown:
                req_key, _body, media_kind = self._request_body(kind, input, settings)
                return submission_unknown(
                    context=context,
                    provider=self.provider_id,
                    media_kind=media_kind,
                    model=req_key,
                    message=str(exc),
                )
            return failure(
                MediaProviderError(exc.code, str(exc), retryable=False)
            )
        except Exception as exc:
            req_key, _body, media_kind = self._request_body(kind, input, settings)
            return submission_unknown(
                context=context,
                provider=self.provider_id,
                media_kind=media_kind,
                model=req_key,
                message=sanitize_error_message(str(exc)),
            )

    @staticmethod
    def _request_body(
        kind: str, input: dict[str, Any], settings: dict[str, Any]
    ) -> tuple[str, dict[str, Any], str]:
        image_urls = [str(value) for value in input.get("image_urls") or ()]
        if kind in {"image.generate", "image.edit"}:
            default_key = str(settings.get("jimeng_image_req_key") or "t2i_v40_jimeng")
            req_key = str(input.get("model") or default_key)
            body: dict[str, Any] = {
                "req_key": req_key,
                "prompt": str(input["prompt"]),
                "force_single": int(input.get("count") or 1) == 1,
            }
            if image_urls:
                body["image_urls"] = image_urls
            if input.get("size"):
                size = str(input["size"])
                if "x" in size.lower():
                    width, height = size.lower().split("x", 1)
                    if width.isdigit() and height.isdigit():
                        body.update(width=int(width), height=int(height))
                elif size.isdigit():
                    body["size"] = int(size)
            media_kind = "image"
        elif kind == "video.generate":
            if image_urls:
                default_key = str(
                    settings.get("jimeng_video_image_req_key")
                    or "jimeng_i2v_first_v30"
                )
            else:
                default_key = str(
                    settings.get("jimeng_video_text_req_key") or "jimeng_t2v_v30"
                )
            req_key = str(input.get("model") or default_key)
            body = {"req_key": req_key, "prompt": str(input["prompt"])}
            if image_urls:
                body["image_urls"] = image_urls
            if input.get("ratio") and not image_urls:
                body["aspect_ratio"] = str(input["ratio"])
            if input.get("duration_seconds"):
                duration = int(input["duration_seconds"])
                if duration not in {5, 10}:
                    raise MediaProviderError(
                        "UNSUPPORTED_PARAMETER",
                        "Jimeng 3.0 supports duration_seconds 5 or 10",
                    )
                body["frames"] = duration * 24 + 1
            media_kind = "video"
        else:
            raise MediaProviderError("UNSUPPORTED_MEDIA_CAPABILITY", kind)
        if input.get("seed") is not None:
            body["seed"] = int(input["seed"])
        body["req_key"] = req_key
        return req_key, body, media_kind

    async def reconcile(
        self, context: CapabilityContext, operation: dict[str, Any]
    ) -> OperationReconciliationResult:
        if operation.get("status") == "submission_unknown":
            return OperationReconciliationResult(
                status="unknown",
                summary=(
                    "Jimeng submission outcome is unknown; manual provider "
                    "reconciliation is required"
                ),
                operation=operation,
            )
        credentials = self._credentials()
        task_id = str(operation.get("provider_operation_id") or "")
        req_key = str(operation.get("req_key") or operation.get("model") or "")
        try:
            if not credentials.access_key or not credentials.secret_key:
                raise MediaProviderError(
                    "CREDENTIAL_NOT_CONFIGURED",
                    "Jimeng AK/SK is not configured",
                    retryable=True,
                )
            body: dict[str, Any] = {"req_key": req_key, "task_id": task_id}
            if operation.get("media_kind") == "image":
                body["req_json"] = json.dumps(
                    {
                        "return_url": True,
                        "logo_info": {"add_logo": bool(operation.get("watermark", True))},
                    },
                    separators=(",", ":"),
                )
            value = await self._call(self._result_action, body, credentials)
            data = value.get("data") or {}
            status = str(data.get("status") or "").lower()
            current = {**operation, "status": status}
            if status in {"in_queue", "generating"}:
                return OperationReconciliationResult(
                    status="pending",
                    summary=f"Jimeng task is {status}",
                    operation=current,
                    retry_after_seconds=10,
                )
            if status in {"not_found", "expired"}:
                return OperationReconciliationResult(
                    status="unknown",
                    summary=f"Jimeng task is {status}; provider result cannot be proven",
                    operation=current,
                )
            if status != "done":
                return OperationReconciliationResult(
                    status="unknown",
                    summary=f"unknown Jimeng task status: {status or 'missing'}",
                    operation=current,
                )
            media_kind = str(operation.get("media_kind") or "")
            urls = (
                [str(value) for value in data.get("image_urls") or ()]
                if media_kind == "image"
                else [str(data.get("video_url") or "")]
            )
            urls = [url for url in urls if url]
            if not urls:
                return OperationReconciliationResult(
                    status="failed",
                    summary="Jimeng task has no media URL",
                    error={"code": "MEDIA_RESULT_MISSING", "message": "media URL is missing"},
                    operation=current,
                )
            artifacts = media_artifacts(
                action_id=str(operation.get("action_id") or context.action_id or task_id),
                media_kind=media_kind,
                urls=urls,
                provider=self.provider_id,
                model=req_key,
                operation_id=task_id,
                source_expires_seconds=86_400 if media_kind == "image" else 3_600,
            )
            return OperationReconciliationResult(
                status="succeeded",
                summary=f"Jimeng {media_kind} generation completed",
                output={
                    "provider": self.provider_id,
                    "task_id": task_id,
                    "artifact_ids": [item.artifact_id for item in artifacts],
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

    async def _call(
        self,
        action: str,
        body: dict[str, Any],
        credentials: JimengCredentials,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        payload = json.dumps(
            body, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
        params = {"Action": action, "Version": self._version}
        headers = sign_openapi_request(
            method="POST",
            url=self._base_url(),
            params=params,
            body=payload,
            access_key=credentials.access_key,
            secret_key=credentials.secret_key,
            region="cn-north-1",
            service="cv",
            idempotency_key=idempotency_key,
            session_token=credentials.session_token or None,
        )
        async with TrackedAsyncClient() as client:
            response = await client.post(
                self._base_url(),
                params=params,
                headers=headers,
                content=payload,
                timeout=60.0,
            )
        if error := response_error(response, "jimeng"):
            raise error
        value = response.json()
        if int(value.get("code") or 0) != 10000:
            code = str(value.get("code") or "JIMENG_REQUEST_FAILED")
            retryable = code in {"50429", "50430", "50500", "50501"}
            raise MediaProviderError(
                code,
                str(value.get("message") or "Jimeng request failed"),
                retryable=retryable,
                outcome_unknown=action == self._submit_action and retryable,
            )
        return dict(value)


__all__ = ["JimengAdapter", "JimengCredentials"]
