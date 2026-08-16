"""Scoped Host Artifact grant creation and unauthenticated one-use upload."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, status

from joyhousebot.api.artifact_schemas import CreateArtifactUploadGrantRequest
from joyhousebot.api.dependencies import ContainerDep, ContextDep
from joyhousebot.application.errors import ValidationError
from joyhousebot.application.presenters import record_dict

router = APIRouter(tags=["artifact-uploads"])


@router.post(
    "/runs/{run_id}/operations/{reconciliation_id}/artifact-upload-grants",
    status_code=status.HTTP_201_CREATED,
)
async def create_artifact_upload_grant(
    run_id: str,
    reconciliation_id: str,
    body: CreateArtifactUploadGrantRequest,
    context: ContextDep,
    container: ContainerDep,
):
    grant, token = await container.artifact_uploads.create(
        context,
        run_id=run_id,
        reconciliation_id=reconciliation_id,
        **body.model_dump(),
    )
    public = record_dict(grant)
    for key in ("lease_owner", "lease_expires_at", "lease_version"):
        public.pop(key, None)
    return {
        "grant": public,
        "upload_token": token,
        "upload_url": f"/v1/artifact-upload-grants/{grant.grant_id}",
    }


@router.put("/artifact-upload-grants/{grant_id}", status_code=status.HTTP_202_ACCEPTED)
async def upload_artifact(
    grant_id: str,
    request: Request,
    container: ContainerDep,
    operation_id: str = Query(min_length=1, max_length=256),
    x_joyhouse_action_id: Annotated[str, Header(min_length=1, max_length=256)] = "",
    x_content_sha256: Annotated[str, Header(min_length=64, max_length=64)] = "",
    content_length: Annotated[int | None, Header(ge=0)] = None,
    authorization: Annotated[str, Header()] = "",
):
    if not authorization.startswith("Bearer ") or len(authorization) <= 7:
        raise ValidationError("Artifact upload Bearer token is required")
    if content_length is None:
        raise ValidationError("Content-Length is required")
    record = await container.artifact_uploads.upload(
        grant_id,
        authorization[7:],
        request.stream(),
        operation_id=operation_id,
        action_id=x_joyhouse_action_id,
        media_type=request.headers.get("content-type", "application/octet-stream"),
        content_sha256=x_content_sha256,
        content_length=content_length,
    )
    return {"grant_id": record.grant_id, "status": record.status}
