"""Versioned collaboration blueprints constraining AgentTeam coordination.

A blueprint is a versioned field of an ``AgentTeamRevision``. It declares the
collaboration structure (phases, participants, dependencies) and the guardrails
a Coordinator plan must respect. It constrains, but never replaces, the
per-Run plan: the Coordinator still decides concrete steps, prompts and
granularity inside the frozen boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

BLUEPRINT_SCHEMA_VERSION = 1
PHASE_KINDS = ("produce", "review", "revise", "synthesize", "checkpoint")
PHASE_MODES = ("parallel", "sequential")
_GUARDRAIL_KEYS = (
    "max_parallel_tasks",
    "require_review",
    "require_plan_confirmation",
    "require_final_confirmation",
)
_TOP_LEVEL_KEYS = {"schema_version", "preset", "phases", "guardrails", "role_bindings"}
_PHASE_KEYS = {"id", "kind", "participants", "mode", "depends_on"}
_PHASE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


@dataclass(frozen=True, slots=True)
class PresetSpec:
    """One explainable collaboration preset offered by Team Composer."""

    preset: str
    label: str
    guidance: str
    # Ordered (phase_id, phase_kind) template; derivation wires linear deps.
    phase_template: tuple[tuple[str, str], ...]
    bindings: tuple[str, ...]


PRESET_REGISTRY: dict[str, PresetSpec] = {
    "parallel_synthesize": PresetSpec(
        preset="parallel_synthesize",
        label="并行产出 · 汇总",
        guidance="多个专家并行产出独立视角，Coordinator 汇总。适合互相独立、无需交叉修改的分工。",
        phase_template=(("produce", "produce"), ("synthesize", "synthesize")),
        bindings=("producers",),
    ),
    "parallel_review_revise_synthesize": PresetSpec(
        preset="parallel_review_revise_synthesize",
        label="并行产出 · 复核 · 修订 · 汇总",
        guidance="专家并行产出，独立复核者审查问题，作者修订后由 Coordinator 汇总。适合质量敏感的成果。",
        phase_template=(
            ("produce", "produce"),
            ("review", "review"),
            ("revise", "revise"),
            ("synthesize", "synthesize"),
        ),
        bindings=("producers", "reviewers"),
    ),
    "sequential_handoff": PresetSpec(
        preset="sequential_handoff",
        label="顺序交接",
        guidance="按链条顺序推进：上一步产物是下一角色的输入，最后由 Coordinator 收束。适合阶段依赖明确的流程。",
        phase_template=(("handoff", "produce"), ("synthesize", "synthesize")),
        bindings=("chain",),
    ),
    "research_challenge_decide": PresetSpec(
        preset="research_challenge_decide",
        label="研究 · 挑战 · 决策",
        guidance="先并行研究，再由挑战者做反方论证与风险检查，Coordinator 给出决策建议。",
        phase_template=(
            ("research", "produce"),
            ("challenge", "review"),
            ("decide", "synthesize"),
        ),
        bindings=("producers", "challengers"),
    ),
    "monitor_diagnose_execute_verify": PresetSpec(
        preset="monitor_diagnose_execute_verify",
        label="监控 · 诊断 · 受控执行 · 验证",
        guidance="运维闭环：监控采集、诊断定位、受控执行与验证复核，Coordinator 汇总结论。",
        phase_template=(
            ("monitor", "produce"),
            ("diagnose", "produce"),
            ("execute", "produce"),
            ("verify", "review"),
            ("synthesize", "synthesize"),
        ),
        bindings=("monitors", "diagnosticians", "executors", "verifiers"),
    ),
}


def preset_summaries() -> list[dict[str, Any]]:
    """Serializable preset catalog for the Composer preset cards."""
    return [
        {
            "preset": spec.preset,
            "label": spec.label,
            "guidance": spec.guidance,
            "phase_template": [
                {"id": phase_id, "kind": kind} for phase_id, kind in spec.phase_template
            ],
            "bindings": list(spec.bindings),
        }
        for spec in PRESET_REGISTRY.values()
    ]


def derive_preset_phases(
    preset: str,
    *,
    role_bindings: dict[str, list[str]] | None,
    member_ids: list[str],
    coordinator_member_id: str,
) -> list[dict[str, Any]]:
    """Deterministically derive canonical phases from a preset and bindings.

    ``sequential_handoff`` expands one produce phase per chained member, in
    order; every other preset maps its template one phase at a time with linear
    dependencies. Unassigned producers default to all non-coordinator members;
    reviewer-style bindings default to non-producer members.
    """
    spec = PRESET_REGISTRY[preset]
    bindings = {str(key): [str(item) for item in value] for key, value in (role_bindings or {}).items()}
    non_coordinators = [item for item in member_ids if item != coordinator_member_id]

    def binding(name: str, *, default: list[str]) -> list[str]:
        value = bindings.get(name)
        if value:
            return list(dict.fromkeys(value))
        return list(default)

    phases: list[tuple[str, str, list[str]]] = []
    if preset == "sequential_handoff":
        chain = binding("chain", default=non_coordinators)
        for index, member_id in enumerate(chain, start=1):
            phases.append((f"handoff_{index}", "produce", [member_id]))
        phases.append(("synthesize", "synthesize", [coordinator_member_id]))
    else:
        producers = binding("producers", default=non_coordinators)
        reviewer_pool = [item for item in non_coordinators if item not in producers]
        if not reviewer_pool:
            reviewer_pool = [coordinator_member_id]
        for phase_id, kind in spec.phase_template:
            if kind in ("produce", "revise"):
                participants = producers
                if kind == "produce" and preset == "monitor_diagnose_execute_verify":
                    slot = {
                        "monitor": "monitors",
                        "diagnose": "diagnosticians",
                        "execute": "executors",
                    }[phase_id]
                    participants = binding(slot, default=producers)
            elif kind == "review":
                slot = next(
                    (name for name in spec.bindings if name not in ("producers", "chain")),
                    "reviewers",
                )
                participants = binding(slot, default=reviewer_pool)
            else:
                participants = [coordinator_member_id]
            phases.append((phase_id, kind, participants))

    result: list[dict[str, Any]] = []
    for index, (phase_id, kind, participants) in enumerate(phases):
        result.append(
            {
                "id": phase_id,
                "kind": kind,
                "participants": participants,
                "mode": "parallel" if len(participants) > 1 else "sequential",
                "depends_on": [phases[index - 1][0]] if index else [],
            }
        )
    return result


def default_blueprint(
    *,
    member_ids: list[str],
    coordinator_member_id: str,
    budget_policy: dict[str, Any],
) -> dict[str, Any]:
    """The implicit blueprint legacy teams resolve to: parallel_synthesize."""
    return normalize_collaboration_blueprint(
        {
            "schema_version": BLUEPRINT_SCHEMA_VERSION,
            "preset": "parallel_synthesize",
            "phases": derive_preset_phases(
                "parallel_synthesize",
                role_bindings=None,
                member_ids=member_ids,
                coordinator_member_id=coordinator_member_id,
            ),
            "guardrails": {"max_parallel_tasks": budget_policy.get("max_parallel_tasks", 4)},
        },
        member_ids=set(member_ids),
        coordinator_member_id=coordinator_member_id,
        budget_policy=budget_policy,
    )


def resolve_effective_blueprint(
    *,
    collaboration_blueprint: dict[str, Any] | None,
    member_ids: list[str],
    coordinator_member_id: str,
    budget_policy: dict[str, Any],
) -> dict[str, Any]:
    """Explicit blueprint when present, else the implicit default.

    The ``origin`` marker distinguishes the two for Console display; it is
    never persisted inside the revision definition.
    """
    if collaboration_blueprint is not None:
        return {**collaboration_blueprint, "origin": "explicit"}
    return {
        **default_blueprint(
            member_ids=member_ids,
            coordinator_member_id=coordinator_member_id,
            budget_policy=budget_policy,
        ),
        "origin": "implicit_default",
    }


def frozen_enforced_blueprint(value: dict[str, Any] | None) -> dict[str, Any]:
    """Return the blueprint a Run must enforce from frozen run metadata.

    Implicit defaults describe how a legacy team currently collaborates but do
    not constrain the Coordinator: publishing an explicit blueprint (including
    via the documented migration) is what makes the structure binding. This
    keeps existing team behavior unchanged until the owner opts in.
    """
    if not isinstance(value, dict) or value.get("origin") != "explicit":
        return {}
    return value


def normalize_collaboration_blueprint(
    value: dict[str, Any] | None,
    *,
    member_ids: set[str],
    coordinator_member_id: str,
    budget_policy: dict[str, Any],
) -> dict[str, Any] | None:
    """Validate and canonicalize a blueprint; ``None`` stays ``None``.

    Accepts either the derived input form (``preset`` + optional
    ``role_bindings``, no ``phases``) or the full canonical form. Raises
    ``ValueError`` with a ``blueprint_*`` error code as the message prefix.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("blueprint_invalid: collaboration blueprint must be an object")
    if unknown := set(value) - _TOP_LEVEL_KEYS:
        raise ValueError(f"blueprint_unknown_fields: unknown keys {sorted(unknown)}")
    version = int(value.get("schema_version") or BLUEPRINT_SCHEMA_VERSION)
    if version != BLUEPRINT_SCHEMA_VERSION:
        raise ValueError("blueprint_unsupported_schema_version")
    preset = str(value.get("preset") or "")
    if preset not in PRESET_REGISTRY:
        raise ValueError("blueprint_unknown_preset")
    role_bindings = value.get("role_bindings")
    if role_bindings is not None and not isinstance(role_bindings, dict):
        raise ValueError("blueprint_invalid: role_bindings must be an object")
    ordered_members = list(member_ids)
    phases = value.get("phases")
    if not phases:
        phases = derive_preset_phases(
            preset,
            role_bindings=role_bindings,
            member_ids=ordered_members,
            coordinator_member_id=coordinator_member_id,
        )
    normalized_phases = _normalize_phases(
        phases,
        member_ids=member_ids,
        coordinator_member_id=coordinator_member_id,
    )
    _validate_phase_structure(normalized_phases, preset=preset)
    guardrails = _normalize_guardrails(
        value.get("guardrails"),
        phases=normalized_phases,
        budget_policy=budget_policy,
    )
    return {
        "schema_version": BLUEPRINT_SCHEMA_VERSION,
        "preset": preset,
        "phases": normalized_phases,
        "guardrails": guardrails,
    }


