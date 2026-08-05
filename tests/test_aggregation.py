from datetime import datetime, timezone

from joyhousebot.orchestration.aggregation import (
    aggregate_task_results,
    normalize_aggregation_policy,
    synthesis_prompt,
)


def _task(task_id: str, content: str, *, status: str = "completed") -> dict:
    return {
        "task_id": task_id,
        "spec_id": task_id,
        "agent_id": "researcher",
        "status": status,
        "result": {"status": status, "content": content},
    }


def test_structured_merge_is_deterministic_deduplicated_and_audited() -> None:
    result = aggregate_task_results(
        [
            _task("a", '{"candidates":[{"id":"a"}],"owner":"first"}'),
            _task("b", '{"candidates":[{"id":"a"},{"id":"b"}],"owner":"second"}'),
        ],
        normalize_aggregation_policy({"mode": "structured_merge"}),
    )

    assert result.structured_output == {
        "candidates": [{"id": "a"}, {"id": "b"}],
        "owner": "first",
    }
    assert result.audit["conflicts"] == [
        {
            "path": "owner",
            "task_id": "b",
            "resolution": "prefer_first",
            "chosen": "first",
        }
    ]


def test_rank_and_evidence_policies_preserve_provenance() -> None:
    tasks = [
        _task("low", '{"score": 0.2, "name": "low"}'),
        _task("high", '{"score": 0.9, "name": "high"}'),
    ]
    ranked = aggregate_task_results(
        tasks,
        normalize_aggregation_policy({"mode": "rank_and_select", "max_items": 1}),
    )
    assert ranked.content == '{"score": 0.9, "name": "high"}'
    assert ranked.structured_output["best"]["task_id"] == "high"
    assert ranked.audit["discarded"] == ["low"]

    evidence = aggregate_task_results(
        tasks + [_task("bad", "ignored", status="failed")],
        normalize_aggregation_policy({"mode": "evidence_merge"}),
    )
    assert [item["task_id"] for item in evidence.structured_output["evidence"]] == ["low", "high"]
    assert evidence.audit["source_task_ids"] == ["low", "high"]


def test_policy_rejects_unknown_modes() -> None:
    try:
        normalize_aggregation_policy({"mode": "magic"})
    except ValueError as error:
        assert "unsupported aggregation" in str(error)
    else:
        raise AssertionError("invalid policy should fail")


def test_dynamic_datetime_evidence_is_serializable() -> None:
    task = _task("dynamic", "")
    task["result"] = {
        "status": "completed",
        "content": "",
        "capability_result": {
            "data": {"observed_at": datetime(2026, 8, 5, tzinfo=timezone.utc)},
        },
    }
    policy = normalize_aggregation_policy({"mode": "structured_merge"})
    result = aggregate_task_results([task], policy)
    assert result.structured_output["observed_at"].isoformat() == "2026-08-05T00:00:00+00:00"
    assert "2026-08-05" in result.content
    assert "2026-08-05" in synthesis_prompt(goal="test", tasks=[task], policy=policy)
