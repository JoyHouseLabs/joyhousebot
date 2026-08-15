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
- owner-scoped `GET /v1/knowledge/health` aggregates document readiness, queue depth, success rate, completion latency
  and bounded failure-code counts for a configurable 1–365 day window; it never returns titles or chunk content;
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

`POST /v1/knowledge/index-requests` is the user's explicit authorization to build this private projection. Core freezes
only the published capability's narrow `knowledge.write` authority into this internal Graph; that field is not exposed by
the public Graph API. `knowledge.index` therefore remains a durable side-effecting Action with a write receipt, but does
not add a second human approval after the owner already requested indexing. External publication, destructive mutation
and other non-internal writes retain the normal approval policy.

The official parser registry currently handles inline text, public HTML/text/JSON/XML, PDF, DOCX, PPTX and XLSX. Office
archives have an additional 50 MB expanded-size cap and reject unsafe paths and XML entity declarations. PDF parsing uses
the extension-local `pypdf` dependency. A source can combine inline text and multiple attachments; parser identity and
version are frozen into the index revision. Extracted text is Unicode NFKC-normalized before chunking, including the
unmapped simplified CJK radical glyphs commonly emitted by presentation-generated PDFs, so ordinary Chinese queries use
the same characters as indexed content. This normalization is recorded as `semantic-text-v1` chunker version `2` so a
later rebuild never silently changes the meaning of an older active revision.

Product-local or cloud Vault identifiers are never sent to a Capability. The Product Gateway reads an owner-authorized
immutable object, streams it to `POST /v1/input-assets`, then submits a `runtime_input` attachment and its `asset_id`.
Run/Graph creation verifies ownership and atomically freezes `runtime_run_input_assets`; the Worker can read the bytes only
through `ContextPort.read_input_asset()` for that exact owner and Run. The public API never exposes the storage URI or host
path. Direct `local_vault` and `cloud_vault` references therefore continue to fail closed. OCR, image understanding and
transcription remain separate extension capabilities because they need explicit model, quota and data-classification
permissions.

## Runtime Input Assets

Worker 的普通运行日志只允许记录 channel、opaque sender/run 标识和内容长度，不得记录输入正文、模型响应预览或解析后的
私有文档内容；正文只存在于受权限控制的 Session、Run、Trace Blob 或 Artifact 链。

- `POST /v1/input-assets` requires `Idempotency-Key`, `Content-Length` and `X-Content-SHA256`, streams to a private
  content-addressed object store and returns only immutable metadata;
- `DELETE /v1/input-assets/{asset_id}` is owner-scoped and idempotent. It returns `404` for another owner and `409` while
  any bound Run is non-terminal; success records a `deleted` lifecycle event and makes later reads/bindings return `404`.
  The content-addressed object is removed by two-phase GC only after no ready asset references it. Deleting an Input Asset
  does not by itself erase historical Run Artifacts derived from it;
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

## Vector and hybrid retrieval

PostgreSQL full-text search remains the zero-provider baseline. K3 added the safe hybrid path; K4 makes it operable at
large corpus scale without weakening that baseline:

- the model catalog declares an exact `embedding` model and dimensions;
- an immutable Embedding Profile revision freezes Provider revision, model ID, dimensions, normalization, batching,
  operation cost, cluster request/token rates and HNSW build/search parameters; the mutable default selector lives on
  the Profile, outside the signed revision;
- publishing the profile fails closed unless the Provider revision is active and the operator has installed `pgvector`;
- `knowledge.index` accepts a profile id, resolves it to an exact profile revision, stages every chunk embedding and
  verifies embedding count before the revision can become ready or active;
- embeddings remain attached to immutable revision/chunk identity; incomplete embedding batches fail the new revision
  while preserving the prior active projection;
- retrieval embeds the query only when a default profile is published, fuses lexical and vector candidates with reciprocal
  rank fusion, and records the retrieval modes plus exact profile revision in evidence;
- embedding or vector-query failure degrades to owner-scoped lexical retrieval, never to a process-local vector store.

K4 adds four governed loops:

1. `knowledge_embedding_operations` records every successful or failed index/query/re-embedding call with the exact
   Profile revision, request count, provider token usage and calculated cost. Remote embedding models must declare
   `input_cost_per_million_tokens`; admission fails closed when pricing, operation budget or cluster-wide per-minute
   request/token quota is unavailable or exceeded.
2. The Agent Worker reconciles each published/retired Profile every five minutes. Below `ann_min_rows` it records exact
   search as the intentional strategy. At/above the threshold it elects one builder with a PostgreSQL advisory lock and
   creates a Profile-specific partial HNSW index with `CREATE INDEX CONCURRENTLY`; invalid interrupted indexes are
   discarded before retry. Search evidence reports `vector_strategy=exact|hnsw`.
3. `POST /v1/knowledge/reembedding-jobs` creates an idempotent owner-scoped job for all documents, one Knowledge base or
   one document. Items use database leases, fencing versions, bounded exponential retry and terminal parent closure.
   Re-embedding attaches a second immutable Profile projection to existing chunks; it does not reparse content or switch
   the active source revision. Console **Models → Embedding Profiles** exposes enqueue, progress, refresh and cancel for
   the currently selected user.
4. Eval suites may target `embedding_profile`. Each automated Case provides a bounded private corpus and query; Core
   builds a synthetic isolated Knowledge projection, invokes published `knowledge.index` and `retrieve` through the
   normal Run/Task/Capability chain, and scores returned evidence. A Draft Profile can execute only when the Capability
   owner is exactly `eval:<eval_run_id>` and that running Eval targets the exact revision. A release-gate policy can then
   block Profile publication until recent automated retrieval evidence passes.

Core owns profile governance, revision completeness and PostgreSQL data. Provider Extensions implement the model API but
cannot install database extensions or inject Runtime DDL. `GET /v1/admin/embedding-profiles/readiness` reports pgvector
availability, the published default profile and activation blockers without resolving secret values.

Operator activation order:

1. install `pgvector` in the target PostgreSQL instance (database-administrator action);
2. in Console **Models → Provider 配置**, declare an enabled `embedding` model with its exact dimensions and publish the
   Provider revision through the normal Worker preload/ACK flow;
3. in **Models → Embedding Profiles**, create a Draft from that exact Provider revision; for a production Profile, attach
   and pass an `embedding_profile` Eval release gate before publishing;
4. submit Knowledge snapshots with `embedding_profile_id`. Submission resolves a profile name to its exact immutable
   revision before the Run is created. Omitting it keeps that document on the lexical baseline.
5. after changing model or dimensions, enqueue a re-embedding job for the old corpus, wait for a terminal successful job,
   inspect vector readiness/index state, and only then make the new Profile the default.

Changing the default Profile does not silently rewrite existing embeddings. Old exact revisions remain resolvable for
frozen work, audit and rollback; new submissions use the current published revision. Re-embedding is always an explicit
durable job, never an implicit side effect of profile publication.

The `retrieve` capability and public search API accept an optional `collection_ref`. Activation projects frozen collection
references into `knowledge_document_scopes`; retrieval no longer queries ad-hoc metadata JSON and still never reads Product
tables. Search evidence is copied from the active immutable revision and includes `revision_id`, page, section path,
block type, character range and chunk content hash.
