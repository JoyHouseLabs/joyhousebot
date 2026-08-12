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

1. validates the snapshot and resolves an installed parser from the parser registry;
2. fetches a public reference only through Core's DNS-pinned SSRF boundary, redirect checks, MIME allowlist and 10 MB
   compressed-size cap;
3. emits structured chunks and parser/chunker profile metadata;
4. asks the narrow Runtime Context service to stage, verify and activate a revision;
5. returns a write receipt tied to Runtime `action_id` and `idempotency_key`.

The official parser registry currently handles inline text, public HTML/text/JSON/XML, PDF, DOCX, PPTX and XLSX. Office
archives have an additional 50 MB expanded-size cap and reject unsafe paths and XML entity declarations. PDF parsing uses
the extension-local `pypdf` dependency. A source can combine inline text and multiple attachments; parser identity and
version are frozen into the index revision.

`local_vault` and `cloud_vault` references deliberately fail closed until a resolver is installed that can exchange a
short-lived readable stream for the current Run. OCR, image understanding and transcription remain separate extension
capabilities because they need explicit model, quota and data-classification permissions.

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

A parser or reference-resolution failure is also persisted as an immutable failed revision tied to the Run. It advances
the observed source generation while leaving the previous active projection searchable. Archived sources remain auditable
but are excluded from normal retrieval.

## Vector readiness decision

Vector indexing remains disabled by default. PostgreSQL full-text search is the zero-provider baseline and is already safe
to rebuild from immutable chunks. Vector activation requires all of the following to be true first:

- an enabled, deployment-allowed embedding Provider and exact model ID;
- a versioned embedding profile containing model ID, dimensions, normalization and chunker version;
- `pgvector` availability verified by the migrator, without letting an Extension inject DDL;
- per-Run token/cost policy, retry and rate-limit handling;
- dual-write staging and complete-vector-count verification before active revision promotion;
- a rebuild path for model or dimension changes and retrieval Eval coverage.

Until those gates exist, silently generating partial or provider-dependent embeddings would weaken revision consistency.

The `retrieve` capability accepts an optional `collection_ref`. It filters the active projection by the frozen collection
references so an App or Skill can request only the relevant branch of the user's library without reading Product tables.
