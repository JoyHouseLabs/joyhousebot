from types import SimpleNamespace

from joyhousebot.application.dinq_projection import build_dinq_projection


def test_dinq_projection_merges_candidates_and_profiles() -> None:
    run = SimpleNamespace(
        run_id="run-1",
        user_id="user-1",
        session_id="ui:search",
        agent_id="main-coordinator",
        status="completed",
        prompt="找强化学习研究人员",
        options={},
        current_phase="execution",
        next_action=None,
        status_summary="完成搜索",
    )
    artifacts = [
        {
            "name": "dinq.candidates.collection",
            "content": {"candidates": [{"id": "github:ada", "name": "Ada", "score": 0.92, "match_reasons": ["RL"]}]},
        },
        {
            "name": "dinq.candidate.profile",
            "content": {"candidate_id": "github:ada", "profile": {"company": "Analytical Engines"}, "enrichment_status": "verified"},
        },
    ]
    projection = build_dinq_projection(
        run=run,
        artifacts=artifacts,
        events=[SimpleNamespace(sequence=2, event_id="e2", type="run.completed", phase="execution", status="completed", summary="完成", data={}, created_at="now")],
        invocations=[object()],
    )
    assert projection["search"]["total_candidates"] == 1
    assert projection["search"]["verified_candidates"] == 1
    assert projection["search"]["tool_calls"] == 1
    candidate = projection["candidates"][0]
    assert candidate["candidate_id"] == "github:ada"
    assert candidate["profile"]["company"] == "Analytical Engines"
    assert projection["activity"][0]["type"] == "run.completed"


def test_dinq_projection_is_empty_for_generic_run() -> None:
    run = {"run_id": "run-2", "status": "queued", "prompt": "hello", "options": {}}
    projection = build_dinq_projection(run=run, artifacts=[], events=[], invocations=[])
    assert projection["candidates"] == []
    assert projection["selected_candidate"] is None
