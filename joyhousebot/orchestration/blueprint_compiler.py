"""Compile Coordinator plans against a frozen Collaboration Blueprint.

The blueprint constrains the collaboration structure a Coordinator may emit:
which phases exist, who may participate in each, and the guardrails (review
presence, parallel width, confirmations). The Coordinator still decides
concrete steps, prompts and granularity inside that boundary.

Repairable violations feed the existing replan loop as structured feedback;
fatal violations are defense-in-depth fences that fail the Run closed with a
recorded reason (``plan_boundary_violation``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from joyhousebot.domain.agent_teams import AgentTeamRevision

_COORDINATOR_ONLY_KINDS = ("synthesize", "checkpoint")


@dataclass(frozen=True, slots=True)
class BlueprintViolation:
    """One enforceable deviation between a plan and its blueprint."""

    code: str
    message: str
    step_id: str | None = None
    phase_id: str | None = None
    repairable: bool = True


class BlueprintRepairError(ValueError):
    """Repairable violations surfaced to the Coordinator as replan feedback."""

    def __init__(self, violations: list[BlueprintViolation]) -> None:
        self.violations = violations
        super().__init__(
            "blueprint_violations: "
            + "; ".join(f"{item.code}: {item.message}" for item in violations)
        )


class PlanBoundaryViolationError(RuntimeError):
    """Fatal blueprint deviation; the Run must fail closed."""

    def __init__(self, violations: list[BlueprintViolation]) -> None:
        self.violations = violations
        super().__init__(
            "plan_boundary_violation: "
            + "; ".join(f"{item.code}: {item.message}" for item in violations)
        )


def compile_plan_against_blueprint(
    plan: dict[str, Any],
    blueprint: dict[str, Any],
    *,
    team: AgentTeamRevision,
) -> list[BlueprintViolation]:
    """Return every deviation between ``plan`` and the frozen ``blueprint``."""
    phases = [dict(item) for item in blueprint.get("phases") or []]
    guardrails = dict(blueprint.get("guardrails") or {})
    steps = [dict(item) for item in plan.get("planned_steps") or []]
    violations: list[BlueprintViolation] = _boundary_fences(steps, team)
    if violations:
        return violations
    matched = _match_steps_to_phases(steps, phases, violations)
    _check_phase_coverage(phases, matched, violations)
    _check_phase_order(phases, steps, violations)
    _check_review_independence(steps, violations)
    _check_guardrails(steps, guardrails, violations)
    return violations


def apply_blueprint_boundary(
    plan: dict[str, Any],
    blueprint: dict[str, Any],
    *,
    team: AgentTeamRevision,
) -> None:
    """Raise for plan deviations inside the planning (replan-able) loop."""
    violations = compile_plan_against_blueprint(plan, blueprint, team=team)
    if not violations:
        return
    fatal = [item for item in violations if not item.repairable]
    if fatal:
        raise PlanBoundaryViolationError(fatal)
    raise BlueprintRepairError(violations)


def enforce_final_plan_boundary(
    plan: dict[str, Any],
    blueprint: dict[str, Any],
    *,
    team: AgentTeamRevision,
) -> None:
    """Final gate before materialization: any deviation fails closed."""
    violations = compile_plan_against_blueprint(plan, blueprint, team=team)
    if violations:
        raise PlanBoundaryViolationError(violations)


def _boundary_fences(
    steps: list[dict[str, Any]], team: AgentTeamRevision
) -> list[BlueprintViolation]:
    """Defense-in-depth fences normally caught by plan normalization."""
    member_ids = {item.member_id for item in team.members}
    allowed_targets = {team.coordinator.member_id, *team.coordinator.allowed_handoffs}
    budget = team.budget_policy
    violations: list[BlueprintViolation] = []
    for step in steps:
        member_id = str(step.get("member_id") or "")
        if member_id and member_id not in member_ids:
            violations.append(
                BlueprintViolation(
                    "blueprint_boundary_violation",
                    f"step {step.get('id')} assigns unknown member {member_id}",
                    step_id=str(step.get("id") or "") or None,
                    repairable=False,
                )
            )
        elif member_id and member_id not in allowed_targets:
            violations.append(
                BlueprintViolation(
                    "blueprint_boundary_violation",
                    f"step {step.get('id')} assigns member {member_id} outside the "
                    "coordinator handoff boundary",
                    step_id=str(step.get("id") or "") or None,
                    repairable=False,
                )
            )
        if (
            step.get("kind") in _COORDINATOR_ONLY_KINDS
            and member_id != team.coordinator_member_id
        ):
            violations.append(
                BlueprintViolation(
                    "blueprint_boundary_violation",
                    f"{step.get('kind')} step {step.get('id')} must use the coordinator",
                    step_id=str(step.get("id") or "") or None,
                    repairable=False,
                )
            )
    if len(steps) > int(budget.get("max_tasks") or 32):
        violations.append(
            BlueprintViolation(
                "blueprint_budget_exceeded",
                f"plan has {len(steps)} steps above the team task budget",
                repairable=False,
            )
        )
    handoffs = sum(
        str(step.get("member_id") or "") != team.coordinator_member_id for step in steps
    )
    if handoffs > int(budget.get("max_handoffs") or 32):
        violations.append(
            BlueprintViolation(
                "blueprint_budget_exceeded",
                f"plan uses {handoffs} handoffs above the team handoff budget",
                repairable=False,
            )
        )
    if any(
        int(step.get("review_round") or 0) > int(budget.get("max_review_rounds") or 2)
        for step in steps
    ):
        violations.append(
            BlueprintViolation(
                "blueprint_budget_exceeded",
                "plan exceeds the team review-round budget",
                repairable=False,
            )
        )
    return violations


def render_stage_graph(
    blueprint: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    """Project a plan onto its blueprint phases for preview surfaces."""
    phases = [dict(item) for item in blueprint.get("phases") or []]
    steps = [dict(item) for item in plan.get("planned_steps") or []]
    matched: dict[str, list[str]] = {}
    unmatched: list[str] = []
    for step in steps:
        member_id = str(step.get("member_id") or "")
        kind = str(step.get("kind") or "produce")
        phase = next(
            (
                item
                for item in phases
                if item.get("kind") == kind and member_id in item.get("participants", [])
            ),
            None,
        )
        if phase is None:
            unmatched.append(str(step.get("id") or ""))
        else:
            matched.setdefault(str(phase["id"]), []).append(str(step.get("id") or ""))
    return {
        "phases": [
            {
                "id": str(phase["id"]),
                "kind": str(phase["kind"]),
                "participants": list(phase.get("participants") or []),
                "mode": str(phase.get("mode") or "parallel"),
                "depends_on": list(phase.get("depends_on") or []),
                "step_ids": matched.get(str(phase["id"]), []),
            }
            for phase in phases
        ],
        "unassigned_step_ids": unmatched,
    }


def _match_steps_to_phases(
    steps: list[dict[str, Any]],
    phases: list[dict[str, Any]],
    violations: list[BlueprintViolation],
) -> dict[str, list[dict[str, Any]]]:
    """Map each step to the phase whose kind and participants contain it."""
    matched: dict[str, list[dict[str, Any]]] = {}
    for step in steps:
        member_id = str(step.get("member_id") or "")
        kind = str(step.get("kind") or "produce")
        phase = next(
            (
                item
                for item in phases
                if item.get("kind") == kind and member_id in item.get("participants", [])
            ),
            None,
        )
        if phase is None:
            violations.append(
                BlueprintViolation(
                    "blueprint_step_member_not_in_phase",
                    f"step {step.get('id')} (kind {kind}, member {member_id or 'unassigned'}) "
                    "does not fit any blueprint phase; assign it to a phase whose "
                    "participants include that member",
                    step_id=str(step.get("id") or "") or None,
                )
            )
            continue
        matched.setdefault(str(phase["id"]), []).append(step)
    return matched


def _check_phase_coverage(
    phases: list[dict[str, Any]],
    matched: dict[str, list[dict[str, Any]]],
    violations: list[BlueprintViolation],
) -> None:
    for phase in phases:
        if not matched.get(str(phase["id"])):
            violations.append(
                BlueprintViolation(
                    "blueprint_missing_phase",
                    f"phase {phase['id']} ({phase['kind']}) has no step; every declared "
                    "phase must be covered by at least one step from its participants "
                    f"{phase.get('participants')}",
                    phase_id=str(phase["id"]),
                )
            )


def _step_closure(step: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> set[str]:
    seen: set[str] = set()
    stack = [str(dep) for dep in step.get("depends_on") or []]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(str(dep) for dep in by_id[current].get("depends_on") or [])
    return seen


def _check_phase_order(
    phases: list[dict[str, Any]],
    steps: list[dict[str, Any]],
    violations: list[BlueprintViolation],
) -> None:
    by_id = {str(step.get("id")): step for step in steps}
    if len(by_id) != len(steps):
        return  # duplicate ids are a normalization error, not an ordering one
    phase_of: dict[str, str] = {}
    for phase in phases:
        for step in steps:
            member_id = str(step.get("member_id") or "")
            if (
                step.get("kind") == phase.get("kind")
                and member_id in phase.get("participants", [])
            ):
                phase_of[str(step.get("id"))] = str(phase["id"])
    for phase in phases:
        target_steps = [
            step_id for step_id, phase_id in phase_of.items() if phase_id == str(phase["id"])
        ]
        for dep_id in phase.get("depends_on") or []:
            dep_steps = [
                step_id for step_id, phase_id in phase_of.items() if phase_id == str(dep_id)
            ]
            if not dep_steps or not target_steps:
                continue
            connected = any(
                _step_closure(by_id[target], by_id) & set(dep_steps) for target in target_steps
            )
            if not connected:
                violations.append(
                    BlueprintViolation(
                        "blueprint_phase_order_violation",
                        f"phase {phase['id']} steps must depend on at least one step of "
                        f"phase {dep_id}",
                        phase_id=str(phase["id"]),
                    )
                )


def _check_review_independence(
    steps: list[dict[str, Any]], violations: list[BlueprintViolation]
) -> None:
    by_id = {str(step.get("id")): step for step in steps}
    for step in steps:
        if step.get("kind") != "review":
            continue
        closure = _step_closure(step, by_id)
        for target_id in closure:
            target = by_id.get(target_id)
            if target is None:
                continue
            if target.get("member_id") == step.get("member_id"):
                violations.append(
                    BlueprintViolation(
                        "blueprint_review_independence",
                        f"review step {step.get('id')} reviews work by its own member "
                        f"{step.get('member_id')} (step {target_id}); reviews must be "
                        "independent",
                        step_id=str(step.get("id") or "") or None,
                    )
                )


def _check_guardrails(
    steps: list[dict[str, Any]],
    guardrails: dict[str, Any],
    violations: list[BlueprintViolation],
) -> None:
    if guardrails.get("require_review") and not any(
        step.get("kind") == "review" for step in steps
    ):
        violations.append(
            BlueprintViolation(
                "blueprint_require_review",
                "the blueprint requires an independent review step; add one",
            )
        )
    max_parallel = int(guardrails.get("max_parallel_tasks") or 4)
    width = _dag_width(steps)
    if width > max_parallel:
        violations.append(
            BlueprintViolation(
                "blueprint_parallel_budget_exceeded",
                f"plan can run {width} steps concurrently above the blueprint limit "
                f"{max_parallel}; chain or merge steps",
            )
        )


def _dag_width(steps: list[dict[str, Any]]) -> int:
    """Maximum number of steps share one dependency level (a sound lower bound
    on concurrent steps; the scheduler additionally caps real concurrency)."""
    by_id = {str(step.get("id")): step for step in steps}
    levels: dict[str, int] = {}

    def level(step_id: str) -> int:
        if step_id in levels:
            return levels[step_id]
        deps = [str(dep) for dep in by_id[step_id].get("depends_on") or [] if dep in by_id]
        result = 1 + max((level(dep) for dep in deps), default=0)
        levels[step_id] = result
        return result

    if not by_id:
        return 0
    buckets: dict[int, int] = {}
    for step_id in by_id:
        buckets[level(step_id)] = buckets.get(level(step_id), 0) + 1
    return max(buckets.values())
