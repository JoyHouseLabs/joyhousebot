"""User control and device-authenticated pull APIs for local Hosts."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, status

from joyhousebot.api.dependencies import ContainerDep, ContextDep
from joyhousebot.api.device_host_schemas import (
    AppendDeviceOperationEventsRequest,
    ClaimDeviceOperationsRequest,
    CompleteDeviceOperationRequest,
    CreateDeviceDeliveryRequest,
    DeviceCompletionResult,
    DeviceHeartbeatRequest,
    DeviceOperationLeaseHeartbeatRequest,
    IssueDeviceModelGrantRequest,
    RegisterDeviceHostRequest,
)
from joyhousebot.application.presenters import record_dict

router = APIRouter(tags=["device-hosts"])


def _public_device(record: Any) -> dict[str, Any]:
    return record_dict(record)


def _public_delivery(record: Any, *, include_request: bool) -> dict[str, Any]:
    value = record_dict(record)
    if not include_request:
        value.pop("request", None)
    value.pop("claim_session_id", None)
    return value


async def _authenticate_device(
    container: Any, authorization: str, device_id: str
) -> tuple[Any, str]:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Device Host Bearer token is required")
    device = await container.device_hosts.authenticate(device_id, token)
    if device is None:
        raise HTTPException(status_code=401, detail="invalid or revoked Device Host identity")
    return device, token


@router.post("/device-hosts", status_code=status.HTTP_201_CREATED)
async def register_device_host(
    body: RegisterDeviceHostRequest,
    context: ContextDep,
    container: ContainerDep,
):
    record, token = await container.device_hosts.register(
        context, **body.model_dump()
    )
    return {"device": _public_device(record), "device_token": token}


@router.get("/device-hosts")
async def list_device_hosts(context: ContextDep, container: ContainerDep):
    return {
        "items": [
            _public_device(record)
            for record in await container.device_hosts.list(context)
        ]
    }


@router.post("/device-hosts/{device_id}/token:rotate")
async def rotate_device_host_token(
    device_id: str, context: ContextDep, container: ContainerDep
):
    return {
        "device_id": device_id,
        "device_token": await container.device_hosts.rotate_token(context, device_id),
    }


@router.delete("/device-hosts/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_device_host(
    device_id: str, context: ContextDep, container: ContainerDep
) -> None:
    await container.device_hosts.revoke(context, device_id)


@router.post(
    "/runs/{run_id}/operations/{reconciliation_id}/device-deliveries",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_device_delivery(
    run_id: str,
    reconciliation_id: str,
    body: CreateDeviceDeliveryRequest,
    context: ContextDep,
    container: ContainerDep,
):
    record = await container.device_hosts.enqueue(
        context,
        run_id=run_id,
        reconciliation_id=reconciliation_id,
        **body.model_dump(),
    )
    return {"delivery": _public_delivery(record, include_request=False)}


@router.get("/device-deliveries/{delivery_id}")
async def get_device_delivery(
    delivery_id: str, context: ContextDep, container: ContainerDep
):
    record = await container.device_hosts.get_delivery(context, delivery_id)
    return {"delivery": _public_delivery(record, include_request=False)}


@router.get("/device-deliveries/{delivery_id}/events")
async def list_device_delivery_events(
    delivery_id: str,
    context: ContextDep,
    container: ContainerDep,
    after_sequence: int = Query(default=-1, ge=-1),
    limit: int = Query(default=200, ge=1, le=500),
):
    return {
        "items": [
            record_dict(record)
            for record in await container.device_hosts.events(
                context,
                delivery_id,
                after_sequence=after_sequence,
                limit=limit,
            )
        ]
    }


@router.post("/device-host/heartbeat")
async def heartbeat_device_host(
    body: DeviceHeartbeatRequest,
    container: ContainerDep,
    authorization: Annotated[str, Header()] = "",
    x_joyhouse_device_id: Annotated[str, Header()] = "",
):
    device, token = await _authenticate_device(
        container, authorization, x_joyhouse_device_id
    )
    record = await container.device_hosts.heartbeat_with_token(
        device, token, **body.model_dump()
    )
    return {"device": _public_device(record)}


@router.post("/device-host/operations:claim")
async def claim_device_operations(
    body: ClaimDeviceOperationsRequest,
    container: ContainerDep,
    authorization: Annotated[str, Header()] = "",
    x_joyhouse_device_id: Annotated[str, Header()] = "",
):
    device, _ = await _authenticate_device(
        container, authorization, x_joyhouse_device_id
    )
    records = await container.device_hosts.claim(device, **body.model_dump())
    return {
        "items": [
            _public_delivery(record, include_request=True) for record in records
        ]
    }


@router.post("/device-host/operations/{delivery_id}/model-grant")
async def issue_device_operation_model_grant(
    delivery_id: str,
    body: IssueDeviceModelGrantRequest,
    container: ContainerDep,
    authorization: Annotated[str, Header()] = "",
    x_joyhouse_device_id: Annotated[str, Header()] = "",
):
    device, _ = await _authenticate_device(
        container, authorization, x_joyhouse_device_id
    )
    record, token = await container.model_grants.issue_for_device(
        device,
        delivery_id,
        **body.model_dump(),
    )
    return {"grant": record_dict(record), "model_grant_token": token}


@router.post("/device-host/operations/{delivery_id}/events:append")
async def append_device_operation_events(
    delivery_id: str,
    body: AppendDeviceOperationEventsRequest,
    container: ContainerDep,
    authorization: Annotated[str, Header()] = "",
    x_joyhouse_device_id: Annotated[str, Header()] = "",
):
    device, _ = await _authenticate_device(
        container, authorization, x_joyhouse_device_id
    )
    record = await container.device_hosts.append_events(
        device,
        delivery_id,
        **body.model_dump(),
    )
    return {"delivery": _public_delivery(record, include_request=False)}


@router.post("/device-host/operations/{delivery_id}:heartbeat")
async def heartbeat_device_operation(
    delivery_id: str,
    body: DeviceOperationLeaseHeartbeatRequest,
    container: ContainerDep,
    authorization: Annotated[str, Header()] = "",
    x_joyhouse_device_id: Annotated[str, Header()] = "",
):
    device, _ = await _authenticate_device(
        container, authorization, x_joyhouse_device_id
    )
    record = await container.device_hosts.heartbeat_operation(
        device, delivery_id, **body.model_dump()
    )
    return {"delivery": _public_delivery(record, include_request=False)}


@router.post("/device-host/operations/{delivery_id}:complete")
async def complete_device_operation(
    delivery_id: str,
    body: CompleteDeviceOperationRequest,
    container: ContainerDep,
    authorization: Annotated[str, Header()] = "",
    x_joyhouse_device_id: Annotated[str, Header()] = "",
):
    device, _ = await _authenticate_device(
        container, authorization, x_joyhouse_device_id
    )
    result = DeviceCompletionResult.model_validate(body.result).model_dump(
        exclude_none=True
    )
    record = await container.device_hosts.complete(
        device,
        delivery_id,
        claim_session_id=body.claim_session_id,
        claim_version=body.claim_version,
        result=result,
    )
    return {"delivery": _public_delivery(record, include_request=False)}
