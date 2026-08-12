"""Runtime-store selection with PostgreSQL as the production-first path."""

from __future__ import annotations

import os
from typing import Any

from joyhousebot.storage.runtime_store import RuntimeStore


def _auto_migrate(default: bool) -> bool:
    raw = os.environ.get("JOYHOUSEBOT_AUTO_MIGRATE")
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("JOYHOUSEBOT_AUTO_MIGRATE must be true or false")


def create_runtime_store(config: Any | None = None) -> RuntimeStore:
    runtime = getattr(config, "runtime", None)
    settings = getattr(runtime, "store", None)
    database_url = str(getattr(settings, "database_url", "") or "").strip()
    database_url = (
        os.environ.get("JOYHOUSE_DATABASE_URL", "").strip()
        or os.environ.get("JOYHOUSEBOT_DATABASE_URL", "").strip()
        or database_url
    )

    if not database_url:
        raise ValueError(
            "PostgreSQL runtime store requires runtime.store.database_url "
            "or JOYHOUSE_DATABASE_URL"
        )
    from joyhousebot.storage.postgres_store import PostgresRuntimeStore

    return PostgresRuntimeStore(
        database_url,
        min_pool_size=int(getattr(settings, "pool_min_size", 1)),
        max_pool_size=int(getattr(settings, "pool_max_size", 10)),
        auto_migrate=_auto_migrate(bool(getattr(settings, "auto_migrate", True))),
        blob_directory=str(getattr(settings, "blob_directory", "") or ""),
        blob_inline_threshold_bytes=int(
            getattr(settings, "blob_inline_threshold_bytes", 65536)
        ),
        bootstrap_model=(
            config.get_bootstrap_model()
            if config is not None and callable(getattr(config, "get_bootstrap_model", None))
            else "unconfigured/model"
        ),
    )
