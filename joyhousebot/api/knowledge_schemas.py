"""Versioned transport schemas for Knowledge indexing snapshots."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

_ID_PATTERN = r"^[A-Za-z0-9_.:-]{1,128}$"


class KnowledgeAttachmentSnapshot(BaseModel):
    reference_kind: Literal["url", "runtime_input"]
    uri: str = Field(default="", max_length=2000)
    asset_id: str = Field(default="", pattern=r"^(|input_[0-9a-f]{32})$")
    display_name: str = Field(default="", max_length=500)
    media_type: str = Field(default="", max_length=200)
    content_sha256: str = Field(default="", pattern=r"^(|[0-9a-f]{64})$")
    byte_size: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def reference_is_complete(self):
        if self.reference_kind == "url" and not self.uri.strip():
            raise ValueError("url attachment requires uri")
        if self.reference_kind == "runtime_input" and not self.asset_id.strip():
            raise ValueError("runtime_input attachment requires asset_id")
        return self


class KnowledgeSourceSnapshotRequest(BaseModel):
    """Immutable Product/App snapshot accepted by the Knowledge indexing Run."""

    source_system: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    source_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    source_version: str = Field(min_length=1, max_length=128)
    source_generation: int = Field(ge=1)
    source_status: Literal["inbox", "active", "archived"] = "active"
    source_type: Literal[
        "note", "web", "file", "image", "video", "email", "capture", "paper", "report"
    ]
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(default="", max_length=250_000)
    source_url: str = Field(default="", max_length=2000)
    attachments: list[KnowledgeAttachmentSnapshot] = Field(default_factory=list, max_length=20)
    tags: list[str] = Field(default_factory=list, max_length=100)
    collection_refs: list[str] = Field(default_factory=list, max_length=100)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    index_profile_id: str = Field(default="lexical-v1", pattern=_ID_PATTERN)
    embedding_profile_id: str | None = Field(
        default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$"
    )

    @model_validator(mode="after")
    def snapshot_has_indexable_input(self):
        if not self.content.strip() and not self.source_url.strip() and not self.attachments:
            raise ValueError("knowledge source snapshot has no indexable content")
        return self


class KnowledgeReembeddingRequest(BaseModel):
    embedding_profile_id: str = Field(
        pattern=r"^[a-z0-9][a-z0-9_-]{0,63}(:v[1-9][0-9]*)?$"
    )
    knowledge_base_id: str | None = Field(default=None, max_length=128)
    doc_id: str | None = Field(default=None, max_length=128)


__all__ = [
    "KnowledgeAttachmentSnapshot",
    "KnowledgeReembeddingRequest",
    "KnowledgeSourceSnapshotRequest",
]
