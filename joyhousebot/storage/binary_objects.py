"""Content-addressed binary objects used by immutable Runtime input assets."""

from __future__ import annotations

import hashlib
import os
import tempfile
import time
from collections.abc import AsyncIterable
from dataclasses import dataclass
from pathlib import Path

import anyio


@dataclass(frozen=True, slots=True)
class BinaryObject:
    uri: str
    object_version: str
    content_sha256: str
    byte_size: int


class LocalBinaryObjectStore:
    """Local content-addressed store; deployments may replace this adapter.

    Files are never addressed by caller-supplied names. A staged upload is
    hashed while streaming, then atomically promoted to its digest path.
    """

    def __init__(self, directory: str | Path, *, scheme: str = "joyhouse-input") -> None:
        if not scheme or any(char not in "abcdefghijklmnopqrstuvwxyz-" for char in scheme):
            raise ValueError("binary object store scheme is invalid")
        self.scheme = scheme
        self.root = Path(directory).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)

    async def put_stream(
        self,
        chunks: AsyncIterable[bytes],
        *,
        expected_sha256: str,
        expected_size: int,
        max_bytes: int,
    ) -> BinaryObject:
        digest = expected_sha256.strip().lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("X-Content-SHA256 must be a lowercase SHA-256 hex digest")
        if expected_size < 0 or expected_size > max_bytes:
            raise ValueError(f"input asset exceeds the {max_bytes} byte upload limit")
        descriptor, staged_name = tempfile.mkstemp(prefix=".upload-", dir=self.root)
        os.close(descriptor)
        staged = Path(staged_name)
        hasher = hashlib.sha256()
        size = 0
        try:
            async with await anyio.open_file(staged, "wb") as output:
                async for chunk in chunks:
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > max_bytes or size > expected_size:
                        raise ValueError("input asset body exceeds declared Content-Length")
                    hasher.update(chunk)
                    await output.write(chunk)
            actual = hasher.hexdigest()
            if size != expected_size:
                raise ValueError("input asset body does not match declared Content-Length")
            if actual != digest:
                raise ValueError("input asset body does not match X-Content-SHA256")
            destination = self._path(digest)
            await anyio.to_thread.run_sync(destination.parent.mkdir, 0o700, True, True)
            await anyio.to_thread.run_sync(os.chmod, staged, 0o600)
            if destination.exists():
                await anyio.to_thread.run_sync(staged.unlink, True)
                await anyio.to_thread.run_sync(destination.touch, 0o600, True)
            else:
                await anyio.to_thread.run_sync(os.replace, staged, destination)
            await anyio.to_thread.run_sync(self._gc_marker(destination).unlink, True)
            return BinaryObject(
                uri=f"{self.scheme}://sha256/{digest}",
                object_version=f"sha256:{digest}",
                content_sha256=digest,
                byte_size=size,
            )
        finally:
            if staged.exists():
                staged.unlink(missing_ok=True)

    def read_bytes(self, uri: str, *, max_bytes: int) -> bytes:
        digest = self._digest(uri)
        path = self._path(digest)
        try:
            size = path.stat().st_size
        except FileNotFoundError as exc:
            raise FileNotFoundError("input asset object is unavailable") from exc
        if size > max_bytes:
            raise ValueError(f"input asset exceeds the {max_bytes} byte read limit")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != digest:
            raise OSError("input asset object checksum verification failed")
        return body

    def prune_unreferenced(
        self, referenced_uris: set[str], *, min_unreferenced_seconds: int = 86400
    ) -> int:
        """Two-phase deletion of content absent from all ready asset records."""
        referenced = {
            self._digest(uri)
            for uri in referenced_uris
            if uri.startswith(f"{self.scheme}://sha256/")
        }
        cutoff = time.time() - max(0, int(min_unreferenced_seconds))
        removed = 0
        for target in self.root.glob("*/*/*.bin"):
            digest = target.stem
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                continue
            marker = self._gc_marker(target)
            if digest in referenced:
                marker.unlink(missing_ok=True)
                continue
            if not marker.exists():
                marker.touch(mode=0o600, exist_ok=True)
                continue
            try:
                if marker.stat().st_mtime > cutoff or target.stat().st_mtime > cutoff:
                    continue
                target.unlink(missing_ok=True)
                marker.unlink(missing_ok=True)
                removed += 1
            except FileNotFoundError:
                continue
        return removed

    def _path(self, digest: str) -> Path:
        return self.root / digest[:2] / digest[2:4] / f"{digest}.bin"

    @staticmethod
    def _gc_marker(target: Path) -> Path:
        return target.with_suffix(".gc")

    def _digest(self, uri: str) -> str:
        prefix = f"{self.scheme}://sha256/"
        if not uri.startswith(prefix):
            raise ValueError("unsupported input asset storage URI")
        digest = uri.removeprefix(prefix)
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("invalid input asset storage URI")
        return digest


__all__ = ["BinaryObject", "LocalBinaryObjectStore"]
