from types import SimpleNamespace

from joyhousebot.agent.tool_execution import (
    build_tool_execution_batches,
    resolve_tool_execution_policy,
)


def _call(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def _safe(_name: str) -> dict:
    return {
        "mode": "parallel_safe",
        "max_concurrent": 2,
        "idempotent": True,
        "side_effect": "none",
    }


def test_same_turn_read_tools_are_batched_with_global_and_capability_limits() -> None:
    batches = build_tool_execution_batches(
        [_call("search"), _call("search"), _call("profiles")],
        agent_policy={"tool_execution": {"mode": "parallel_safe", "max_parallel_calls": 2}},
        scenario_execution_policy={"tool_execution": {"mode": "parallel_safe", "max_parallel_calls": 2}},
        capability_policy_for=_safe,
    )

    assert [item.indices for item in batches] == [(0, 1), (2,)]
    assert [item.parallel for item in batches] == [True, False]


def test_write_or_unknown_capability_is_a_serial_barrier() -> None:
    def policies(name: str) -> dict:
        if name == "write":
            return {
                "mode": "parallel_safe",
                "max_concurrent": 8,
                "idempotent": True,
                "side_effect": "write",
            }
        return _safe(name)

    batches = build_tool_execution_batches(
        [_call("read-a"), _call("write"), _call("read-b"), _call("read-c")],
        agent_policy={"tool_execution": {"mode": "parallel_safe", "max_parallel_calls": 4}},
        scenario_execution_policy={},
        capability_policy_for=policies,
    )

    assert [item.indices for item in batches] == [(0,), (1,), (2, 3)]
    assert [item.parallel for item in batches] == [False, False, True]


def test_serial_agent_or_scenario_never_enables_parallel_calls() -> None:
    assert resolve_tool_execution_policy(
        {"tool_execution": {"mode": "parallel_safe", "max_parallel_calls": 8}},
        {"tool_execution": {"mode": "sequential", "max_parallel_calls": 1}},
    ) == {"mode": "sequential", "max_parallel_calls": 1}
    assert resolve_tool_execution_policy({}, {}) == {
        "mode": "sequential",
        "max_parallel_calls": 1,
    }
