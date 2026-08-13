# Joyhousebot

## Durable, governed execution for Agent applications

Joyhousebot is not a single-agent chat client or a model-vendor SDK. It is an open-source, PostgreSQL-first Agent Runtime for local or cloud deployment. It turns goals into durable Runs and Tasks with governed capabilities, recovery, human confirmation, evidence, audit, and replay.

It provides one execution control plane for building, publishing, operating, and governing Agent applications without making a product, model, or business domain a Core dependency.

## Why Joyhousebot

Most Agent tools answer a prompt or call a tool once. Joyhousebot makes a goal progress safely over hours, days, or longer. Its differentiation is not access to more models; it makes execution itself durable, governed, and reusable.

| Capability | Runtime mechanism | Result |
| --- | --- | --- |
| Durable execution | PostgreSQL Run/Task state machine, Worker leases, fencing, retries, and recovery | A task can be taken over after a process or Worker failure instead of starting over |
| Governed action | Capability Registry, allowlists, permissions, approvals, idempotency keys, and reconciliation | An Agent can act in external systems with explicit boundaries and receipts |
| Evidence and verification | Events, Traces, Artifacts, verification, audit, and replay | Users can inspect what happened, why, where it failed, and how to reproduce it |
| Compounding assets | Immutable Artifact → Work versions, Skills/Evals, and release gates | Outputs and methods remain reusable assets when a conversation or model changes |
| Safe extensibility | Core / Extension / App separation; versioned HTTP/SSE and MCP enter one Run chain | Models and capabilities can change without embedding product code or user state in Core |

## Architecture: one execution chain

```mermaid
flowchart TB
    CLIENTS[JoyHouse / independent Apps / Console / API clients]
    ENTRY[HTTP + SSE / schedules / webhooks / channels / MCP]
    API[API and control plane\nauthentication, submission, queries, releases]
    PG[(PostgreSQL\nthe sole runtime source of truth)]
    AGENT[Agent Workers\nplanning, models, Workflows, Tool Dispatcher]
    SCHEDULER[Scheduler Workers\nwakeups, recovery, timeouts, callbacks]
    CHANNEL[Channel Workers\ninbound channels and Outbox delivery]
    GOVERN[Governance\nallowlists · permissions · approvals · idempotency · audit]
    EXT[Extensions\nProviders · Channels · Connectors · Capabilities]
    APP[Independent Apps / external systems\nsigned Remote Capability]
    OUTPUT[Events · Traces · Artifacts · Work · Evals]

    CLIENTS --> ENTRY --> API --> PG
    PG <--> AGENT
    PG <--> SCHEDULER
    PG <--> CHANNEL
    AGENT --> GOVERN --> EXT
    EXT <--> APP
    AGENT --> OUTPUT
    SCHEDULER --> OUTPUT
    OUTPUT --> PG
```

Every entry point reaches the same `Run → Task → Event → Trace → Artifact` chain. APIs never execute models or tools in a request thread; Workers never treat process memory as the source of truth; external capabilities always run within version, permission, approval, and audit boundaries.

Business Apps remain independently deployable products; Skills are versioned methods; Extensions are technical Runtime artifacts. JoyHouse Market is a separate private repository and deployment; its replaceable Registry uses author DSSE signatures, Market attestations, TUF metadata, local permission approval, and signed Entitlements. Core does not require the official Market, and Market never receives private Run, Prompt, Memory, or Artifact contents. The private JoyHouse Desktop, Web, Mobile, website, and browser extension live in the adjacent `../joyhouse` product repository. See the [App integration contract](docs/APP_INTEGRATION.md) and [App Market governance protocol](docs/APP_MARKET_GOVERNANCE.md).

## Governance model

```text
Users / API clients
        │ identity, permissions, quotas, audit
        ▼
    Agent application
        │ revisions, scenarios, clarification DAGs, memory policy
        ▼
Capability catalog ─ Skills / Tools / MCP / Channels / Providers
        │ allowlists, policy, sandbox, health
        ▼
Runtime ─ Run / Task / Event / Trace / Artifact / Replay
        ▼
PostgreSQL source of truth
```

Governance is part of every execution: requests are authenticated, capabilities come from a published catalog, execution is recorded as resumable events and diagnostics, and results are archived with cost, latency, errors, and artifacts.

## Core capabilities

### Agent, scenario, and release governance

- Versioned catalogs for Agents, Skills, Tools, Scenarios, and MCP servers.
- Draft → publish → Worker acknowledgement → activation is an explicit rollout state machine; failed rollouts retain the previous revision.
- Scenarios support intent routing, field validation, clarification nodes and edges, capability bindings, and execution policies.
- A main coordinator can route to a scenario, ask configured follow-ups, or create a parallel Task Graph without hard-coding a business application into the core runtime.
- AgentTeams freeze member revisions and compile typed produce/review/revise/synthesis steps into the durable Task Graph. Workflow revisions can compose Agents, frozen Team or fixed-Scenario child Runs, verification, branches, bounded loops, and human approvals without introducing a second execution engine.

### Capability and security governance

- One Capability Registry for Tools, Skills, Connectors, and MCP capabilities with allowlists, permissions, quotas, and input validation.
- Shell execution is Docker-only and fails closed when isolation is unavailable.
- Files, Memory, Knowledge, and Artifacts are isolated by `user_id + agent_id + root_run_id`; a Worker filesystem is never the shared source of truth.
- Provider, database, Channel, and external-service credentials are environment or `env://VARIABLE` references, never plaintext JSON or logs.

### Observability, audit, and explainability

