from joyhousebot_capability_media_monitor.extension import MediaMonitorExtension
from joyhousebot_capability_media_monitor.feed import new_entries, next_cursor, parse_feed

from joyhousebot.capabilities import CapabilityExtensionRegistry
from joyhousebot.contracts import CapabilityContext

RSS = """<?xml version="1.0"?>
<rss><channel><title>News</title>
<item><guid>one</guid><title>First</title><link>https://news.example/a#fragment</link><pubDate>Tue, 16 Aug 2026 10:00:00 GMT</pubDate><description>One <b>summary</b></description></item>
<item><guid>two</guid><title>Second</title><link>https://news.example/b</link></item>
</channel></rss>"""


def test_media_monitor_registers_a_scoped_read_capability() -> None:
    registry = CapabilityExtensionRegistry()
    registry.register_extension(MediaMonitorExtension())
    definition, _ = registry.get("media.feed.read", "0.1.0")
    assert definition.permissions == ("network.http.read",)
    assert definition.side_effect == "read"
    assert definition.ref.extension_id == "capability-media-monitor"


def test_rss_parser_generates_stable_identities_and_cursor() -> None:
    title, entries = parse_feed(RSS, max_items=10)
    assert title == "News"
    assert entries[0].url == "https://news.example/a"
    assert entries[0].summary == "One summary"
    additions = new_entries(type("Snapshot", (), {"entries": entries})(), {entries[0].entry_id})
    assert [item.title for item in additions] == ["Second"]
    cursor = next_cursor(type("Snapshot", (), {"entries": entries, "etag": "tag", "last_modified": None})(), set())
    assert cursor["etag"] == "tag"
    assert cursor["known_entry_ids"] == [entries[0].entry_id, entries[1].entry_id]


async def test_media_monitor_requires_the_network_permission() -> None:
    registry = CapabilityExtensionRegistry()
    registry.register_extension(MediaMonitorExtension())
    result = await registry.invoke(
        "media.feed.read",
        {"feed_url": "https://example.com/feed.xml"},
        context=CapabilityContext("user", "session", "run", metadata={"permissions": []}),
    )
    assert result.success is False
    assert result.error["code"] == "PERMISSION_DENIED"
