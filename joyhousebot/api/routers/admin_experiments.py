"""Online Experiment control API for exact Agent revision variants."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from joyhousebot.api.dependencies import ContainerDep, EvalsReaderDep, EvalsWriterDep
from joyhousebot.api.experiment_schemas import SaveExperimentRequest, SetExperimentStatusRequest

router = APIRouter(prefix="/admin/experiments", tags=["experiments"])


@router.get("")
async def list_experiments(principal: EvalsReaderDep, container: ContainerDep):
    return {"items": await container.experiments.list()}


@router.get("/{experiment_id}")
async def get_experiment(experiment_id: str, principal: EvalsReaderDep, container: ContainerDep):
    try:
        return await container.experiments.get(experiment_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/{experiment_id}")
async def save_experiment(
    experiment_id: str,
    body: SaveExperimentRequest,
    principal: EvalsWriterDep,
    container: ContainerDep,
):
    if body.experiment_id != experiment_id:
        raise HTTPException(status_code=400, detail="body experiment_id must match path")
    try:
        return await container.experiments.save_draft(body.model_dump(), actor_id=principal.subject)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{experiment_id}/start")
async def start_experiment(
    experiment_id: str, principal: EvalsWriterDep, container: ContainerDep
):
    try:
        return await container.experiments.start(experiment_id, actor_id=principal.subject)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.put("/{experiment_id}/status")
async def set_experiment_status(
    experiment_id: str,
    body: SetExperimentStatusRequest,
    principal: EvalsWriterDep,
    container: ContainerDep,
):
    try:
        return await container.experiments.set_status(
            experiment_id,
            status=body.status,
            reason=body.reason,
            actor_id=principal.subject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{experiment_id}/summary")
async def experiment_summary(
    experiment_id: str, principal: EvalsReaderDep, container: ContainerDep
):
    try:
        return await container.experiments.summary(experiment_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
