from joyhousebot.domain.memory_policy import EffectiveMemoryPolicy


def test_task_only_policy_blocks_persistent_memory() -> None:
    policy = EffectiveMemoryPolicy.from_dict(
        {
            "enabled": False,
            "mode": "task_only",
            "write_mode": "none",
            "layers": {
                "working": {"read": True, "write": False},
                "profile": {"read": False, "write": False},
                "long_term": {"read": False, "write": False},
                "episodic": {"read": False, "write": False},
            },
        }
    )
    assert not policy.can_read_context
    assert not policy.allows_path("MEMORY.md", "read")
    assert not policy.allows_path("HISTORY.md", "read")
    assert not policy.can_consolidate


def test_personalized_candidate_policy_separates_layers() -> None:
    policy = EffectiveMemoryPolicy.from_dict(
        {
            "enabled": True,
            "mode": "personalized",
            "write_mode": "candidate",
            "layers": {
                "profile": {"read": True, "write": True},
                "long_term": {"read": True, "write": True},
                "episodic": {"read": False, "write": False},
                "agent": {"read": False, "write": False},
            },
        }
    )
    assert policy.can_read_context
    assert policy.can_consolidate
    assert policy.allows_path("PROFILE.md", "read")
    assert policy.allows_path("MEMORY.md", "read")
    assert not policy.allows_path("HISTORY.md", "read")
    assert not policy.allows_path("MEMORY.md", "write", direct=True)
    assert policy.allows_path("MEMORY.md", "write")


def test_empty_or_removed_shorthand_policy_fails_closed() -> None:
    policy = EffectiveMemoryPolicy.from_dict({"read": True, "write": True})
    assert not policy.can_read_context
    assert not policy.can_read_tools
    assert policy.write_mode == "none"


def test_tool_only_policy_does_not_inject_context_but_allows_reads() -> None:
    policy = EffectiveMemoryPolicy.from_dict(
        {
            "enabled": True,
            "read_mode": "tool_only",
            "write_mode": "none",
            "layers": {"long_term": {"read": True, "write": False}},
        }
    )
    assert not policy.can_read_context
    assert policy.can_read_tools
    assert policy.allows_path("MEMORY.md", "read")