def _normalize_phases(
    phases: Any,
    *,
    member_ids: set[str],
    coordinator_member_id: str,
) -> list[dict[str, Any]]:
    if not isinstance(phases, list) or not 2 <= len(phases) <= 16:
        raise ValueError("blueprint_invalid_phases: phases must be a list of 2 to 16 entries")
    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in phases:
        if not isinstance(item, dict):
            raise ValueError("blueprint_invalid_phase: phase must be an object")
        if unknown := set(item) - _PHASE_KEYS:
            raise ValueError(f"blueprint_invalid_phase: unknown keys {sorted(unknown)}")
        phase_id = str(item.get("id") or "")
        if not _PHASE_ID.fullmatch(phase_id) or phase_id in seen:
            raise ValueError("blueprint_invalid_phase: phase id must be unique and stable")
        seen.add(phase_id)
        kind = str(item.get("kind") or "")
        if kind not in PHASE_KINDS:
            raise ValueError("blueprint_invalid_phase_kind")
        mode = str(item.get("mode") or "parallel")
        if mode not in PHASE_MODES:
            raise ValueError("blueprint_invalid_phase_mode")
        participants = [str(member) for member in item.get("participants") or ()]
        if not participants or len(participants) != len(set(participants)):
            raise ValueError("blueprint_invalid_phase: participants must be non-empty and unique")
        unknown_members = set(participants) - member_ids
        if unknown_members:
            raise ValueError(
                f"blueprint_unknown_participant: {sorted(unknown_members)}"
            )
        if kind in ("synthesize", "checkpoint") and participants != [coordinator_member_id]:
            raise ValueError("blueprint_coordinator_only_phase")
        depends_on = [str(dep) for dep in item.get("depends_on") or ()]
        if len(depends_on) != len(set(depends_on)):
            raise ValueError("blueprint_invalid_phase: duplicate depends_on entries")
        result.append(
            {
                "id": phase_id,
                "kind": kind,
                "participants": participants,
                "mode": mode,
                "depends_on": depends_on,
            }
        )
    for index, phase in enumerate(result):
        for dep in phase["depends_on"]:
            if dep == phase["id"] or dep not in {item["id"] for item in result[:index]}:
                raise ValueError("blueprint_invalid_dependency")
    return result


