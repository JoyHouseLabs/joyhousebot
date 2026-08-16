# Media Monitor Capability

`capability-media-monitor` is the stable, Python-native acquisition layer for RSS and Atom media
sources. It does not own a database or create a second scheduler: a scheduled Agent Run passes the
last `next_cursor` through Runtime Monitor Scratch and receives a new cursor plus only unseen items.

The capability deliberately returns source evidence rather than an AI summary. A Skill or App may
filter entries, ask for a full article fetch through `capability-context-assets`, then create a
brief or opportunity record. This keeps source acquisition reusable and preserves the standard
Artifact / Knowledge / Run trail.

Security and behavior:

- all egress uses Porthouse's DNS-pinned SSRF boundary and bounded response size;
- only RSS/Atom XML is accepted; DTD/entity declarations are rejected;
- ETag, Last-Modified, and up to 500 source entry identities stay in an opaque Runtime cursor;
- feeds without RSS/Atom should use a reviewed OpenCLI source adapter, not an arbitrary scraper.

Install/enable it like other Python extensions, then grant an Agent `network.http.read` and add
`media.feed.read` to its approved tools.
