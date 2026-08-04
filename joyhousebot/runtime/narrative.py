"""Safe, deterministic execution narration for runtime events.

The runtime intentionally exposes concise evidence-backed progress summaries,
not a provider's private reasoning content.  All persisted event payloads pass
through the same redaction boundary so SSE, logs, and replay agree.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from joyhousebot.runtime.models import AgentEvent, EventVisibility
from joyhousebot.runtime.tracking import redact_sensitive_text

_SENSITIVE_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
)
_MAX_STRING = 8_000
_MAX_COLLECTION = 200


def _sensitive(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in _SENSITIVE_PARTS)


def redact_runtime_value(value: Any, *, key: str = "", depth: int = 0) -> Any:
    """Return JSON-safe bounded data with credentials removed."""

    if key and _sensitive(key):
        return "[REDACTED]"
    if depth >= 12:
        return "[MAX_DEPTH]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        redacted = redact_sensitive_text(value)
        return redacted if len(redacted) <= _MAX_STRING else redacted[:_MAX_STRING] + "…[truncated]"
    if isinstance(value, dict):
        items = list(value.items())[:_MAX_COLLECTION]
        out = {
            str(item_key): redact_runtime_value(item_value, key=str(item_key), depth=depth + 1)
            for item_key, item_value in items
        }
        if len(value) > _MAX_COLLECTION:
            out["_truncated_items"] = len(value) - _MAX_COLLECTION
        return out
    if isinstance(value, (list, tuple, set)):
        values = list(value)
        out = [redact_runtime_value(item, depth=depth + 1) for item in values[:_MAX_COLLECTION]]
        if len(values) > _MAX_COLLECTION:
            out.append({"_truncated_items": len(values) - _MAX_COLLECTION})
        return out
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)[:_MAX_STRING]


def _capability_summary(event: AgentEvent, action: str) -> str:
    capability = str(
        event.data.get("capability_id") or event.data.get("tool") or "能力"
    )
    return f"{action}{capability}"


def event_phase(event_type: str) -> str | None:
    domain = event_type.split(".", 1)[0]
    return {
        "run": "execution",
        "phase": "execution",
        "plan": "planning",
        "decision": "planning",
        "model": "thinking",
        "message": "responding",
        "capability": "acting",
        "task": "delegating",
        "subagent": "delegating",
        "permission": "waiting",
        "user_input": "waiting",
        "verification": "verifying",
        "artifact": "finalizing",
        "aggregation": "finalizing",
        "lease": "recovering",
    }.get(domain)


def event_summary(event: AgentEvent) -> str | None:
    """Render a factual, compact summary from the event itself."""

    explicit = event.data.get("summary") or event.data.get("progress_note")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()[:500]
    if event.type in {"run.claimed", "task.started"}:
        queue_wait_ms = event.data.get("queue_wait_ms")
        wake_source = event.data.get("wake_source")
        if isinstance(queue_wait_ms, int):
            label = "执行节点已领取任务" if event.type == "run.claimed" else "子任务开始执行"
            source = {"pg_notify": "通知唤醒", "poll": "轮询恢复", "recovery": "恢复扫描", "local": "本地调度"}.get(str(wake_source), str(wake_source or "调度"))
            return f"{label}（{source} · 队列 {queue_wait_ms} ms）"
    mapping = {
        "run.accepted": "请求已接受",
        "run.queued": "任务已进入执行队列",
        "run.claimed": "执行节点已领取任务",
        "run.started": "开始执行任务",
        "run.paused": "任务已暂停",
        "run.resumed": "任务已恢复",
        "run.cancelling": "正在取消任务",
        "run.completed": "任务执行完成",
        "run.failed": "任务执行失败",
        "run.cancelled": "任务已取消",
        "run.timed_out": "任务执行超时",
        "model.request.started": "正在分析并决定下一步",
        "model.thinking.started": "正在思考",
        "model.thinking.completed": "分析完成",
        "model.response.completed": "本轮分析已完成",
        "model.provider_fallback": "当前模型不可用，正在切换备用模型",
        "model.retry_scheduled": "模型请求失败，正在重试",
        "model.cache.hit": "命中模型响应缓存",
        "message.completed": "回复已生成",
        "phase.started": "进入新的执行阶段",
        "phase.progress": "执行阶段正在推进",
        "phase.completed": "执行阶段已完成",
        "plan.created": "执行计划已生成",
        "plan.updated": "执行计划已更新",
        "plan.step.started": "计划步骤开始执行",
        "plan.step.completed": "计划步骤已完成",
        "plan.step.failed": "计划步骤执行失败",
        "decision.recorded": "已记录调度决策",
        "task.queued": "子任务已创建",
        "task.started": "子任务开始执行",
        "task.completed": "子任务已完成",
        "task.failed": "子任务执行失败",
        "task.skipped": "子任务已跳过",
        "subagent.spawned": "已分派给子 Agent",
        "subagent.claimed": "子 Agent 开始处理",
        "subagent.completed": "子 Agent 已完成",
        "subagent.failed": "子 Agent 执行失败",
        "verification.started": "正在验证执行结果",
        "verification.passed": "结果验证通过",
        "verification.failed": "结果验证失败",
        "artifact.created": "执行产物已保存",
        "aggregation.started": "正在汇总多 Agent 结果",
        "aggregation.completed": "多 Agent 结果已汇总",
        "aggregation.failed": "多 Agent 结果汇总失败",
        "lease.lost": "执行节点失去任务所有权",
        "lease.takeover": "任务已由其他节点接管",
        "permission.requested": "正在等待操作授权",
        "permission.resolved": "操作授权已处理",
        "capability.permission_requested": "正在等待能力执行授权",
        "capability.permission_resolved": "能力执行授权已处理",
        "user_input.requested": "正在等待用户输入",
        "user_input.resolved": "已收到用户输入",
    }
    if event.type == "capability.requested":
        return _capability_summary(event, "准备执行：")
    if event.type == "capability.started":
        return _capability_summary(event, "正在执行：")
    if event.type == "capability.completed":
        return _capability_summary(event, "执行完成：")
    if event.type == "capability.failed":
        return _capability_summary(event, "执行失败：")
    if event.type == "capability.progress":
        return _capability_summary(event, "执行中：")
    if event.type == "task.progress":
        completed = event.data.get("completed")
        total = event.data.get("total")
        if isinstance(completed, int) and isinstance(total, int) and total > 0:
            return f"任务进度 {completed}/{total}"
    return mapping.get(event.type)


def prepare_event(event: AgentEvent) -> AgentEvent:
    """Normalize one event before it becomes part of the durable record."""

    data = redact_runtime_value(event.data)
    assert isinstance(data, dict)
    visibility = event.visibility
    if visibility not in {item.value for item in EventVisibility}:
        visibility = EventVisibility.DEBUG.value
    return replace(
        event,
        data=data,
        visibility=visibility,
        phase=event.phase or event_phase(event.type),
        summary=event.summary or event_summary(replace(event, data=data)),
    )


def public_event_dict(event: AgentEvent, *, include_debug: bool = False) -> dict[str, Any]:
    """Serialize an event for clients without leaking raw execution payloads."""

    payload = event.to_dict()
    data = dict(payload.get("data") or {})
    if not include_debug:
        hidden = [
            "args",
            "arguments",
            "reasoning_content",
            "result",
            "text",
            "tool_input",
            "tool_output",
        ]
        if not event.type.startswith("message."):
            hidden.append("content")
        for key in hidden:
            data.pop(key, None)
    payload["data"] = data
    return payload
