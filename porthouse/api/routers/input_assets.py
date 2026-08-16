"""Owner API for streaming immutable execution inputs into the Runtime."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, status

from porthouse.api.dependencies import ContainerDep, ContextDep
from porthouse.application.errors import ValidationError
from porthouse.application.presenters import input_asset_public_dict

router = APIRouter(prefix="/input-assets", tags=["input-assets"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_input_asset(
    request: Request,
    context: ContextDep,
    container: ContainerDep,
    file_name: str = Query(min_length=1, max_length=500),
    x_content_sha256: Annotated[str, Header(min_length=64, max_length=64)] = "",
    content_length: Annotated[int | None, Header(ge=0)] = None,
):
    if content_length is None:
        raise ValidationError("Content-Length is required")
    record, created = await container.input_assets.upload(
        context,
        request.stream(),
        original_name=file_name,
        media_type=request.headers.get("content-type", "application/octet-stream"),
        content_sha256=x_content_sha256,
        content_length=content_length,
    )
    return {**input_asset_public_dict(record), "created": created}


@router.get("/{asset_id}")
async def get_input_asset(
    asset_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    return input_asset_public_dict(await container.input_assets.get(context, asset_id))


@router.delete("/{asset_id}")
async def delete_input_asset(
    asset_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    return input_asset_public_dict(await container.input_assets.delete(context, asset_id))


__all__ = ["router"]
