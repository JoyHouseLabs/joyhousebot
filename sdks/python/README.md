# joyhousebot-sdk

Typed async Python client for joyhousebot `/v2`. Apps use `AppClient`; products acting on behalf of
their signed-in owner use `OwnerClient`. The package has no Runtime, database, Market, or Extension
dependency.

`RunHandle.operations()` returns the bounded, user-facing progress projection for long-running
remote operations. Provider operation identifiers, credentials, and arbitrary provider payloads
are intentionally excluded.
