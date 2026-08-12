"""Authenticated owner API for durable Knowledge assets."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Response, status

from joyhousebot.api.dependencies import ContainerDep, ContextDep
from joyhousebot.api.knowledge_schemas import KnowledgeSourceSnapshotRequest
from joyhousebot.api.schemas import (
    CreateKnowledgeBaseRequest,
    UpdateKnowledgeBaseRequest,
)
from joyhousebot.application.presenters import record_dict

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("/index-requests", status_code=status.HTTP_202_ACCEPTED)
async def submit_knowledge_index_request(
    body: KnowledgeSourceSnapshotRequest,
    context: ContextDep,
    container: ContainerDep,
):
    """Compile one immutable source snapshot into the common Run/Task chain."""
    record = await container.knowledge_assets.submit_index_request(
        context, body.model_dump()
    )
    return record_dict(record)


@router.get("/bases")
async def list_knowledge_bases(
    context: ContextDep,
    container: ContainerDep,
    status_filter: Literal["active", "archived", "all"] = Query(
        default="all", alias="status"
    ),
):
    items = await container.knowledge_assets.list_bases(
        context,
        status=None if status_filter == "all" else status_filter,
    )
    return {"items": items}


@router.post("/bases", status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(
    body: CreateKnowledgeBaseRequest,
    context: ContextDep,
    container: ContainerDep,
):
    return await container.knowledge_assets.create_base(
        context,
        name=body.name,
        description=body.description,
    )


@router.patch("/bases/{knowledge_base_id}")
async def update_knowledge_base(
    knowledge_base_id: str,
    body: UpdateKnowledgeBaseRequest,
    context: ContextDep,
    container: ContainerDep,
):
    return await container.knowledge_assets.update_base(
        context,
        knowledge_base_id,
        name=body.name,
        description=body.description,
        status=body.status,
    )


@router.delete("/bases/{knowledge_base_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(
    knowledge_base_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    await container.knowledge_assets.delete_base(context, knowledge_base_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/bases/{knowledge_base_id}/documents/{doc_id}")
async def bind_knowledge_document(
    knowledge_base_id: str,
    doc_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    created = await container.knowledge_assets.bind_document(
        context, knowledge_base_id, doc_id
    )
    return {"bound": True, "created": created}


@router.delete(
    "/bases/{knowledge_base_id}/documents/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unbind_knowledge_document(
    knowledge_base_id: str,
    doc_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    await container.knowledge_assets.unbind_document(context, knowledge_base_id, doc_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/documents")
async def list_knowledge_documents(
    context: ContextDep,
    container: ContainerDep,
    knowledge_base_id: str | None = Query(default=None, max_length=200),
    source_type: Literal[
        "url",
        "note",
        "web",
        "file",
        "image",
        "video",
        "email",
        "capture",
        "paper",
        "report",
        "all",
    ] = "all",
    search: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=200, ge=1, le=500),
):
    """List this user's private indexed sources and compact totals."""
    items, summary = await container.knowledge_assets.list(
        context,
        knowledge_base_id=knowledge_base_id,
        source_type=None if source_type == "all" else source_type,
        search=search,
        limit=limit,
    )
    return {"items": items, "summary": summary}


@router.get("/documents/{doc_id}")
async def get_knowledge_document(
    doc_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    """Read one private source with its indexed chunks."""
    return await container.knowledge_assets.get(context, doc_id)


@router.get("/documents/{doc_id}/revisions")
async def list_knowledge_document_revisions(
    doc_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    """Inspect immutable index attempts without exposing another user's assets."""
    return {
        "items": await container.knowledge_assets.list_revisions(context, doc_id)
    }


@router.delete("/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_document(
    doc_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    """Remove one source while retaining a deletion audit event."""
    await container.knowledge_assets.delete(context, doc_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