def _dependency_closure(phases: list[dict[str, Any]], phase: dict[str, Any]) -> set[str]:
    by_id = {item["id"]: item for item in phases}
    seen: set[str] = set()
    stack = list(phase["depends_on"])
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(by_id[current]["depends_on"])
    return seen


def _validate_phase_structure(phases: list[dict[str, Any]], *, preset: str) -> None:
    synthesize = [item for item in phases if item["kind"] == "synthesize"]
    if not synthesize:
        raise ValueError("blueprint_missing_synthesize")
    if len(synthesize) != 1 or phases[-1]["id"] != synthesize[0]["id"]:
        raise ValueError("blueprint_synthesize_not_final")
    by_id = {item["id"]: item for item in phases}
    depended = {dep for item in phases for dep in item["depends_on"]}
    if synthesize[0]["id"] in depended:
        raise ValueError("blueprint_synthesize_not_final")
    for phase in phases:
        if phase["kind"] != "review":
            continue
        closure = _dependency_closure(phases, phase)
        producers = {
            member
            for dep in closure
            for member in by_id[dep]["participants"]
        }
        if overlap := set(phase["participants"]) & producers:
            raise ValueError(
                f"blueprint_review_independence: {sorted(overlap)} cannot review own output"
            )
    template_kinds = {kind for _, kind in PRESET_REGISTRY[preset].phase_template}
    present_kinds = {item["kind"] for item in phases}
    if missing := template_kinds - present_kinds:
        raise ValueError(f"blueprint_preset_kind_missing: {sorted(missing)}")


