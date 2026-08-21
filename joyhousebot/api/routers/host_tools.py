from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status

from joyhousebot.api.dependencies import ContainerDep
from joyhousebot.api.host_tool_schemas import (
    CreateHostToolRequest,
    GrantedHostToolRequest,
    IssueHostToolGrantRequest,
)
from joyhousebot.application.presenters import record_dict

router = APIRouter(tags=["host-tools"])


async def _device(container, authorization: str, device_id: str):  # noqa: ANN001,ANN202
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Device Host Bearer token is required")
    value = await container.device_hosts.authenticate(device_id, token)
    if value is None:
        raise HTTPException(status_code=401, detail="invalid or revoked Device Host identity")
    return value


async def _grant(container, authorization: str):  # noqa: ANN001,ANN202
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Host Tool grant is required")
    value = await container.host_tools.authenticate_grant(token)
    if value is None:
        raise HTTPException(status_code=401, detail="invalid or expired Host Tool grant")
    return value


@router.post("/device-host/operations/{delivery_id}/tool-grant")
async def issue_host_tool_grant(
    delivery_id: str,
    body: IssueHostToolGrantRequest,
    container: ContainerDep,
    authorization: Annotated[str, Header()] = "",
    x_joyhousebot_device_id: Annotated[str, Header()] = "",
):
    device = await _device(container, authorization, x_joyhousebot_device_id)
    record, token = await container.host_tools.issue_for_device(
        device,
        delivery_id,
        **body.model_dump(),
    )
    return {
        "grant": {
            "grant_id": record.grant_id,
            "delivery_id": record.delivery_id,
            "expires_at": record.expires_at,
        },
        "tool_grant_token": token,
    }


@router.post(
    "/device-host/operations/{delivery_id}/tool-requests",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_host_tool_request(
    delivery_id: str,
    body: CreateHostToolRequest,
    container: ContainerDep,
    authorization: Annotated[str, Header()] = "",
    x_joyhousebot_device_id: Annotated[str, Header()] = "",
):
    device = await _device(container, authorization, x_joyhousebot_device_id)
    record, created = await container.host_tools.create(
        device,
        delivery_id,
        **body.model_dump(),
    )
    return {"request": record_dict(record), "created": created}


@router.get("/device-host/operations/{delivery_id}/tool-requests/{request_id}")
async def get_host_tool_request(
    delivery_id: str,
    request_id: str,
    container: ContainerDep,
    authorization: Annotated[str, Header()] = "",
    x_joyhousebot_device_id: Annotated[str, Header()] = "",
):
    device = await _device(container, authorization, x_joyhousebot_device_id)
    return {
        "request": record_dict(
            await container.host_tools.get(device, delivery_id, request_id)
        )
    }


@router.post("/host-tool-requests", status_code=status.HTTP_202_ACCEPTED)
async def create_granted_host_tool_request(
    body: GrantedHostToolRequest,
    container: ContainerDep,
    authorization: Annotated[str, Header()] = "",
):
    grant = await _grant(container, authorization)
    record, created = await container.host_tools.create_with_grant(
        grant, **body.model_dump()
    )
    return {"request": record_dict(record), "created": created}


@router.get("/host-tool-requests/{request_id}")
async def get_granted_host_tool_request(
    request_id: str,
    container: ContainerDep,
    authorization: Annotated[str, Header()] = "",
):
    grant = await _grant(container, authorization)
    return {
        "request": record_dict(
            await container.host_tools.get_with_grant(grant, request_id)
        )
    }
