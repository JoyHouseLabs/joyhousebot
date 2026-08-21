"""Administrative use cases for Knowledge embedding profiles."""

from __future__ import annotations

import asyncio
from typing import Any


class EmbeddingProfileService:
    def __init__(self, store: Any) -> None:
        self.store = store

    async def list_profiles(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.store.list_embedding_profiles)

    async def get_profile(self, profile_id: str) -> dict[str, Any] | None:
        return await asyncio.to_thread(self.store.get_embedding_profile, profile_id)

    async def save_revision(
        self,
        profile_id: str,
        *,
        name: str,
        description: str,
        configuration: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.store.save_embedding_profile_revision,
            profile_id,
            name=name,
            description=description,
            configuration=configuration,
            actor_id=actor_id,
        )

    async def publish_revision(
        self, profile_id: str, revision_id: str, *, actor_id: str
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.store.publish_embedding_profile_revision,
            profile_id,
            revision_id,
            actor_id=actor_id,
        )

    async def readiness(self) -> dict[str, Any]:
        return await asyncio.to_thread(self.store.knowledge_vector_readiness)


__all__ = ["EmbeddingProfileService"]
