# Knowledge Runtime boundary

JoyhouseBot Core owns the durable and governed mechanics of Knowledge; source-specific parsing remains an official or
third-party extension capability.

## Core responsibilities

- authenticated `POST /v1/knowledge/index-requests` submission with a required stable `Idempotency-Key`;
- compilation into the common Run/Task/Event/Trace path;
- owner-scoped PostgreSQL documents, immutable revisions, chunks, active projection and audit events;
- atomic revision activation, failed-revision preservation and stale-generation rejection;
- retrieval over the active projection only;
- owner-scoped `GET /v1/knowledge/search` with document, revision, page, section and character-range evidence;
- stable source lookup through `GET /v1/knowledge/source-state` for Product reconciliation and revision history;
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

Product-local or cloud Vault identifiers are never sent to a Capability. The Product Gateway reads an owner-authorized
immutable object, streams it to `POST /v1/input-assets`, then submits a `runtime_input` attachment and its `asset_id`.
Run/Graph creation verifies ownership and atomically freezes `runtime_run_input_assets`; the Worker can read the bytes only
through `ContextPort.read_input_asset()` for that exact owner and Run. The public API never exposes the storage URI or host
path. Direct `local_vault` and `cloud_vault` references therefore continue to fail closed. OCR, image understanding and
transcription remain separate extension capabilities because they need explicit model, quota and data-classification
permissions.

## Runtime Input Assets

- `POST /v1/input-assets` requires `Idempotency-Key`, `Content-Length` and `X-Content-SHA256`, streams to a private
  content-addressed object store and returns only immutable metadata;
- `POST /v1/runs`, `POST /v1/runs/graphs` and Workflow execution accept up to 20 `input_asset_ids`; binding occurs in the
  same PostgreSQL transaction as Run creation and idempotent replay must name the same set;
- `runtime_input_asset_events` records create/bind/read lifecycle events while `runtime_logs` records Run-scoped reads;
- the normal Runtime retention job soft-deletes unneeded assets only after every bound Run is terminal, then reclaims
  unreferenced objects with a two-phase 24-hour grace period;
- single-host deployments may use `runtime.store.inputAssetDirectory`; multi-host API/Worker deployments need one shared
  durable filesystem or an adapter implementing the same binary object-store contract.

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

The `retrieve` capability and public search API accept an optional `collection_ref`. Activation projects frozen collection
references into `knowledge_document_scopes`; retrieval no longer queries ad-hoc metadata JSON and still never reads Product
tables. Search evidence is copied from the active immutable revision and includes `revision_id`, page, section path,
block type, character range and chunk content hash.
