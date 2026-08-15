"""Personal works, immutable versions, sharing, and public presentation."""

from __future__ import annotations

from fastapi import APIRouter, Response

from joyhousebot.api.dependencies import ContainerDep, ContextDep
from joyhousebot.api.schemas import (
    CreateWorkRequest,
    CreateWorkShareRequest,
    CreateWorkVersionRequest,
    GrantWorkCollaboratorRequest,
    UpdateWorkRequest,
)
from joyhousebot.api.work_schemas import (
    CreateWorkHandoffReceiptRequest,
    CreateWorkHandoffRequest,
)

router = APIRouter(tags=["works"])


@router.get("/works")
async def list_works(context: ContextDep, container: ContainerDep):
    return {"items": await container.works.list(context)}


@router.post("/works", status_code=201)
async def create_work(
    body: CreateWorkRequest, context: ContextDep, container: ContainerDep
):
    return await container.works.create(context, body.model_dump())


@router.get("/works/{work_id}")
async def get_work(work_id: str, context: ContextDep, container: ContainerDep):
    return await container.works.get(context, work_id)


@router.patch("/works/{work_id}")
async def update_work(
    work_id: str,
    body: UpdateWorkRequest,
    context: ContextDep,
    container: ContainerDep,
):
    return await container.works.update(
        context, work_id, body.model_dump(exclude_unset=True)
    )


@router.post("/works/{work_id}/versions", status_code=201)
async def create_work_version(
    work_id: str,
    body: CreateWorkVersionRequest,
    context: ContextDep,
    container: ContainerDep,
):
    return await container.works.add_version(context, work_id, body.model_dump())


@router.get("/works/{work_id}/consumers")
async def list_work_consumers(
    work_id: str, context: ContextDep, container: ContainerDep
):
    return {"items": await container.works.list_consumers(context, work_id)}


@router.get("/works/{work_id}/handoffs")
async def list_work_handoffs(
    work_id: str, context: ContextDep, container: ContainerDep
):
    return {"items": await container.works.list_handoffs(context, work_id)}


@router.post("/works/{work_id}/handoffs", status_code=201)
async def create_work_handoff(
    work_id: str,
    body: CreateWorkHandoffRequest,
    context: ContextDep,
    container: ContainerDep,
):
    return await container.works.create_handoff(context, work_id, body.model_dump())


@router.get("/work-handoffs/{handoff_id}/input")
async def get_work_handoff_input(
    handoff_id: str, context: ContextDep, container: ContainerDep
):
    return await container.works.handoff_input(context, handoff_id)


@router.get("/work-handoffs/{handoff_id}/receipts")
async def list_work_handoff_receipts(
    handoff_id: str, context: ContextDep, container: ContainerDep
):
    return {"items": await container.works.list_handoff_receipts(context, handoff_id)}


@router.post("/work-handoffs/{handoff_id}/receipt", status_code=201)
async def create_work_handoff_receipt(
    handoff_id: str,
    body: CreateWorkHandoffReceiptRequest,
    context: ContextDep,
    container: ContainerDep,
):
    return await container.works.add_handoff_receipt(
        context, handoff_id, body.model_dump()
    )


@router.post("/work-handoffs/{handoff_id}/cancel")
async def cancel_work_handoff(
    handoff_id: str, context: ContextDep, container: ContainerDep
):
    return await container.works.cancel_handoff(context, handoff_id)


@router.get("/works/{work_id}/shares")
async def list_work_shares(
    work_id: str, context: ContextDep, container: ContainerDep
):
    return {"items": await container.works.list_shares(context, work_id)}


@router.post("/works/{work_id}/shares", status_code=201)
async def create_work_share(
    work_id: str,
    body: CreateWorkShareRequest,
    context: ContextDep,
    container: ContainerDep,
):
    return await container.works.create_share(context, work_id, body.model_dump())


@router.post("/works/{work_id}/shares/{share_id}/revoke")
async def revoke_work_share(
    work_id: str,
    share_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    return await container.works.revoke_share(context, work_id, share_id)


@router.get("/works/{work_id}/collaborators")
async def list_work_collaborators(
    work_id: str, context: ContextDep, container: ContainerDep
):
    return {"items": await container.works.list_collaborators(context, work_id)}


@router.put("/works/{work_id}/collaborators/{user_id}")
async def grant_work_collaborator(
    work_id: str,
    user_id: str,
    body: GrantWorkCollaboratorRequest,
    context: ContextDep,
    container: ContainerDep,
):
    value = body.model_dump()
    value["user_id"] = user_id
    return await container.works.grant_collaborator(context, work_id, value)


@router.delete("/works/{work_id}/collaborators/{user_id}", status_code=204)
async def revoke_work_collaborator(
    work_id: str,
    user_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    await container.works.revoke_collaborator(context, work_id, user_id)
    return Response(status_code=204)


@router.get("/works/{work_id}/audit")
async def list_work_audit(
    work_id: str, context: ContextDep, container: ContainerDep
):
    return {"items": await container.works.audit(context, work_id)}


@router.get("/public/works/{slug}")
async def get_public_work(slug: str, container: ContainerDep):
    return await container.works.resolve_public(slug=slug)


@router.get("/public/shares/{token}")
async def get_shared_work(token: str, container: ContainerDep):
    return await container.works.resolve_share(token=token)
