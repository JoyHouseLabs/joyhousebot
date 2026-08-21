"""Generic media feed observation. Cursor storage remains Runtime-owned."""

from __future__ import annotations

from typing import Any

from joyhousebot.extension_sdk import (
    CapabilityContext,
    CapabilityDefinition,
    CapabilityExtensionManifest,
    CapabilityKind,
    CapabilityRef,
    CapabilityResult,
)
from joyhousebot.extension_sdk.manifest import source_tree_digest
from joyhousebot.extension_sdk.network import sanitize_error_message

from .feed import FeedParseError, fetch_feed, new_entries, next_cursor

_MAX_KNOWN_IDS = 500


class FeedReadHandler:
    async def execute(self, context: CapabilityContext, input: dict[str, Any]) -> CapabilityResult:
        del context
        url = str(input.get("feed_url") or "").strip()
        if not url:
            return _failure("INVALID_PARAMETERS", "feed_url is required")
        max_items = int(input.get("max_items") or 20)
        cursor = dict(input.get("cursor") or {})
        known = {str(value) for value in cursor.get("known_entry_ids") or [] if str(value)}
        if len(known) > _MAX_KNOWN_IDS:
            return _failure("INVALID_PARAMETERS", "cursor.known_entry_ids exceeds 500 entries")
        try:
            snapshot = await fetch_feed(url, cursor=cursor, max_items=max_items)
        except FeedParseError as exc:
            return _failure("FEED_INVALID", str(exc))
        except Exception as exc:
            return _failure("FEED_FETCH_FAILED", sanitize_error_message(str(exc)), retryable=True)
        additions = new_entries(snapshot, known)
        return CapabilityResult(
            success=True,
            output={
                "feed_url": url,
                "feed_title": snapshot.title,
                "not_modified": snapshot.not_modified,
                "new_entries": [entry.to_dict() for entry in additions],
                "observed_entries": [entry.to_dict() for entry in snapshot.entries],
                "next_cursor": next_cursor(snapshot, known),
            },
            metadata={"summary": f"Observed {len(additions)} new item(s) from the media source."},
        )


class MediaMonitorExtension:
    extension_id = "capability-media-monitor"
    version = "0.1.0"

    def manifest(self) -> CapabilityExtensionManifest:
        return CapabilityExtensionManifest(
            extension_id=self.extension_id,
            version=self.version,
            name="Media Monitor",
            description="Read RSS and Atom sources with a Runtime-owned incremental cursor.",
            distribution_name="joyhousebot-capability-media-monitor",
            build_digest=source_tree_digest(__file__),
            runtime_contract_version=2,
            required_permissions=("network.http.read",),
            dependencies=({"id": "outbound-http", "kind": "service", "required": True},),
        )

    def register(self, registry: Any) -> None:
        registry.register_capability(_definition(self.version), FeedReadHandler())

    def health_checks(self) -> tuple[Any, ...]:
        return ()


def _definition(version: str) -> CapabilityDefinition:
    return CapabilityDefinition(
        ref=CapabilityRef("media.feed.read", version, CapabilityKind.CAPABILITY),
        name="Read media feed",
        description=(
            "Read one RSS or Atom feed through protected HTTP, returning a bounded snapshot and "
            "an opaque next cursor. Persist the cursor through Runtime monitor scratch, never in an Agent prompt."
        ),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["feed_url"],
            "properties": {
                "feed_url": {"type": "string", "minLength": 8, "maxLength": 2000},
                "max_items": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                "cursor": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "etag": {"type": ["string", "null"], "maxLength": 1024},
                        "last_modified": {"type": ["string", "null"], "maxLength": 1024},
                        "known_entry_ids": {
                            "type": "array",
                            "maxItems": _MAX_KNOWN_IDS,
                            "items": {"type": "string", "minLength": 1, "maxLength": 128},
                        },
                    },
                },
            },
        },
        output_schema={"type": "object"},
        adapter="extension",
        tags=("media", "rss", "atom", "monitor", "ingestion"),
        expected_duration_seconds=5,
        timeout_seconds=60,
        idempotent=True,
        retryable=True,
        side_effect="read",
        permissions=("network.http.read",),
        data_classification="confidential",
        invocation_concurrency="parallel_safe",
        max_concurrent_invocations=4,
    )


def _failure(code: str, message: str, *, retryable: bool = False) -> CapabilityResult:
    return CapabilityResult(success=False, error={"code": code, "message": message, "retryable": retryable})


def create_extension() -> MediaMonitorExtension:
    return MediaMonitorExtension()
