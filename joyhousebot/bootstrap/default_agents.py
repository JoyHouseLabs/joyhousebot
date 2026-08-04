"""Idempotent product defaults used only to seed an empty Agent catalog."""

from joyhousebot.domain.agents import AgentDefinition, AgentRevision


def default_agent_profiles() -> tuple[tuple[AgentDefinition, AgentRevision], ...]:
    common_model = {
        # Keep the explicit gateway prefix: the model family name must never
        # cause the provider resolver to prefer a direct DeepSeek credential.
        "primary": "openrouter/deepseek/deepseek-v4-flash",
        "fallbacks": [],
        "temperature": 0.3,
        "max_tokens": 4096,
        "max_tool_iterations": 20,
        "memory_window": 50,
        # Do not opt in to OpenRouter reasoning tokens for the default fast
        # path.  Individual revisions can deliberately enable them later.
        "capture_reasoning": False,
        "reasoning_effort": "none",
        "cache_enabled": True,
        "cache_ttl_seconds": 300,
    }
    return (
        (
            AgentDefinition(
                agent_id="main-coordinator",
                name="Main Coordinator",
                description="识别意图、选择场景并编排多 Agent 工作流",
                role="coordinator",
            ),
            AgentRevision(
                revision_id="main-coordinator:v1",
                agent_id="main-coordinator",
                version=1,
                persona={"tone": "clear", "traits": ["precise", "decisive"]},
                instructions=(
                    "Classify the request, select only published platform capabilities, "
                    "ask for required information, and create an executable plan."
                ),
                model_policy=dict(common_model),
                planning_policy={"max_steps": 32, "max_fan_out": 10},
                capability_policy={"mode": "catalog"},
                memory_policy={
                    "enabled": False,
                    "mode": "task_only",
                    "scope": "user_agent",
                    "layers": {
                        "working": {"read": True, "write": False, "persist": False},
                        "session": {"read": True, "write": False, "persist": True},
                        "episodic": {"read": False, "write": False, "persist": True},
                        "profile": {"read": False, "write": False, "persist": True},
                        "long_term": {"read": False, "write": False, "persist": True},
                        "agent": {"read": False, "write": False, "persist": True},
                    },
                    "read_mode": "none",
                    "write_mode": "none",
                },
                status="published",
            ),
        ),
        (
            AgentDefinition(
                agent_id="joy",
                name="JoyAgent",
                description="通用任务执行 Agent",
                role="executor",
                is_default=True,
            ),
            AgentRevision(
                revision_id="joy:v1",
                agent_id="joy",
                version=1,
                persona={
                    "tone": "helpful",
                    "language": "follow-user",
                    "traits": ["accurate", "concise"],
                },
                instructions="Complete the assigned task and ground the answer in evidence.",
                model_policy=dict(common_model),
                planning_policy={"allow_subagents": True, "max_fan_out": 10},
                capability_policy={"mode": "catalog"},
                memory_policy={
                    "enabled": True,
                    "mode": "personalized",
                    "scope": "user_agent",
                    "layers": {
                        "working": {"read": True, "write": False, "persist": False},
                        "session": {"read": True, "write": False, "persist": True},
                        "episodic": {"read": True, "write": True, "persist": True},
                        "profile": {"read": True, "write": True, "persist": True},
                        "long_term": {"read": True, "write": True, "persist": True},
                        "agent": {"read": False, "write": False, "persist": True},
                    },
                    "read_mode": "auto",
                    "write_mode": "candidate",
                    "retrieval": {"top_k": 10, "max_tokens": 6000},
                },
                status="published",
            ),
        ),
    )
