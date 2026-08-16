"""Authenticated owner API for durable Knowledge assets."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Response, status

from porthouse.api.dependencies import ContainerDep, ContextDep
from porthouse.api.knowledge_schemas import (
    KnowledgeReembeddingRequest,
    KnowledgeSourceSnapshotRequest,
)
from porthouse.api.schemas import (
    CreateKnowledgeBaseRequest,
    UpdateKnowledgeBaseRequest,
)
from porthouse.application.presenters import record_dict

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("/index-requests", status_code=status.HTTP_202_ACCEPTED)
async def submit_knowledge_index_request(
    body: KnowledgeSourceSnapshotRequest,
    context: ContextDep,
    container: ContainerDep,
):
    """Compile one immutable source snapshot into the common Run/Task chain."""
    record = await container.knowledge_assets.submit_index_request(context, body.model_dump())
    return record_dict(record)


@router.get("/bases")
async def list_knowledge_bases(
    context: ContextDep,
    container: ContainerDep,
    status_filter: Literal["active", "archived", "all"] = Query(default="all", alias="status"),
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
    created = await container.knowledge_assets.bind_document(context, knowledge_base_id, doc_id)
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


@router.get("/search")
async def search_knowledge(
    context: ContextDep,
    container: ContainerDep,
    query: str = Query(alias="q", min_length=1, max_length=200),
    top_k: int = Query(default=20, ge=1, le=50),
    knowledge_base_id: str | None = Query(default=None, max_length=200),
    collection_ref: str | None = Query(default=None, max_length=200),
    doc_id: str | None = Query(default=None, max_length=200),
    source_type: str | None = Query(default=None, max_length=40),
):
    """Search the active private index and return source-positioned evidence."""
    items = await container.knowledge_assets.search(
        context,
        query=query,
        top_k=top_k,
        knowledge_base_id=knowledge_base_id,
        collection_ref=collection_ref,
        doc_id=doc_id,
        source_type=source_type,
    )
    return {"items": items}


@router.get("/source-state")
async def get_knowledge_source_state(
    context: ContextDep,
    container: ContainerDep,
    source_system: str = Query(min_length=1, max_length=100),
    source_id: str = Query(min_length=1, max_length=200),
):
    """Resolve one external source to its active document and immutable revisions."""
    return await container.knowledge_assets.get_source_state(
        context, source_system=source_system, source_id=source_id
    )


@router.get("/health")
async def get_knowledge_index_health(
    context: ContextDep,
    container: ContainerDep,
    window_days: int = Query(default=30, ge=1, le=365),
):
    """Return owner-scoped index readiness, latency, and failure aggregates."""
    return await container.knowledge_assets.health(context, window_days=window_days)


@router.post("/reembedding-jobs", status_code=status.HTTP_202_ACCEPTED)
async def enqueue_knowledge_reembedding(
    body: KnowledgeReembeddingRequest,
    context: ContextDep,
    container: ContainerDep,
):
    """Queue an owner-scoped projection upgrade; parsing and active chunks are unchanged."""
    return await container.knowledge_maintenance.enqueue_reembedding(
        context,
        embedding_profile_id=body.embedding_profile_id,
        knowledge_base_id=body.knowledge_base_id,
        doc_id=body.doc_id,
    )


@router.get("/reembedding-jobs")
async def list_knowledge_reembedding_jobs(
    context: ContextDep,
    container: ContainerDep,
    limit: int = Query(default=100, ge=1, le=500),
):
    return {
        "items": await container.knowledge_maintenance.list_jobs(context, limit=limit)
    }


@router.get("/reembedding-jobs/{job_id}")
async def get_knowledge_reembedding_job(
    job_id: str, context: ContextDep, container: ContainerDep
):
    return await container.knowledge_maintenance.get_job(context, job_id)


@router.delete(
    "/reembedding-jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def cancel_knowledge_reembedding_job(
    job_id: str, context: ContextDep, container: ContainerDep
):
    await container.knowledge_maintenance.cancel_job(context, job_id)
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
    return {"items": await container.knowledge_assets.list_revisions(context, doc_id)}


@router.delete("/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_document(
    doc_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    """Remove one source while retaining a deletion audit event."""
    await container.knowledge_assets.delete(context, doc_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
