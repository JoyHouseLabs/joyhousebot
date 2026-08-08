#!/usr/bin/env python3
"""Publish an Agent revision that opts into safe same-turn Tool concurrency.

This is an explicit operational action rather than a database migration:
existing Agent revisions keep their historical serial semantics for replay.
"""

from __future__ import annotations

import argparse

from joyhousebot.config.access import get_config
from joyhousebot.domain.agents import AgentRevision
from joyhousebot.storage.factory import create_runtime_store


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-id", default="main-coordinator")
    parser.add_argument("--max-parallel-calls", type=int, default=4)
    parser.add_argument("--actor-id", default="system:tool-parallelism")
    args = parser.parse_args()
    max_parallel = max(1, min(args.max_parallel_calls, 128))

    store = create_runtime_store(get_config())
    try:
        profile = store.get_agent_profile(args.agent_id)
        if profile is None:
            raise SystemExit(f"published Agent not found: {args.agent_id}")
        current = profile.revision
        current_policy = dict(current.model_policy)
        existing = dict(current_policy.get("tool_execution") or {})
        if (
            existing.get("mode") == "parallel_safe"
            and int(existing.get("max_parallel_calls") or 0) == max_parallel
        ):
            print(f"{args.agent_id} already uses safe tool parallelism ({max_parallel})")
            return
        revisions = store.list_agent_revisions(args.agent_id)
        version = max((item.version for item in revisions), default=0) + 1
        revision_ids = {item.revision_id for item in revisions}
        # Older deployments used an ID suffix that did not always match the
        # numeric revision version.  Preserve immutability by avoiding both
        # collision dimensions when publishing this migration revision.
        while f"{args.agent_id}:v{version}" in revision_ids:
            version += 1
        revision_id = f"{args.agent_id}:v{version}"
        current_policy["tool_execution"] = {
            "mode": "parallel_safe",
            "max_parallel_calls": max_parallel,
        }
        revision = AgentRevision(
            revision_id=revision_id,
            agent_id=current.agent_id,
            version=version,
            persona=dict(current.persona),
            instructions=current.instructions,
            model_policy=current_policy,
            planning_policy=dict(current.planning_policy),
            capability_policy=dict(current.capability_policy),
            memory_policy=dict(current.memory_policy),
            output_policy=dict(current.output_policy),
            plugin_requirements=tuple(current.plugin_requirements),
            status="draft",
            created_by=args.actor_id,
        )
        store.save_agent_revision(profile.definition, revision)
        store.publish_agent_revision(args.agent_id, revision_id, actor_id=args.actor_id)
        print(f"published {revision_id}: safe same-turn Tool concurrency={max_parallel}")
    finally:
        store.close()


if __name__ == "__main__":
    main()
