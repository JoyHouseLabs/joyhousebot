# JoyhouseBot Context Assets Capability

Optional durable context capabilities for JoyhouseBot:

Document parsing primitives are supplied by the independently installable
`capability-document-processing` package. Knowledge indexing reuses those parsers;
private one-off extraction uses `document.extract` and does not create Knowledge.

- `retrieve`: search user-scoped knowledge or Agent memory;
- `memory_get`: read a document from the current Run's memory scope;
- `fetch_url_to_knowledgebase`: fetch a public URL and index its readable content.
- `knowledge.index`: parse immutable Runtime inputs and stage a versioned private
  Knowledge revision. The built-in Office Open XML parser supports DOCX paragraphs,
  heading paths, list items, table rows, and explicit page-break evidence, as well as
  page-positioned PPTX slides and XLSX sheets.
- `knowledge.index` can opt into an operator-published Embedding Profile. The Runtime
  owns Provider resolution, immutable vector staging and completeness checks; the
  extension never receives credentials or database access.

The extension receives only the Runtime's scope-enforcing context service. It does not
receive a PostgreSQL repository or construct its own user/Agent scope.

Install the package and explicitly enable capability plugin discovery, or add
`joyhousebot_capability_context_assets` to `tools.capabilityPlugins`.
