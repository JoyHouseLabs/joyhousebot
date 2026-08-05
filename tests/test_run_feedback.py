"""Durable Run-level human feedback tests."""

from __future__ import annotations

from tests.support.postgres_store import PostgresTestStore


def test_run_feedback_is_scoped_and_round_trips(tmp_path) -> None:
    store = PostgresTestStore(tmp_path / "run-feedback.db")
    run, created = store.create_runtime_run(
        run_id="run_feedback_test",
        user_id="user_a",
        session_id="session_a",
        agent_id="joy",
        kind="agent",
        prompt="test output",
        options={},
    )
    assert created and run.run_id == "run_feedback_test"

    saved = store.create_run_feedback(
        run_id=run.run_id,
        user_id="user_a",
        agent_id=run.agent_id,
        session_id=run.session_id,
        agent_revision_id="joy:v1",
        feedback_type="missing_data",
        rating="negative",
        comment="需要补充候选人的工作年限。",
        output_excerpt="候选人列表……",
        metadata={"source": "test"},
    )
    assert saved.feedback_type == "missing_data"
    assert saved.agent_revision_id == "joy:v1"
    assert saved.metadata == {"source": "test"}
    assert len(store.list_run_feedback(run.run_id, user_id="user_a")) == 1
    assert store.list_run_feedback(run.run_id, user_id="other") == []
    store.close()
