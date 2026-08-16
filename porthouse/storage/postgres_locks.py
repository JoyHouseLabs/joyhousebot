"""Cluster-wide PostgreSQL advisory lock identifiers.

Keep global lock ordering centralized.  Schema migration is always acquired
before maintenance so a new process cannot run DDL concurrently with retention
deletes from an already-started process.
"""

SCHEMA_MIGRATION_LOCK_ID = 872_341_900
RUNTIME_PURGE_LOCK_ID = 872_341_901
