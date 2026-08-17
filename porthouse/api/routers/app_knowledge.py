"""App-scoped knowledge data plane: path-is-owned, never user-global.

Mirrors ``/v1/apps/{installation_id}/runs|schedules``: the installation in
the path owns the namespace, delegated principals are pinned to their own
installation, and the public ``/v1/knowledge`` surface stays unchanged for
personal libraries.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from porthouse.api.dependencies import ContainerDep, ContextDep
from porthouse.api.knowledge_schemas import KnowledgeSourceSnapshotRequest

router = APIRouter(prefix="/apps", tags=["apps"])


def _require_installation(context: ContextDep, installation_id: str) -> None:
    if (
        context.principal.app_installation_id
        and context.principal.app_installation_id != installation_id
    ):
        raise HTTPException(status_code=404, detail="App installation not found")


@router.post("/{installation_id}/knowledge/index-requests", status_code=202)
async def submit_app_knowledge_index(
    installation_id: str,
    body: KnowledgeSourceSnapshotRequest,
    context: ContextDep,
    container: ContainerDep,
):
    _require_installation(context, installation_id)
    if not context.idempotency_key:
        raise HTTPException(
            status_code=400,
            detail="Knowledge indexing requires an Idempotency-Key header",
        )
    record = await container.knowledge_assets.submit_index_request(
        context,
        body.model_dump(),
        app_installation_id=installation_id,
    )
    return {"run_id": record.run_id}


@router.get("/{installation_id}/knowledge/search")
async def search_app_knowledge(
    installation_id: str,
    context: ContextDep,
    container: ContainerDep,
    q: str,
    top_k: int = 10,
    source_type: str | None = None,
    collection_ref: str | None = None,
):
    _require_installation(context, installation_id)
    return {
        "items": await container.knowledge_assets.search(
            context,
            query=q,
            top_k=min(max(top_k, 1), 50),
            source_type=source_type,
            collection_ref=collection_ref,
            app_installation_id=installation_id,
        )
    }


@router.get("/{installation_id}/knowledge/documents")
async def list_app_knowledge_documents(
    installation_id: str,
    context: ContextDep,
    container: ContainerDep,
    source_type: str | None = None,
    search: str | None = None,
    limit: int = 200,
):
    _require_installation(context, installation_id)
    items, summary = await container.knowledge_assets.list(
        context,
        source_type=source_type,
        search=search,
        limit=limit,
        app_installation_id=installation_id,
    )
    return {"items": items, "summary": summary}


@router.get("/{installation_id}/knowledge/documents/{doc_id}")
async def get_app_knowledge_document(
    installation_id: str,
    doc_id: str,
    context: ContextDep,
    container: ContainerDep,
):
    _require_installation(context, installation_id)
    document = await container.knowledge_assets.get(
        context, doc_id, app_installation_id=installation_id
    )
    return document


@router.get("/{installation_id}/knowledge/source-state")
async def get_app_knowledge_source_state(
    installation_id: str,
    context: ContextDep,
    container: ContainerDep,
    source_system: str,
    source_id: str,
):
    _require_installation(context, installation_id)
    state = await container.knowledge_assets.get_source_state(
        context,
        source_system=source_system,
        source_id=source_id,
        app_installation_id=installation_id,
    )
    return state