def _normalize_guardrails(
    value: dict[str, Any] | None,
    *,
    phases: list[dict[str, Any]],
    budget_policy: dict[str, Any],
) -> dict[str, Any]:
    source = dict(value or {})
    if unknown := set(source) - set(_GUARDRAIL_KEYS):
        raise ValueError(f"blueprint_invalid_guardrails: unknown keys {sorted(unknown)}")
    budget_cap = int(budget_policy.get("max_parallel_tasks") or 4)
    max_parallel = source.get("max_parallel_tasks")
    if max_parallel is None:
        max_parallel = budget_cap
    if not isinstance(max_parallel, int) or isinstance(max_parallel, bool) or not 1 <= max_parallel <= 32:
        raise ValueError("blueprint_invalid_guardrails: max_parallel_tasks must be an integer 1-32")
    if max_parallel > budget_cap:
        raise ValueError("blueprint_parallel_exceeds_budget")
    result: dict[str, Any] = {"max_parallel_tasks": max_parallel}
    for key in ("require_review", "require_plan_confirmation", "require_final_confirmation"):
        flag = source.get(key)
        if flag is None:
            flag = key == "require_review" and any(item["kind"] == "review" for item in phases)
        if not isinstance(flag, bool):
            raise ValueError(f"blueprint_invalid_guardrails: {key} must be a boolean")
        result[key] = flag
    if result["require_review"] and not any(item["kind"] == "review" for item in phases):
        raise ValueError("blueprint_review_required_missing")
    return result
