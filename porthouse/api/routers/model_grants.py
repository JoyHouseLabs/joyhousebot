"""User-authenticated control plane for Host model grants."""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from porthouse.api.dependencies import ContainerDep, ContextDep
from porthouse.api.model_grant_schemas import CreateHostModelGrantRequest
from porthouse.application.presenters import record_dict

router = APIRouter(tags=["host-model-grants"])


@router.post(
    "/device-deliveries/{delivery_id}/model-grants",
    status_code=status.HTTP_201_CREATED,
)
async def issue_host_model_grant(
    delivery_id: str,
    body: CreateHostModelGrantRequest,
    context: ContextDep,
    container: ContainerDep,
):
    record, token = await container.model_grants.issue(
        context,
        delivery_id,
        **body.model_dump(),
    )
    return {"grant": record_dict(record), "model_grant_token": token}


@router.get("/model-grants")
async def list_host_model_grants(
    context: ContextDep,
    container: ContainerDep,
    delivery_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    return {
        "items": [
            record_dict(record)
            for record in await container.model_grants.list(
                context,
                delivery_id=delivery_id,
                limit=limit,
            )
        ]
    }


@router.delete("/model-grants/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_host_model_grant(
    grant_id: str,
    context: ContextDep,
    container: ContainerDep,
) -> None:
    await container.model_grants.revoke(context, grant_id)
