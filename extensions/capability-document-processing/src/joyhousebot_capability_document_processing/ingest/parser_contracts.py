"""Stable parser contracts shared by document sources and registries."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

from .models import Chunk


class SourceParseError(ValueError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        parser_id: str = "unresolved",
        parser_version: str = "1",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.parser_id = parser_id
        self.parser_version = parser_version


@dataclass(frozen=True)
class ParseCandidate:
    reference_kind: str
    uri: str = ""
    display_name: str = ""
    media_type: str = ""
    content: str = ""
    asset_id: str = ""
    binary_body: bytes = b""

    @property
    def extension(self) -> str:
        path = unquote(urlparse(self.uri).path) or self.display_name
        return PurePosixPath(path).suffix.lower()


@dataclass
class ParsedCandidate:
    parser_id: str
    parser_version: str
    chunks: list[Chunk]
    trace: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedSnapshot:
    parser_id: str
    parser_version: str
    chunks: list[dict[str, Any]]
    trace: dict[str, Any]


class CandidateParser(Protocol):
    parser_id: str
    parser_version: str
    priority: int

    def supports(self, candidate: ParseCandidate) -> bool: ...

    async def parse(self, candidate: ParseCandidate) -> ParsedCandidate: ...


__all__ = [
    "CandidateParser",
    "ParseCandidate",
    "ParsedCandidate",
    "ParsedSnapshot",
    "SourceParseError",
]