Every request creates a resumable Run timeline linking parent/child Runs, Tasks, Workers, model calls, Tool Invocations, logs, spans, and artifacts. The console exposes queue wait, claim latency, first-token time, tool latency, tokens, cost, retries, cache hits, errors, routing decisions, follow-ups, and child Agents.

Privileged diagnostics may inspect reasoning/thinking blocks actually returned by a provider. The platform distinguishes `provider_native/exact`, `model_declared/normalized`, `runtime_decision`, and `unavailable`; it never claims to expose hidden model state that a provider did not return. Raw payloads and reasoning blobs are permission-gated and reads are audited.

### Replay and improvement

Offline, frozen, branch, and live replays support incident analysis, deterministic comparison, and controlled re-execution. Versioned Eval datasets, deterministic scorers, and exact Agent/Scenario/Capability release gates prevent a failing revision from activating. Model caching reuses only equivalent requests and still records Invocation, Span, and audit data.

### From artifacts to shareable work

Run Artifacts can enter an immutable Work version chain. Owners explicitly choose private, unlisted, or public visibility and manage classification, published versions, collaborators, revocable/expiring version-pinned links, and access audit. Producing an Artifact never publishes personal data automatically.

### Distributed execution

```text
Client ── HTTP / SSE ──▶ FastAPI API replicas
                              │
                              ▼
                         PostgreSQL
                    ┌─────────┼─────────┐
                    ▼         ▼         ▼
              Agent Worker  Scheduler  Channel Worker
```

- PostgreSQL is the only runtime source of truth; SQLite is not supported.
- Workers use leases, fencing versions, `FOR UPDATE SKIP LOCKED`, and PostgreSQL `LISTEN/NOTIFY` rather than in-process queues.
- APIs authenticate, submit, and query; models and tools execute in Workers.
- Top-level Runs in one session are serialized; users, sessions, and child tasks can run concurrently.
- Redis is optional acceleration only and cannot replace the PostgreSQL state machine.

### One public execution path

Versioned HTTP + SSE is the public application protocol. Chat, schedules, Channel ingress, multi-Agent DAGs, and MCP `tools/call` all enter the same Run/Task pipeline; there is no second RPC or MCP execution engine.

## Identity and permissions

The current core model deliberately has no `tenant_id`. Resource ownership is expressed by the authenticated `user_id`; a session is `user_id + agent_id + session_id`. Agents, Skills, Tools, and child-Agent capacity are shared platform capabilities. Platform administrators are stored separately in `platform_admins`.

Production uses database-issued Bearer Tokens, storing only SHA-256 token fingerprints. `X-User-ID` is development-only. Permissions are operation-scoped (`runs.read`, `runs.cancel`, `agents.publish`, `reasoning.read_raw`, `replay.execute`) and administrative actions create audit events.

## Business integration and code boundaries

```text
api / bootstrap / channel adapters
                ↓
            application
                ↓
       runtime + domain services
                ↓
       module-owned PostgreSQL repositories
```

Business applications keep their own UI, identity, billing, domain rules, and database. They integrate through versioned HTTP/SSE, the App SDK, and Remote Capability rather than adding business code to the `joyhousebot` core package. The Runtime freezes Durable Action identities for external writes and records receipts and governed Artifacts on the shared execution chain.

## Start locally

PostgreSQL is required:

```bash
cp config.dev.json config.json
export LLM_PROVIDER="openrouter"
export LLM_API_KEY="your-key"
export JOYHOUSE_DATABASE_URL="postgresql://joyhousebot:joyhousebot-dev@127.0.0.1:15432/joyhousebot"
./scripts/start-local.sh
```

`config.dev.json` enables `allowInsecureAuth` (the `X-User-ID` header alone authenticates); it is for local development only — never expose it. `config.example.json` is the production-safe baseline template.

Open `http://127.0.0.1:18790/ui/`; OpenAPI is at `/docs`, and health endpoints are `/healthz` and `/readyz`. `config.json` is ignored by Git; never commit real credentials.

Docker Compose is also available:

```bash
export LLM_PROVIDER="openrouter"
export LLM_API_KEY="your-key"
export LLM_MODEL="openrouter/openai/gpt-4.1-mini"
export POSTGRES_PASSWORD="choose-a-strong-password"
export JOYHOUSEBOT_METRICS_TOKEN="choose-a-scrape-token"
export JOYHOUSEBOT_AUTH_ENCRYPTION_KEY="$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n')"
uv sync
docker compose -f docker-compose.runtime.yml up --build
```

Store `JOYHOUSEBOT_AUTH_ENCRYPTION_KEY` in secret management and retain it: it encrypts control-plane TOTP secrets, and losing it prevents recovery of enrolled authenticators.

Compose starts two API roles: `api` (public data plane, 18790) and `control` (admin plane and console UI, bound to `127.0.0.1:18791` by default — do not expose it publicly).

See [Architecture](docs/ARCHITECTURE.md) and [Operations](docs/OPERATIONS.md) for deployment, migrations, Worker roles, and incident handling.

## Submit a Run

```bash
curl -X POST http://127.0.0.1:18790/v1/runs \
  -H 'Content-Type: application/json' \
  -H 'X-User-ID: joyhousebot' \
  -d '{"execution":{"mode":"agent","agent_id":"main-coordinator"},"session_id":"demo","input":{"content":"Analyze this task"}}'
```

Use a database-issued Bearer Token in production.

## Verify

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check joyhousebot tests
cd apps/console && npm run build
```

## License

Joyhousebot is released under the Apache License 2.0. Commercial use is permitted; redistributions must retain the license, copyright notices, and comply with the Apache 2.0 patent and NOTICE terms.
