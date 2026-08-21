"""Worker-side exact-build acknowledgement for non-Agent extensions."""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger


class ExtensionRolloutWatcher:
    def __init__(self, *, store: Any, runtime: Any) -> None:
        self.store = store
        self.runtime = runtime

    async def refresh_pending(self) -> int:
        pending = await asyncio.to_thread(
            self.store.list_pending_configuration_revisions,
            self.runtime.worker_id,
        )
        loaded = {
            (
                str(item.get("extension_id") or ""),
                str(item.get("version") or ""),
                str(item.get("build_digest") or ""),
            )
            for item in self.runtime.extension_releases
        }
        acknowledged = 0
        for item in pending:
            if item["aggregate_type"] != "extension":
                continue
            try:
                release = await asyncio.to_thread(
                    self.store.get_extension_release,
                    item["aggregate_id"],
                    item["revision_id"],
                )
                if release is None:
                    raise RuntimeError("staged extension release is unavailable")
                expected = (
                    str(release["extension_id"]),
                    str(release["version"]),
                    str(release["build_digest"]),
                )
                if expected not in loaded:
                    raise RuntimeError(
                        "extension is not loaded with the exact staged build: "
                        f"{expected[0]}@{expected[1]}"
                    )
                await asyncio.to_thread(
                    self.store.acknowledge_configuration_revision,
                    worker_id=self.runtime.worker_id,
                    aggregate_type="extension",
                    aggregate_id=item["aggregate_id"],
                    revision_id=item["revision_id"],
                    status="loaded",
                )
                acknowledged += 1
            except Exception as exc:
                logger.exception(
                    "failed to acknowledge extension revision={} worker={}",
                    item["revision_id"],
                    self.runtime.worker_id,
                )
                await asyncio.to_thread(
                    self.store.acknowledge_configuration_revision,
                    worker_id=self.runtime.worker_id,
                    aggregate_type="extension",
                    aggregate_id=item["aggregate_id"],
                    revision_id=item["revision_id"],
                    status="failed",
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
        return acknowledged

    async def watch(self, *, poll_interval: float = 1.0) -> None:
        while True:
            await self.refresh_pending()
            await asyncio.sleep(max(0.1, float(poll_interval)))


__all__ = ["ExtensionRolloutWatcher"]
