"""Evaluation datasets, scored observations, and release-gate control API."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query

from joyhousebot.api.dependencies import ContainerDep, EvalsReaderDep, EvalsWriterDep
from joyhousebot.api.schemas import (
    CreateEvalRunRequest,
    ExecuteEvalRunRequest,
    RecordEvalObservationRequest,
    SaveEvalSuiteRequest,
    SaveReleaseGateRequest,
)

router = APIRouter(prefix="/admin", tags=["evaluations"])


@router.get("/eval-suites")
async def list_eval_suites(principal: EvalsReaderDep, container: ContainerDep):
    return {"items": await container.evals.list_suites()}


@router.put("/eval-suites/{suite_id}/versions/{version}")
async def save_eval_suite(
    suite_id: str,
    version: int,
    body: SaveEvalSuiteRequest,
    principal: EvalsWriterDep,
    container: ContainerDep,
):
    if body.suite_id != suite_id or body.version != version:
        raise HTTPException(status_code=400, detail="suite identity must match path")
    value = body.model_dump()
    value["cases"] = [
        {
            **case.model_dump(exclude={"scorers"}),
            "scorers": [scorer.model_dump(by_alias=True) for scorer in case.scorers],
        }
        for case in body.cases
    ]
    return await container.evals.save_suite(value, actor_id=principal.subject)


@router.post("/eval-runs", status_code=201)
async def create_eval_run(
    body: CreateEvalRunRequest,
    principal: EvalsWriterDep,
    container: ContainerDep,
):
    return await container.evals.create_run(
        body.model_dump(), actor_id=principal.subject
    )


@router.get("/eval-runs")
async def list_eval_runs(
    principal: EvalsReaderDep,
    container: ContainerDep,
    target_type: str | None = None,
    target_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
):
    return {
        "items": await container.evals.list_runs(
            target_type=target_type, target_id=target_id, limit=limit
        )
    }


@router.post("/eval-runs/{eval_run_id}/observations")
async def record_eval_observation(
    eval_run_id: str,
    body: RecordEvalObservationRequest,
    principal: EvalsWriterDep,
    container: ContainerDep,
):
    return await container.evals.record_observation(eval_run_id, body.model_dump())


@router.post("/eval-runs/{eval_run_id}/finalize")
async def finalize_eval_run(
    eval_run_id: str,
    principal: EvalsWriterDep,
    container: ContainerDep,
):
    return await container.evals.finalize_run(eval_run_id)


@router.post("/eval-runs/{eval_run_id}/execute")
async def execute_eval_run(
    eval_run_id: str,
    body: ExecuteEvalRunRequest,
    principal: EvalsWriterDep,
    container: ContainerDep,
):
    return await container.eval_execution.execute(
        eval_run_id,
        actor_id=principal.subject,
        max_concurrency=body.max_concurrency,
        case_timeout_seconds=body.case_timeout_seconds,
    )


@router.put("/release-gates/{target_type}/{target_id}/{target_revision_id}")
async def save_release_gate(
    target_type: str,
    target_id: str,
    target_revision_id: str,
    body: SaveReleaseGateRequest,
    principal: EvalsWriterDep,
    container: ContainerDep,
):
    return await container.evals.save_release_gate(
        {
            "target_type": target_type,
            "target_id": target_id,
            "target_revision_id": target_revision_id,
            "required": body.required,
            "requirements": [item.model_dump() for item in body.requirements],
        },
        actor_id=principal.subject,
    )


@router.get("/release-gates/{target_type}/{target_id}/{target_revision_id}")
async def get_release_gate(
    target_type: str,
    target_id: str,
    target_revision_id: str,
    principal: EvalsReaderDep,
    container: ContainerDep,
):
    value = await asyncio.to_thread(
        container.store.get_release_gate_policy,
        target_type,
        target_id,
        target_revision_id,
    )
    if value is None:
        raise HTTPException(status_code=404, detail="release gate not found")
    return value
