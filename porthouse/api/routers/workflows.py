"""User-owned AI Workflow Studio endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Response

from porthouse.api.dependencies import ContainerDep, ContextDep
from porthouse.api.schemas import (
    ExecuteWorkflowRequest,
    GenerateWorkflowRequest,
    PublishWorkflowRequest,
    SaveWorkflowRequest,
)
from porthouse.application.presenters import record_dict

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.get("")
async def list_workflows(context: ContextDep, container: ContainerDep):
    return {"items": await container.workflows.list(context)}


@router.post("/generations", status_code=202)
async def generate_workflow(
    body: GenerateWorkflowRequest,
    context: ContextDep,
    container: ContainerDep,
    response: Response,
):
    run = await container.workflows.start_generation(context, body.model_dump())
    response.headers["Location"] = f"/v1/workflows/generations/{run.run_id}"
    return record_dict(run)


@router.get("/generations/{run_id}")
async def get_workflow_generation(
    run_id: str, context: ContextDep, container: ContainerDep
):
    return await container.workflows.generation(context, run_id)


@router.post("", status_code=201)
async def create_workflow(
    body: SaveWorkflowRequest, context: ContextDep, container: ContainerDep
):
    return await container.workflows.save(context, None, body.model_dump())


@router.get("/{workflow_id}")
async def get_workflow(workflow_id: str, context: ContextDep, container: ContainerDep):
    return await container.workflows.get(context, workflow_id)


@router.post("/{workflow_id}/revisions", status_code=201)
async def create_workflow_revision(
    workflow_id: str,
    body: SaveWorkflowRequest,
    context: ContextDep,
    container: ContainerDep,
):
    return await container.workflows.save(context, workflow_id, body.model_dump())


@router.post("/{workflow_id}/publish")
async def publish_workflow(
    workflow_id: str,
    body: PublishWorkflowRequest,
    context: ContextDep,
    container: ContainerDep,
):
    return await container.workflows.publish(context, workflow_id, body.revision_id)


@router.post("/{workflow_id}/runs", status_code=202)
async def execute_workflow(
    workflow_id: str,
    body: ExecuteWorkflowRequest,
    context: ContextDep,
    container: ContainerDep,
    response: Response,
):
    run = await container.workflows.execute(context, workflow_id, body.model_dump())
    response.headers["Location"] = f"/v1/runs/{run.run_id}"
    return record_dict(run)


@router.delete("/{workflow_id}", status_code=204)
async def delete_workflow(
    workflow_id: str, context: ContextDep, container: ContainerDep
) -> Response:
    await container.workflows.delete(context, workflow_id)
    return Response(status_code=204)
