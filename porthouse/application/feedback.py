"""Run feedback use cases."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from porthouse.application.context import RequestContext


@dataclass(slots=True)
class CreateFeedbackCommand:
    comment: str
    feedback_type: str = "other"
    rating: str | None = None
    output_excerpt: str | None = None
    turn_id: str | None = None
    message_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class FeedbackService:
    def __init__(self, runs: Any, store: Any) -> None:
        self.runs = runs
        self.store = store

    async def create(
        self, context: RequestContext, run_id: str, command: CreateFeedbackCommand
    ) -> Any:
        """Persist human feedback with the Run's execution snapshot for audit/replay."""
        run = await self.runs.get(context, run_id)
        snapshot = await asyncio.to_thread(self.store.get_run_execution_snapshot, run_id)
        row = await asyncio.to_thread(
            self.store.create_run_feedback,
            run_id=run_id,
            user_id=context.user_id,
            agent_id=run.agent_id,
            session_id=run.session_id,
            agent_revision_id=snapshot.agent_revision_id if snapshot else None,
            turn_id=command.turn_id,
            message_id=command.message_id,
            feedback_type=command.feedback_type,
            rating=command.rating,
            comment=command.comment.strip(),
            output_excerpt=command.output_excerpt,
            metadata={**command.metadata, "source": "web-playground"},
        )
        await asyncio.to_thread(
            self.store.append_runtime_log,
            run_id=run_id,
            stage="feedback.created",
            message="Human feedback recorded for Run output",
            data={
                "feedback_id": row.feedback_id,
                "feedback_type": row.feedback_type,
                "actor": context.user_id,
            },
        )
        return row
