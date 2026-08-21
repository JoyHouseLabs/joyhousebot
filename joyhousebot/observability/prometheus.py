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
    capacity = data.get("capacity") or {}
    lines.extend(
        [
            f"joyhousebot_workers_reporting_total {int(capacity.get('reporting_workers', 0))}",
            f"joyhousebot_agent_slots_total {int(capacity.get('agent_slots', 0))}",
            f"joyhousebot_agent_slots_active {int(capacity.get('agent_active', 0))}",
            f"joyhousebot_agent_slots_waiting {int(capacity.get('agent_waiting', 0))}",
            f"joyhousebot_graph_slots_total {int(capacity.get('graph_slots', 0))}",
            f"joyhousebot_graph_slots_active {int(capacity.get('graph_active', 0))}",
            f"joyhousebot_graph_tasks_buffered {int(capacity.get('graph_buffered', 0))}",
            f"joyhousebot_worker_process_cpu_percent_avg {float(capacity.get('worker_cpu_percent_avg', 0))}",
            f"joyhousebot_worker_process_rss_bytes {int(capacity.get('worker_rss_bytes', 0))}",
        ]
    )
    database_pool = data.get("database_pool") or {}
    lines.extend(
        [
            f"joyhousebot_database_pool_size {int(database_pool.get('size', 0))}",
            f"joyhousebot_database_pool_available {int(database_pool.get('available', 0))}",
            f"joyhousebot_database_pool_waiting {int(database_pool.get('waiting', 0))}",
            f"joyhousebot_database_pool_max_size {int(database_pool.get('max_size', 0))}",
        ]
    )
    for row in data.get("provider_errors_24h") or []:
        labels = f'provider="{_label(row["provider"])}",model="{_label(row["model"])}"'
        lines.append(f"joyhousebot_provider_failures_24h_total{{{labels}}} {int(row['failed'])}")
        lines.append(f"joyhousebot_provider_failure_rate_24h{{{labels}}} {float(row['failure_rate'])}")
    for row in data.get("team_runs") or []:
        lines.append(
            f'joyhousebot_team_runs_total{{status="{_label(row["status"])}"}} {int(row["count"])}'
        )
    for row in data.get("team_plan_actions") or []:
        lines.append(
            f'joyhousebot_team_plan_actions_total{{action="{_label(row["action"])}"}} {int(row["count"])}'
        )
    team_planning = data.get("team_planning") or {}
    if team_planning:
        lines.extend(
            [
                "# TYPE joyhousebot_team_planning_duration_seconds gauge",
                "joyhousebot_team_planning_duration_seconds"
                f'{{quantile="p95"}} {float(team_planning.get("planning_duration_seconds_p95", 0))}',
                "joyhousebot_team_planning_duration_seconds"
                f'{{quantile="avg"}} {float(team_planning.get("planning_duration_seconds_avg", 0))}',
                "joyhousebot_team_plan_confirmation_wait_seconds "
                f'{float(team_planning.get("confirmation_wait_seconds_p95", 0))}',
            ]
        )
    for row in data.get("team_tasks") or []:
        labels = f'kind="{_label(row["kind"])}",status="{_label(row["status"])}"'
        lines.append(f"joyhousebot_team_tasks_total{{{labels}}} {int(row['count'])}")
    for row in data.get("coordinator_replans") or []:
        lines.append(
            f'joyhousebot_coordinator_replans_total{{reason_code="{_label(row["reason_code"])}"}}'
            f" {int(row['count'])}"
        )
    for key, title in (
        ("actions", "Durable Actions"),
        ("approvals", "Approval requests"),
        ("reconciliations", "External operation reconciliations"),
        ("sagas", "Graph Sagas"),
        ("graph_patch_proposals", "GraphPatch proposals"),
        ("eval_runs", "Evaluation runs"),
        ("work_shares", "Work shares"),
        ("app_callback_deliveries", "App callback deliveries"),
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
            "# TYPE joyhousebot_app_callback_oldest_pending_seconds gauge",
            "joyhousebot_app_callback_oldest_pending_seconds "
            f"{float(data.get('app_callback_oldest_pending_seconds', 0))}",
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
