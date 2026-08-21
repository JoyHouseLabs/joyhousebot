"""Self-contained schema migration chain for the scheduling domain."""

from __future__ import annotations

from typing import Any

from joyhousebot.scheduling.schema import (
    SCHEDULE_CURRENT_SCHEMA_V4_DDL,
    SCHEDULE_DDL,
    SCHEDULE_DROP_RUN_IDS_V3_DDL,
    SCHEDULE_GOVERNANCE_V6_DDL,
    SCHEDULE_INSTALLATION_V5_DDL,
    SCHEDULE_OCCURRENCE_RUNS_V2_DDL,
)


def ensure_schedule_schema(store: Any) -> None:
    """Apply the scheduling migration chain under its own advisory lock.

    The chain lives outside the core ``_migrate_all`` sequence: each version is
    recorded through the store's migration-history helpers and must stay
    immutable once published. Never edit a released DDL constant; append a new
    version instead.
    """

    with store._pool.connection() as connection:
        with connection.transaction():
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (872341911,))
            schedule_description = (
                "durable schedules, occurrences, monitor state, and delivery"
            )
            store._migration_is_recorded(
                connection,
                name="scheduling",
                version=1,
                ddl=SCHEDULE_DDL,
                description=schedule_description,
            )
            schedule_table = connection.execute(
                "SELECT to_regclass('public.schedule_occurrences') AS name"
            ).fetchone()
            # Do not replay the immutable v1 script merely because a
            # migration-history row was removed. v1 contains the legacy
            # ``run_ids`` column, which v3 deliberately drops. Repeating
            # that add/drop cycle leaks PostgreSQL attribute slots.
            schedules_table = connection.execute(
                "SELECT to_regclass('public.schedules') AS name"
            ).fetchone()
            if not (
                schedule_table
                and schedule_table["name"]
                and schedules_table
                and schedules_table["name"]
            ):
                connection.execute(SCHEDULE_DDL)
            store._record_migration(
                connection,
                name="scheduling",
                version=1,
                ddl=SCHEDULE_DDL,
                description=schedule_description,
            )
            relation_description = "normalized occurrence-to-Run submission history"
            store._migration_is_recorded(
                connection,
                name="scheduling",
                version=2,
                ddl=SCHEDULE_OCCURRENCE_RUNS_V2_DDL,
                description=relation_description,
            )
            relation_table = connection.execute(
                "SELECT to_regclass('public.schedule_occurrence_runs') AS name"
            ).fetchone()
            if not (relation_table and relation_table["name"]):
                connection.execute(SCHEDULE_OCCURRENCE_RUNS_V2_DDL)
            store._record_migration(
                connection,
                name="scheduling",
                version=2,
                ddl=SCHEDULE_OCCURRENCE_RUNS_V2_DDL,
                description=relation_description,
            )
            drop_description = "remove legacy JSON occurrence-to-Run history"
            store._migration_is_recorded(
                connection,
                name="scheduling",
                version=3,
                ddl=SCHEDULE_DROP_RUN_IDS_V3_DDL,
                description=drop_description,
            )
            legacy_column = connection.execute(
                """SELECT 1 AS present FROM information_schema.columns
                   WHERE table_schema='public'
                     AND table_name='schedule_occurrences'
                     AND column_name='run_ids'"""
            ).fetchone()
            if legacy_column:
                connection.execute(SCHEDULE_DROP_RUN_IDS_V3_DDL)
            store._record_migration(
                connection,
                name="scheduling",
                version=3,
                ddl=SCHEDULE_DROP_RUN_IDS_V3_DDL,
                description=drop_description,
            )
            current_description = "repair current schedule schema without legacy run_ids"
            current_recorded = store._migration_is_recorded(
                connection,
                name="scheduling",
                version=4,
                ddl=SCHEDULE_CURRENT_SCHEMA_V4_DDL,
                description=current_description,
            )
            if not current_recorded:
                connection.execute(SCHEDULE_CURRENT_SCHEMA_V4_DDL)
            store._record_migration(
                connection,
                name="scheduling",
                version=4,
                ddl=SCHEDULE_CURRENT_SCHEMA_V4_DDL,
                description=current_description,
            )
            installation_description = "App installation ownership for schedules"
            installation_recorded = store._migration_is_recorded(
                connection,
                name="scheduling",
                version=5,
                ddl=SCHEDULE_INSTALLATION_V5_DDL,
                description=installation_description,
            )
            if not installation_recorded:
                connection.execute(SCHEDULE_INSTALLATION_V5_DDL)
            store._record_migration(
                connection,
                name="scheduling",
                version=5,
                ddl=SCHEDULE_INSTALLATION_V5_DDL,
                description=installation_description,
            )
            governance_description = "cross-occurrence schedule budgets and circuit state"
            governance_recorded = store._migration_is_recorded(
                connection,
                name="scheduling",
                version=6,
                ddl=SCHEDULE_GOVERNANCE_V6_DDL,
                description=governance_description,
            )
            if not governance_recorded:
                connection.execute(SCHEDULE_GOVERNANCE_V6_DDL)
            store._record_migration(
                connection,
                name="scheduling",
                version=6,
                ddl=SCHEDULE_GOVERNANCE_V6_DDL,
                description=governance_description,
            )
