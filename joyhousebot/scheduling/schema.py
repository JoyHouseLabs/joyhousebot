"""PostgreSQL schema owned by the scheduling repository."""

SCHEDULE_DDL = """
    CREATE TABLE IF NOT EXISTS schedules (
        schedule_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        name TEXT NOT NULL,
        agent_id TEXT,
        enabled BOOLEAN NOT NULL DEFAULT TRUE,
        schedule JSONB NOT NULL,
        payload JSONB NOT NULL,
        policy JSONB NOT NULL DEFAULT '{}'::jsonb,
        next_run_at_ms BIGINT,
        last_run_at_ms BIGINT,
        last_status TEXT,
        last_error TEXT,
        delete_after_run BOOLEAN NOT NULL DEFAULT FALSE,
        lease_owner TEXT,
        lease_until_ms BIGINT,
        lease_version BIGINT NOT NULL DEFAULT 0,
        created_at_ms BIGINT NOT NULL,
        updated_at_ms BIGINT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_schedules_due
        ON schedules(next_run_at_ms, schedule_id)
        WHERE enabled AND next_run_at_ms IS NOT NULL;
    CREATE INDEX IF NOT EXISTS ix_schedules_user
        ON schedules(user_id, updated_at_ms DESC);
    CREATE TABLE IF NOT EXISTS schedule_occurrences (
        occurrence_id TEXT PRIMARY KEY,
        schedule_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        scheduled_for_ms BIGINT NOT NULL,
        status TEXT NOT NULL,
        worker_id TEXT,
        lease_version BIGINT NOT NULL,
        run_id TEXT,
        error TEXT,
        name TEXT,
        agent_id TEXT,
        schedule JSONB NOT NULL DEFAULT '{}'::jsonb,
        payload JSONB NOT NULL DEFAULT '{}'::jsonb,
        policy JSONB NOT NULL DEFAULT '{}'::jsonb,
        attempt INTEGER NOT NULL DEFAULT 1,
        submit_attempt INTEGER NOT NULL DEFAULT 0,
        next_attempt_at_ms BIGINT,
        lease_owner TEXT,
        lease_until_ms BIGINT,
        delivery_status TEXT NOT NULL DEFAULT 'not_requested',
        delivery_outbound_id TEXT,
        delivery_error TEXT,
        delivered_at_ms BIGINT,
        delete_after_run BOOLEAN NOT NULL DEFAULT FALSE,
        run_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
        started_at_ms BIGINT NOT NULL,
        finished_at_ms BIGINT,
        UNIQUE(schedule_id, scheduled_for_ms)
    );
    CREATE INDEX IF NOT EXISTS ix_schedule_occurrences_user
        ON schedule_occurrences(user_id, started_at_ms DESC);
    ALTER TABLE schedules
        ADD COLUMN IF NOT EXISTS policy JSONB NOT NULL DEFAULT '{}'::jsonb;
    ALTER TABLE schedule_occurrences ADD COLUMN IF NOT EXISTS name TEXT;
    ALTER TABLE schedule_occurrences ADD COLUMN IF NOT EXISTS agent_id TEXT;
    ALTER TABLE schedule_occurrences
        ADD COLUMN IF NOT EXISTS schedule JSONB NOT NULL DEFAULT '{}'::jsonb;
    ALTER TABLE schedule_occurrences
        ADD COLUMN IF NOT EXISTS payload JSONB NOT NULL DEFAULT '{}'::jsonb;
    ALTER TABLE schedule_occurrences
        ADD COLUMN IF NOT EXISTS policy JSONB NOT NULL DEFAULT '{}'::jsonb;
    ALTER TABLE schedule_occurrences
        ADD COLUMN IF NOT EXISTS attempt INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE schedule_occurrences
        ADD COLUMN IF NOT EXISTS submit_attempt INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE schedule_occurrences ADD COLUMN IF NOT EXISTS next_attempt_at_ms BIGINT;
    ALTER TABLE schedule_occurrences ADD COLUMN IF NOT EXISTS lease_owner TEXT;
    ALTER TABLE schedule_occurrences ADD COLUMN IF NOT EXISTS lease_until_ms BIGINT;
    ALTER TABLE schedule_occurrences
        ADD COLUMN IF NOT EXISTS delivery_status TEXT NOT NULL DEFAULT 'not_requested';
    ALTER TABLE schedule_occurrences ADD COLUMN IF NOT EXISTS delivery_outbound_id TEXT;
    ALTER TABLE schedule_occurrences ADD COLUMN IF NOT EXISTS delivery_error TEXT;
    ALTER TABLE schedule_occurrences ADD COLUMN IF NOT EXISTS delivered_at_ms BIGINT;
    ALTER TABLE schedule_occurrences
        ADD COLUMN IF NOT EXISTS delete_after_run BOOLEAN NOT NULL DEFAULT FALSE;
    ALTER TABLE schedule_occurrences
        ADD COLUMN IF NOT EXISTS run_ids JSONB NOT NULL DEFAULT '[]'::jsonb;
    CREATE INDEX IF NOT EXISTS ix_schedule_occurrences_retry
        ON schedule_occurrences(next_attempt_at_ms, occurrence_id)
        WHERE status='retry_wait' AND next_attempt_at_ms IS NOT NULL;
    CREATE UNIQUE INDEX IF NOT EXISTS ux_schedule_occurrences_run
        ON schedule_occurrences(run_id) WHERE run_id IS NOT NULL;
    ALTER TABLE schedule_occurrences
        ADD COLUMN IF NOT EXISTS monitor_scratch_revision BIGINT;
    ALTER TABLE schedule_occurrences ADD COLUMN IF NOT EXISTS monitor_observation_hash TEXT;
    ALTER TABLE schedule_occurrences
        ADD COLUMN IF NOT EXISTS monitor_observation JSONB NOT NULL DEFAULT '{}'::jsonb;
    ALTER TABLE schedule_occurrences ADD COLUMN IF NOT EXISTS monitor_preflight_status TEXT;
    CREATE TABLE IF NOT EXISTS schedule_monitor_state (
        schedule_id TEXT PRIMARY KEY REFERENCES schedules(schedule_id) ON DELETE CASCADE,
        user_id TEXT NOT NULL,
        scratch_revision BIGINT NOT NULL DEFAULT 0,
        scratch_content TEXT NOT NULL DEFAULT '',
        observation_hash TEXT,
        observation JSONB NOT NULL DEFAULT '{}'::jsonb,
        observed_at_ms BIGINT,
        updated_at_ms BIGINT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS ix_schedule_monitor_state_user
        ON schedule_monitor_state(user_id, updated_at_ms DESC);
    CREATE TABLE IF NOT EXISTS schedule_monitor_scratch_revisions (
        schedule_id TEXT NOT NULL REFERENCES schedules(schedule_id) ON DELETE CASCADE,
        revision BIGINT NOT NULL,
        user_id TEXT NOT NULL,
        content TEXT NOT NULL,
        actor_type TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        run_id TEXT,
        action_id TEXT,
        created_at_ms BIGINT NOT NULL,
        PRIMARY KEY(schedule_id, revision)
    );
    CREATE UNIQUE INDEX IF NOT EXISTS ux_schedule_monitor_scratch_action
        ON schedule_monitor_scratch_revisions(schedule_id, action_id)
        WHERE action_id IS NOT NULL;
    CREATE INDEX IF NOT EXISTS ix_schedule_monitor_scratch_user
        ON schedule_monitor_scratch_revisions(user_id, schedule_id, revision DESC);
"""
