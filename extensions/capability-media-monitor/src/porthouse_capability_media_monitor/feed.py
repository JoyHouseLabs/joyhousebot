"""Bounded, dependency-free RSS/Atom parsing and conditional fetching."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from porthouse.extension_sdk.network import (
    DEFAULT_MAX_BYTES,
    SsrfProtectedTransport,
    TrackedAsyncClient,
    fetch_url,
    validate_url,
)

_ATOM = "http://www.w3.org/2005/Atom"
_MAX_ITEMS = 100
_REQUEST_TIMEOUT = 30.0
_TAG = re.compile(r"<[^>]+>")


class FeedParseError(ValueError):
    """A source did not return a valid bounded RSS or Atom document."""


@dataclass(frozen=True, slots=True)
class FeedEntry:
    entry_id: str
    title: str
    url: str
    published_at: str | None
    updated_at: str | None
    summary: str

    def to_dict(self) -> dict[str, str | None]:
        return {
            "entry_id": self.entry_id,
            "title": self.title,
            "url": self.url,
            "published_at": self.published_at,
            "updated_at": self.updated_at,
            "summary": self.summary,
        }


@dataclass(frozen=True, slots=True)
class FeedSnapshot:
    title: str
    entries: tuple[FeedEntry, ...]
    etag: str | None
    last_modified: str | None
    not_modified: bool = False


def canonical_url(value: str) -> str:
    """Normalize identity without discarding query parameters used by publishers."""
    parsed = urlsplit(str(value).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    hostname = parsed.hostname.lower()
    port = parsed.port
    netloc = hostname if port in {None, 80, 443} else f"{hostname}:{port}"
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, parsed.query, ""))


def parse_feed(text: str, *, max_items: int) -> tuple[str, tuple[FeedEntry, ...]]:
    """Parse RSS 2.0 or Atom without resolving external XML entities."""
    if not 1 <= max_items <= _MAX_ITEMS:
        raise FeedParseError("max_items must be between 1 and 100")
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise FeedParseError("feed DTD and entity declarations are not supported")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise FeedParseError("feed XML is invalid") from exc
    if _local(root.tag) == "rss":
        channel = _first_child(root, "channel")
        if channel is None:
            raise FeedParseError("RSS feed has no channel")
        return _text(channel, "title") or "Untitled feed", tuple(
            _rss_entry(item) for item in _children(channel, "item")[:max_items]
        )
    if root.tag == f"{{{_ATOM}}}feed" or _local(root.tag) == "feed":
        return _atom_text(root, "title") or "Untitled feed", tuple(
            _atom_entry(item) for item in _children(root, "entry")[:max_items]
        )
    raise FeedParseError("feed must be RSS or Atom")


async def fetch_feed(
    url: str,
    *,
    cursor: dict[str, Any] | None = None,
    max_items: int,
) -> FeedSnapshot:
    """Read one public feed via the common SSRF-safe egress boundary."""
    valid, reason = validate_url(url)
    if not valid:
        raise FeedParseError(f"feed URL is invalid: {reason}")
    headers = {"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"}
    cursor = cursor or {}
    if str(cursor.get("etag") or "").strip():
        headers["If-None-Match"] = str(cursor["etag"]).strip()
    if str(cursor.get("last_modified") or "").strip():
        headers["If-Modified-Since"] = str(cursor["last_modified"]).strip()
    async with TrackedAsyncClient(
        propagate_headers=False,
        transport=SsrfProtectedTransport(),
        follow_redirects=False,
        timeout=_REQUEST_TIMEOUT,
    ) as client:
        # `fetch_url` deliberately raises on non-2xx responses. Conditional GET
        # needs 304 to remain an ordinary, no-change observation.
        async with client.stream("GET", url, headers=headers) as response:
            if response.status_code == 304:
                return FeedSnapshot(
                    title="",
                    entries=(),
                    etag=response.headers.get("etag") or str(cursor.get("etag") or "") or None,
                    last_modified=(
                        response.headers.get("last-modified")
                        or str(cursor.get("last_modified") or "")
                        or None
                    ),
                    not_modified=True,
                )
    # Repeat through the shared redirect, DNS-pinning, content-type and size policy.
    async with TrackedAsyncClient(
        propagate_headers=False,
        transport=SsrfProtectedTransport(),
        follow_redirects=False,
        timeout=_REQUEST_TIMEOUT,
    ) as client:
        response, body = await fetch_url(
            client,
            url,
            headers=headers,
            max_bytes=DEFAULT_MAX_BYTES,
        )
    title, entries = parse_feed(body, max_items=max_items)
    return FeedSnapshot(
        title=title,
        entries=entries,
        etag=response.headers.get("etag") or None,
        last_modified=response.headers.get("last-modified") or None,
    )


def new_entries(snapshot: FeedSnapshot, known_entry_ids: set[str]) -> tuple[FeedEntry, ...]:
    return tuple(entry for entry in snapshot.entries if entry.entry_id not in known_entry_ids)


def next_cursor(snapshot: FeedSnapshot, known_entry_ids: set[str]) -> dict[str, Any]:
    ordered = [entry.entry_id for entry in snapshot.entries]
    merged = list(dict.fromkeys([*ordered, *sorted(known_entry_ids)]))[:500]
    return {
        "etag": snapshot.etag,
        "last_modified": snapshot.last_modified,
        "known_entry_ids": merged,
    }


def _rss_entry(item: ET.Element) -> FeedEntry:
    url = canonical_url(_text(item, "link"))
    title = _text(item, "title") or "Untitled article"
    raw_id = _text(item, "guid") or url or title
    return FeedEntry(
        entry_id=_entry_id(raw_id, url),
        title=_clean(title),
        url=url,
        published_at=_date(_text(item, "pubDate")),
        updated_at=None,
        summary=_clean(_text(item, "description") or _text(item, "encoded")),
    )


def _atom_entry(item: ET.Element) -> FeedEntry:
    link = ""
    for candidate in _children(item, "link"):
        href = canonical_url(candidate.attrib.get("href") or "")
        rel = candidate.attrib.get("rel", "alternate")
        if href and rel in {"alternate", ""}:
            link = href
            break
        if href and not link:
            link = href
    title = _atom_text(item, "title") or "Untitled article"
    raw_id = _atom_text(item, "id") or link or title
    return FeedEntry(
        entry_id=_entry_id(raw_id, link),
        title=_clean(title),
        url=link,
        published_at=_date(_atom_text(item, "published")),
        updated_at=_date(_atom_text(item, "updated")),
        summary=_clean(_atom_text(item, "summary") or _atom_text(item, "content")),
    )


def _entry_id(raw_id: str, url: str) -> str:
    return hashlib.sha256(f"{raw_id.strip()}\n{url}".encode("utf-8")).hexdigest()


def _date(value: str) -> str | None:
    raw = value.strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            return datetime.fromisoformat(raw[:-1] + "+00:00").astimezone(UTC).isoformat()
        return datetime.fromisoformat(raw).astimezone(UTC).isoformat()
    except ValueError:
        try:
            return parsedate_to_datetime(raw).astimezone(UTC).isoformat()
        except (TypeError, ValueError):
            return raw[:128]


def _children(node: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in node if _local(child.tag) == name]


def _first_child(node: ET.Element, name: str) -> ET.Element | None:
    return next(iter(_children(node, name)), None)


def _text(node: ET.Element, name: str) -> str:
    child = _first_child(node, name)
    return "".join(child.itertext()) if child is not None else ""


def _atom_text(node: ET.Element, name: str) -> str:
    for child in node:
        if _local(child.tag) == name:
            return "".join(child.itertext())
    return ""


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(_TAG.sub(" ", value))).strip()[:20_000]
