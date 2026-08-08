"""Replay experiment use cases for platform administration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from joyhousebot.application.errors import NotFoundError
from joyhousebot.runtime.models import AgentOptions


@dataclass(slots=True)
class CreateReplayCommand:
    mode: str = "offline"
    source_turn_id: str | None = None
    prompt: str | None = None
    model: str | None = None
    agent_id: str | None = None
    system_prompt: str | None = None


def replay_comparison(source: Any, target: Any = None) -> dict[str, Any]:
    source_content = str(((source.result or {}).get("content") or ""))
    target_content = str(((target.result or {}).get("content") or "")) if target else source_content
    return {
        "source_status": source.status,
        "target_status": target.status if target else source.status,
        "content_equal": source_content == target_content,
        "source_length": len(source_content),
        "target_length": len(target_content),
    }


class ReplayService:
    def __init__(self, runtime: Any, store: Any) -> None:
        self.runtime = runtime
        self.store = store

    async def create(
        self, run_id: str, command: CreateReplayCommand, *, actor_id: str
    ) -> dict[str, Any]:
        source = await asyncio.to_thread(self.store.get_runtime_run, run_id)
        if source is None:
            raise NotFoundError("run not found")
        overrides = {
            key: value
            for key, value in {
                "prompt": command.prompt,
                "model": command.model,
                "agent_id": command.agent_id,
                "system_prompt": command.system_prompt,
            }.items()
            if value is not None
        }
        replay = await asyncio.to_thread(
            self.store.create_replay_run,
            source_run_id=run_id,
            source_turn_id=command.source_turn_id,
            mode=command.mode,
            overrides=overrides,
            created_by=actor_id,
            status="completed" if command.mode in {"offline", "frozen"} else "queued",
            comparison=(
                replay_comparison(source) if command.mode in {"offline", "frozen"} else None
            ),
            finished_at=(
                source.updated_at if command.mode in {"offline", "frozen"} else None
            ),
        )
        await asyncio.to_thread(
            self.store.append_runtime_log,
            run_id=run_id,
            stage="replay.created",
            message="Replay experiment created",
            data={"actor": actor_id, "replay_id": replay.replay_id, "mode": command.mode},
        )
        if command.mode in {"offline", "frozen"}:
            return replay.to_dict()

        values = dict(source.options or {})
        metadata = {
            **dict(values.get("metadata") or {}),
            "replay": {
                "replay_id": replay.replay_id,
                "source_run_id": source.run_id,
                "source_turn_id": command.source_turn_id,
                "mode": command.mode,
            },
        }
        options = AgentOptions.from_dict(
            {
                **values,
                "prompt": command.prompt if command.prompt is not None else source.prompt,
                "user_id": source.user_id,
                "session_id": source.session_id,
                "agent_id": command.agent_id or source.agent_id,
                "model": command.model if command.model is not None else values.get("model"),
                "system_prompt": (
                    command.system_prompt
                    if command.system_prompt is not None
                    else values.get("system_prompt")
                ),
                "metadata": metadata,
                "idempotency_key": None,
                "root_run_id": source.root_run_id or source.run_id,
                "parent_run_id": source.run_id,
                "parent_task_id": None,
                "request_id": None,
                "tracker_id": None,
            }
        )
        new_run = await self.runtime.submit_run(options)
        await asyncio.to_thread(
            self.store.update_replay_run,
            replay.replay_id,
            new_run_id=new_run.run_id,
            status="running",
            finished_at=None,
        )
        replay = await asyncio.to_thread(self.store.get_replay_run, replay.replay_id)
        return replay.to_dict()
