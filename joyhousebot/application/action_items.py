"""A derived, user-scoped view of Runtime work that needs human action.

This is intentionally not another task or inbox state machine.  Input
requests and approvals are authoritative in their existing tables; this
service only presents their currently actionable projection.
"""

from __future__ import annotations

import asyncio
from typing import Any

from joyhousebot.application.context import RequestContext


class ActionItemService:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def list(self, context: RequestContext, *, limit: int = 100) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(200, limit))
        inputs, approvals = await asyncio.gather(
            asyncio.to_thread(
                self.store.list_pending_user_input_requests,
                user_id=context.user_id,
                limit=bounded_limit,
            ),
            asyncio.to_thread(
                self.store.list_pending_user_approval_requests,
                user_id=context.user_id,
                limit=bounded_limit,
            ),
        )
        items = [
            self._input_item(row)
            for row in inputs
            if self._visible_to_principal(context, row)
        ] + [
            self._approval_item(row, context=context)
            for row in approvals
            if self._visible_to_principal(context, row)
        ]
        items.sort(key=lambda item: (str(item["created_at"]), str(item["item_id"])))
        return items[:bounded_limit]

    @staticmethod
    def _visible_to_principal(context: RequestContext, row: dict[str, Any]) -> bool:
        installation_id = context.principal.app_installation_id
        if not installation_id:
            return True
        options = dict(row.get("run_options") or {})
        metadata = dict(options.get("metadata") or {})
        app = dict(metadata.get("app") or {})
        return str(app.get("installation_id") or "") == installation_id

    @staticmethod
    def _run_payload(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": str(row["run_id"]),
            "agent_id": str(row["agent_id"]),
            "status": str(row["run_status"]),
            "title": str(row.get("status_summary") or row.get("run_prompt") or "Agent Run"),
            "updated_at": str(row["run_updated_at"]),
        }

    def _input_item(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "item_id": f"input:{row['input_request_id']}",
            "kind": "input",
            "created_at": str(row["created_at"]),
            "expires_at": str(row["expires_at"]) if row.get("expires_at") else None,
            "run": self._run_payload(row),
            "input": {
                "input_request_id": str(row["input_request_id"]),
                "question": str(row["question"]),
                "fields": list(row.get("fields") or []),
                "presentation": dict(row.get("presentation") or {}),
                "source": str(row.get("source") or "scenario"),
            },
        }

    def _approval_item(self, row: dict[str, Any], *, context: RequestContext) -> dict[str, Any]:
        required_role = str(row.get("required_role") or "owner")
        can_resolve = required_role != "operator" or context.principal.can(
            "approvals.resolve.operator"
        )
        return {
            "item_id": f"approval:{row['approval_id']}",
            "kind": "approval",
            "created_at": str(row["requested_at"]),
            "expires_at": str(row["expires_at"]) if row.get("expires_at") else None,
            "run": self._run_payload(row),
            "approval": {
                "approval_id": str(row["approval_id"]),
                "subject_type": str(row.get("subject_type") or "action"),
                "subject": dict(row.get("subject") or {}),
                "capability_ref": dict(row.get("capability_ref") or {}),
                "input_preview": dict(row.get("input_preview") or {}),
                "risk": str(row.get("risk") or "unknown"),
                "data_classification": str(row.get("data_classification") or "internal"),
                "required_role": required_role,
                "can_resolve": can_resolve,
            },
        }
