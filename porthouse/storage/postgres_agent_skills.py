"""Exact Skill bindings owned by immutable Agent revisions."""

from __future__ import annotations

from typing import Any

from porthouse.storage.json_codec import Jsonb


class PostgresAgentSkillStoreMixin:
    def bind_agent_skill(
        self,
        *,
        agent_revision_id: str,
        skill_id: str,
        skill_version: str,
        activation_mode: str = "coordinator_selected",
        priority: int = 100,
        configuration: dict[str, Any] | None = None,
    ) -> None:
        if activation_mode not in {"always", "coordinator_selected", "scenario_required"}:
            raise ValueError("invalid Skill activation mode")
        with self._pool.connection() as conn, conn.transaction():
            revision = conn.execute(
                "SELECT status FROM agent_revisions WHERE revision_id=%s", (agent_revision_id,)
            ).fetchone()
            skill = conn.execute(
                """SELECT v.content_sha256 FROM skill_versions v
                   JOIN skill_definitions d USING(skill_id)
                   WHERE v.skill_id=%s AND v.version=%s
                     AND v.status IN ('published','retired')
                     AND d.status='active'""",
                (skill_id, skill_version),
            ).fetchone()
            if revision is None or skill is None:
                raise ValueError("Agent revision or published Skill version not found")
            if revision["status"] != "draft":
                raise ValueError("Skill bindings can only modify draft Agent revisions")
            conn.execute(
                """INSERT INTO agent_skill_bindings
                       (agent_revision_id,skill_id,skill_version,activation_mode,priority,
                        configuration,skill_content_sha256) VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(agent_revision_id,skill_id,skill_version) DO UPDATE SET
                       activation_mode=excluded.activation_mode,priority=excluded.priority,
                       configuration=excluded.configuration,
                       skill_content_sha256=excluded.skill_content_sha256""",
                (
                    agent_revision_id,
                    skill_id,
                    skill_version,
                    activation_mode,
                    priority,
                    Jsonb(configuration or {}),
                    str(skill["content_sha256"]),
                ),
            )

    def unbind_agent_skill(
        self, *, agent_revision_id: str, skill_id: str, skill_version: str
    ) -> bool:
        with self._pool.connection() as conn, conn.transaction():
            revision = conn.execute(
                "SELECT status FROM agent_revisions WHERE revision_id=%s",
                (agent_revision_id,),
            ).fetchone()
            if revision is None:
                return False
            if revision["status"] != "draft":
                raise ValueError("Skill bindings can only modify draft Agent revisions")
            changed = conn.execute(
                """DELETE FROM agent_skill_bindings
                   WHERE agent_revision_id=%s AND skill_id=%s AND skill_version=%s""",
                (agent_revision_id, skill_id, skill_version),
            ).rowcount
        return bool(changed)

    def list_agent_skill_bindings(self, agent_revision_id: str) -> list[dict[str, Any]]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                """SELECT * FROM agent_skill_bindings WHERE agent_revision_id=%s
                   ORDER BY priority,skill_id""",
                (agent_revision_id,),
            ).fetchall()
        return [
            {
                "agent_revision_id": row["agent_revision_id"],
                "skill_id": row["skill_id"],
                "skill_version": row["skill_version"],
                "activation_mode": row["activation_mode"],
                "priority": int(row["priority"]),
                "configuration": dict(row["configuration"]),
                "content_sha256": str(row["skill_content_sha256"]),
            }
            for row in rows
        ]
