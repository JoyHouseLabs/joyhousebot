# JoyhouseBot Context Assets Capability

Optional durable context capabilities for JoyhouseBot:

- `retrieve`: search user-scoped knowledge or Agent memory;
- `memory_get`: read a document from the current Run's memory scope;
- `fetch_url_to_knowledgebase`: fetch a public URL and index its readable content.

The extension receives only the Runtime's scope-enforcing context service. It does not
receive a PostgreSQL repository or construct its own user/Agent scope.

Install the package and explicitly enable capability plugin discovery, or add
`joyhousebot_capability_context_assets` to `tools.capabilityPlugins`.
