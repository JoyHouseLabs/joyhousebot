"""Prometheus text exposition for bounded runtime metrics."""

from __future__ import annotations

from typing import Any


def _label(value: object) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _status_family(
    lines: list[str], name: str, values: dict[str, int], *, help_text: str
) -> None:
    lines.extend([f"# HELP {name} {help_text}", f"# TYPE {name} gauge"])
    for status, count in values.items():
        lines.append(f'{name}{{status="{_label(status)}"}} {int(count)}')


def render_prometheus(data: dict[str, Any]) -> str:
    lines = [
        "# HELP joyhousebot_up API process readiness.",
        "# TYPE joyhousebot_up gauge",
        "joyhousebot_up 1",
    ]
    for family in ("runs", "tasks", "workers"):
        _status_family(
            lines,
            f"joyhousebot_{family}_total",
            data.get(family) or {},
            help_text=f"Durable {family} grouped by status.",
        )
    for row in data.get("runs_24h") or []:
        status = _label(row["status"])
        lines.append(f'joyhousebot_runs_24h_total{{status="{status}"}} {row["count"]}')
        lines.append(
            f'joyhousebot_run_duration_ms_p95{{window="24h",status="{status}"}} '
            f'{row["p95_duration_ms"]}'
        )
    for row in data.get("providers") or []:
        labels = (
            f'provider="{_label(row["provider"])}",model="{_label(row["model"])}",'
            f'status="{_label(row["status"])}"'
        )
        lines.append(f"joyhousebot_provider_requests_total{{{labels}}} {row['count']}")
        lines.append(f"joyhousebot_provider_duration_ms_avg{{{labels}}} {row['avg_duration_ms']}")
        lines.append(f"joyhousebot_provider_ttft_ms_avg{{{labels}}} {row['avg_ttft_ms']}")
        lines.append(f"joyhousebot_provider_duration_ms_p95{{{labels}}} {row['p95_duration_ms']}")
        lines.append(f"joyhousebot_provider_ttft_ms_p95{{{labels}}} {row['p95_ttft_ms']}")
        lines.append(f"joyhousebot_provider_cost_usd_total{{{labels}}} {row['cost_usd']}")
    queue = data.get("queue") or {}
    lines.extend(
        [
            f"joyhousebot_queue_queued_tasks {int(queue.get('queued', 0))}",
            f"joyhousebot_queue_oldest_age_seconds {float(queue.get('oldest_age_seconds', 0))}",
            f"joyhousebot_queue_expired_leases_total {int(queue.get('expired_leases', 0))}",
            f"joyhousebot_queue_retried_tasks_total {int(queue.get('retried_tasks', 0))}",
            f"joyhousebot_task_claim_delay_ms_p95 {float(queue.get('claim_delay_p95_ms', 0))}",
            f"joyhousebot_workers_stale_total {int(data.get('workers_stale', 0))}",
        ]
    )
    for key, title in (
        ("actions", "Durable Actions"),
        ("approvals", "Approval requests"),
        ("reconciliations", "External operation reconciliations"),
        ("sagas", "Graph Sagas"),
        ("graph_patch_proposals", "GraphPatch proposals"),
        ("eval_runs", "Evaluation runs"),
        ("work_shares", "Work shares"),
    ):
        _status_family(
            lines,
            f"joyhousebot_{key}_total",
            data.get(key) or {},
            help_text=f"{title} grouped by status.",
        )
    lines.extend(
        [
            "# TYPE joyhousebot_approval_oldest_pending_seconds gauge",
            "joyhousebot_approval_oldest_pending_seconds "
            f"{float(data.get('approval_oldest_pending_seconds', 0))}",
            "# TYPE joyhousebot_reconciliation_oldest_active_seconds gauge",
            "joyhousebot_reconciliation_oldest_active_seconds "
            f"{float(data.get('reconciliation_oldest_active_seconds', 0))}",
        ]
    )
    for row in data.get("verifications") or []:
        labels = (
            f'status="{_label(row["status"])}",'
            f'verifier_type="{_label(row["verifier_type"])}"'
        )
        lines.append(f"joyhousebot_verifications_total{{{labels}}} {row['count']}")
    for row in data.get("works") or []:
        labels = (
            f'status="{_label(row["status"])}",visibility="{_label(row["visibility"])}"'
        )
        lines.append(f"joyhousebot_works_total{{{labels}}} {row['count']}")
    for row in data.get("api_tokens") or []:
        labels = (
            f'token_type="{_label(row["token_type"])}",status="{_label(row["status"])}"'
        )
        lines.append(f"joyhousebot_api_tokens_total{{{labels}}} {row['count']}")
    for risk, count in (data.get("api_token_risks") or {}).items():
        lines.append(
            f'joyhousebot_api_token_risks_total{{risk="{_label(risk)}"}} {int(count)}'
        )
    for event_type, count in (data.get("critical_events_24h") or {}).items():
        lines.append(
            f'joyhousebot_critical_events_24h_total{{event_type="{_label(event_type)}"}} '
            f"{int(count)}"
        )
    for kind, seconds in (data.get("active_ages") or {}).items():
        lines.append(
            f'joyhousebot_oldest_active_seconds{{kind="{_label(kind)}"}} {float(seconds)}'
        )
    for row in data.get("channels") or []:
        labels = (
            f'channel="{_label(row["channel"])}",status="{_label(row["status"])}"'
        )
        lines.append(f"joyhousebot_channel_outbox_total{{{labels}}} {row['count']}")
    return "\n".join(lines) + "\n"
