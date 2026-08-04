"""Idempotent database seed for product-provided prompt Skills."""

from joyhousebot.domain.capabilities import (
    CapabilityDefinition,
    CapabilityKind,
    CapabilityRef,
)

_MEMORY = """# Durable memory

- Use `memory_get` for the current user's long-term memory records.
- Use `retrieve` to search durable memory and knowledge records.
- Memory scope comes from the immutable Run context; never accept another user's scope.
- Store only facts useful across later sessions. Do not store secrets unless requested.
"""

_DECISION_CARD = """# Decision card

For evidence-based decisions or recommendations, structure the answer as:
1. Conclusion
2. Evidence with source traces
3. Risks and caveats
4. Uncertainties
5. Concrete next actions
"""

_CRON = """# Scheduling

Use the `cron` capability for reminders, recurring Agent tasks, and one-time tasks.
Resolve the user's timezone and preserve the originating `user_id` and `agent_id`.
Use ISO timestamps for one-time schedules and standard cron expressions for recurring work.
"""

# Prompt content is immutable once published. Increment this only when the
# built-in instruction catalog changes; older revisions remain replayable.
_DEFAULT_SKILL_VERSION = "1.0.3"


def default_skill_definitions() -> tuple[CapabilityDefinition, ...]:
    values = (
        ("memory", "Durable user-scoped memory and knowledge retrieval.", _MEMORY, True),
        (
            "decision-card",
            "Structured conclusions, evidence, risks, uncertainties, and next actions.",
            _DECISION_CARD,
            False,
        ),
        ("cron", "Schedule reminders and recurring tasks.", _CRON, False),
    )
    return tuple(
        CapabilityDefinition(
            ref=CapabilityRef(f"skill.{name}", _DEFAULT_SKILL_VERSION, CapabilityKind.SKILL),
            name=name,
            description=description,
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object"},
            adapter=f"prompt-skill:{name}",
            tags=("instruction",),
            execution_mode="immediate",
            expected_duration_seconds=0,
            timeout_seconds=1,
            retryable=False,
            configuration={
                "instruction_content": content,
                "always": always,
                "source": "database-seed",
            },
            configuration_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "instruction_content": {"type": "string", "minLength": 1, "maxLength": 100000},
                    "always": {"type": "boolean"},
                },
            },
        )
        for name, description, content, always in values
    )


def seed_default_skills(store: object) -> None:
    publish = getattr(store, "publish_capability")
    for definition in default_skill_definitions():
        publish(definition)
