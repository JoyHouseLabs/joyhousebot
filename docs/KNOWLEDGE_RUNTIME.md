# Knowledge Runtime boundary

JoyhouseBot Core owns the durable and governed mechanics of Knowledge; source-specific parsing remains an official or
third-party extension capability.

## Core responsibilities

- authenticated `POST /v1/knowledge/index-requests` submission with a required stable `Idempotency-Key`;
- compilation into the common Run/Task/Event/Trace path;
- owner-scoped PostgreSQL documents, immutable revisions, chunks, active projection and audit events;
- atomic revision activation, failed-revision preservation and stale-generation rejection;
- retrieval over the active projection only;
- versioned Knowledge base membership and control-plane APIs.

Core does not import Product models, read `product_*` tables, fetch remote files in HTTP request threads or implement
vendor-specific embeddings and document parsers.

## Extension responsibilities

The official `capability-context-assets` package publishes `knowledge.index`. A Worker invokes it with a frozen Action
identity and immutable source snapshot. The extension:

1. validates the snapshot and installed parser;
2. fetches a public web source only when the snapshot intentionally contains a URL and no body;
3. emits structured chunks and parser/chunker profile metadata;
4. asks the narrow Runtime Context service to stage, verify and activate a revision;
5. returns a write receipt tied to Runtime `action_id` and `idempotency_key`.

Additional PDF/Office/OCR/transcription and embedding providers must be separate extension capabilities. Their network,
filesystem, model, quota and data-classification permissions remain explicit in the Capability Registry.

## Public snapshot contract

The request carries `source_system`, `source_id`, `source_version`, monotonic `source_generation`, `source_status`,
`content_sha256`, content or stable references, classification metadata and `index_profile_id`. Runtime derives one stable
document identity from `(user_id, source_system, source_id)`.

`source_version` describes the upstream business object. `source_generation` describes the ordered Knowledge projection
request and must increase for changes such as collection membership that do not increment the business object's version.
When executions complete out of order, activation rejects any generation lower than the current active generation.

## Storage lifecycle

```text
Run accepted
  → knowledge_index_revisions(staging)
  → knowledge_revision_chunks
  → revision ready
  → atomic copy/switch of active knowledge_chunks
  → previous revision superseded
```

A failed staging/ready revision records its error and leaves the previous active projection searchable. Archived sources
remain auditable but are excluded from normal retrieval. Vector columns are intentionally deferred until an embedding
profile, dimensions, cost policy and rebuild story are versioned; PostgreSQL full-text search is the zero-provider baseline.

The `retrieve` capability accepts an optional `collection_ref`. It filters the active projection by the frozen collection
references so an App or Skill can request only the relevant branch of the user's library without reading Product tables.
