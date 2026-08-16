"""Content-addressed storage for JSON payloads kept outside PostgreSQL rows."""

from __future__ import annotations

import json
import os
import time
from hashlib import sha256 as hashlib_sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Protocol

from porthouse.domain.identity import canonical_json


class ContentBlobStore(Protocol):
    """Minimal contract implemented by local or cloud object-store adapters."""

    def put_json(self, value: Any, *, sha256: str) -> str: ...

    def get_json(self, uri: str, *, expected_sha256: str) -> Any: ...

class LocalContentBlobStore:
    """Private content-addressed JSON store on a local or shared filesystem."""

    uri_prefix = "porthouse-blob://sha256/"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise ValueError("Runtime Blob directory must be a directory")

    def put_json(self, value: Any, *, sha256: str) -> str:
        digest = self._digest(sha256)
        raw = canonical_json(value).encode("utf-8")
        if hashlib_sha256(raw).hexdigest() != digest:
            raise ValueError("Runtime Blob value does not match its requested SHA-256")
        target = self._path(digest)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not target.exists():
            with NamedTemporaryFile(
                mode="wb",
                prefix=f".{digest}.",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as temporary:
                temporary.write(raw)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            temporary_path.chmod(0o600)
            try:
                temporary_path.replace(target)
            finally:
                temporary_path.unlink(missing_ok=True)
        else:
            # A concurrent database transaction may be about to reference an
            # existing content-addressed object. Refresh its grace period and
            # cancel any earlier GC mark before returning its URI.
            target.touch(exist_ok=True)
        self._gc_marker(target).unlink(missing_ok=True)
        return f"{self.uri_prefix}{digest}"

    def get_json(self, uri: str, *, expected_sha256: str) -> Any:
        if not uri.startswith(self.uri_prefix):
            raise ValueError("Unsupported Runtime Blob URI")
        digest = self._digest(uri.removeprefix(self.uri_prefix))
        if digest != self._digest(expected_sha256):
            raise ValueError("Runtime Blob URI does not match its recorded SHA-256")
        raw = self._path(digest).read_bytes()
        if hashlib_sha256(raw).hexdigest() != digest:
            raise ValueError("Runtime Blob content failed SHA-256 verification")
        return json.loads(raw)

    def prune_unreferenced(
        self, referenced_uris: set[str], *, min_unreferenced_seconds: int = 86400
    ) -> int:
        """Two-phase removal of objects absent from all PostgreSQL references.

        The first sweep only writes a marker. A later sweep may remove the
        object after the grace period. ``put_json`` clears that marker, which
        protects in-flight transactions that reuse an existing digest.
        """
        referenced = {
            uri.removeprefix(self.uri_prefix)
            for uri in referenced_uris
            if uri.startswith(self.uri_prefix)
        }
        cutoff = time.time() - max(0, int(min_unreferenced_seconds))
        removed = 0
        for target in self.root.glob("*/*/*.json"):
            digest = target.stem
            try:
                self._digest(digest)
            except ValueError:
                continue
            marker = self._gc_marker(target)
            if digest in referenced:
                marker.unlink(missing_ok=True)
                continue
            if not marker.exists():
                marker.touch(mode=0o600, exist_ok=True)
                continue
            try:
                # ``put_json`` refreshes the object before returning its URI.
                # Recheck both marker and object timestamps immediately before
                # unlinking so an in-flight reference gets the full grace.
                if marker.stat().st_mtime > cutoff or target.stat().st_mtime > cutoff:
                    continue
                target.unlink(missing_ok=True)
                marker.unlink(missing_ok=True)
                removed += 1
            except FileNotFoundError:
                continue
        return removed

    def _path(self, digest: str) -> Path:
        return self.root / digest[:2] / digest[2:4] / f"{digest}.json"

    @staticmethod
    def _gc_marker(target: Path) -> Path:
        return target.with_suffix(".gc")

    @staticmethod
    def _digest(value: str) -> str:
        normalized = str(value).strip().lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("Runtime Blob SHA-256 is invalid")
        return normalized


def externalize_json(
    blob_store: ContentBlobStore | None,
    value: Any,
    *,
    sha256: str,
    size_bytes: int,
    inline_threshold_bytes: int,
) -> tuple[Any, str | None]:
    """Return an inline value or an immutable object-store reference."""
    if blob_store is None or size_bytes <= max(0, inline_threshold_bytes):
        return value, None
    return None, blob_store.put_json(value, sha256=sha256)


def hydrate_json(
    blob_store: ContentBlobStore | None,
    content: Any,
    storage_uri: str | None,
    *,
    sha256: str,
) -> Any:
    if content is not None or not storage_uri:
        return content
    if not storage_uri.startswith(LocalContentBlobStore.uri_prefix):
        return content
    if blob_store is None:
        raise RuntimeError("Runtime Blob store is required to read externalized content")
    return blob_store.get_json(storage_uri, expected_sha256=sha256)


__all__ = [
    "ContentBlobStore",
    "LocalContentBlobStore",
    "externalize_json",
    "hydrate_json",
]
