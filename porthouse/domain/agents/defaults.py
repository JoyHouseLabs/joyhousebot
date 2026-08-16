"""Neutral Agent definition used only for a genuinely empty catalog."""

from porthouse.domain.agents.models import AgentDefinition, AgentRevision


def default_agent_profiles(
    primary_model: str,
) -> tuple[tuple[AgentDefinition, AgentRevision], ...]:
    """Return the single non-product Agent required to boot an empty Runtime."""
    resolved_model = str(primary_model).strip()
    if not resolved_model:
        raise ValueError("default Agent bootstrap requires an exact primary model")
    return (
        (
            AgentDefinition(
                agent_id="default",
                name="Default Agent",
                description="Neutral execution entrypoint for an empty Porthouse Runtime",
                # The built-in profile is the direct execution boundary used
                # by products before they install their own Agents. Team and
                # Scenario execution already freeze their own coordinator, so
                # making this neutral profile a coordinator would add an
                # unrelated structured planning turn to every explicit Agent
                # request.
                role="executor",
                is_default=True,
            ),
            AgentRevision(
                revision_id="default:v1",
                agent_id="default",
                version=1,
                instructions=(
                    "Execute the requested task using only explicitly published and authorized "
                    "capabilities. Ask for required information when execution is ambiguous."
                ),
                model_policy={
                    "primary": resolved_model,
                    "fallbacks": [],
                    "temperature": 0.3,
                    "max_tokens": 4096,
                    "max_tool_iterations": 20,
                    "capture_reasoning": False,
                    "reasoning_effort": "none",
                    "cache_enabled": True,
                    "cache_ttl_seconds": 300,
                },
                planning_policy={"max_steps": 32, "max_fan_out": 10, "max_replans": 2},
                capability_policy={"mode": "catalog", "permissions": []},
                memory_policy={
                    "enabled": False,
                    "mode": "task_only",
                    "scope": "user_agent",
                    "read_mode": "none",
                    "write_mode": "none",
                },
                status="published",
            ),
        ),
    )
