# Joyhousebot

## Governance for enterprise Agent applications

Joyhousebot is not a single-agent chat client and not a model-vendor SDK. It governs the concerns that appear when an Agent application enters real business workflows: identity and permissions, capability access, version rollout, distributed execution, recovery, auditability, replay, cost, and performance.

It provides one PostgreSQL-first control plane and runtime for building, publishing, operating, and governing many Agent applications.

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

### Capability and security governance

- One Capability Registry for Tools, Skills, Connectors, and MCP capabilities with allowlists, permissions, quotas, and input validation.
- Shell execution is Docker-only and fails closed when isolation is unavailable.
- Files, Memory, Knowledge, and Artifacts are isolated by `user_id + agent_id + root_run_id`; a Worker filesystem is never the shared source of truth.
- Provider, database, Channel, and external-service credentials are environment or `env://VARIABLE` references, never plaintext JSON or logs.

### Observability, audit, and explainability

Every request creates a resumable Run timeline linking parent/child Runs, Tasks, Workers, model calls, Tool Invocations, logs, spans, and artifacts. The console exposes queue wait, claim latency, first-token time, tool latency, tokens, cost, retries, cache hits, errors, routing decisions, follow-ups, and child Agents.

Privileged diagnostics may inspect reasoning/thinking blocks actually returned by a provider. The platform distinguishes `provider_native/exact`, `model_declared/normalized`, `runtime_decision`, and `unavailable`; it never claims to expose hidden model state that a provider did not return. Raw payloads and reasoning blobs are permission-gated and reads are audited.

### Replay and improvement

Offline, frozen, branch, and live replays support incident analysis, deterministic comparison, and controlled re-execution. Model caching reuses only equivalent requests and still records Invocation, Span, and audit data.

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
       dedicated PostgreSQL repositories
```

Business applications such as Dinq Discover should register Scenarios, Capabilities, Tools, Skills, or MCP servers through an independent plugin package rather than adding business code to the `joyhousebot` core package.

## Start locally

PostgreSQL is required:

```bash
cp config.dev.json config.json
export LLM_PROVIDER="openrouter"
export LLM_API_KEY="your-key"
export JOYHOUSEBOT_DATABASE_URL="postgresql://joyhousebot:password@127.0.0.1:5432/joyhousebot"
./scripts/start-local.sh
```

`config.dev.json` enables `allowInsecureAuth` (the `X-User-ID` header alone authenticates); it is for local development only — never expose it. `config.example.json` is the production-safe baseline template.

Open `http://127.0.0.1:18790/ui/`; OpenAPI is at `/docs`, and health endpoints are `/healthz` and `/readyz`. `config.json` is ignored by Git; never commit real credentials.

Docker Compose is also available:

```bash
export LLM_PROVIDER="openrouter"
export LLM_API_KEY="your-key"
export POSTGRES_PASSWORD="choose-a-strong-password"
export JOYHOUSEBOT_METRICS_TOKEN="choose-a-scrape-token"
uv sync
docker compose -f docker-compose.runtime.yml up --build
```

Compose starts two API roles: `api` (public data plane, 18790) and `control` (admin plane and console UI, bound to `127.0.0.1:18791` by default — do not expose it publicly).

See [Architecture](docs/ARCHITECTURE.md) and [Operations](docs/OPERATIONS.md) for deployment, migrations, Worker roles, and incident handling.

## Submit a Run

```bash
curl -X POST http://127.0.0.1:18790/v1/runs \
  -H 'Content-Type: application/json' \
  -H 'X-User-ID: local-dev' \
  -d '{"agent_id":"main-coordinator","session_id":"demo","input":{"content":"Analyze this task"}}'
```

Use a database-issued Bearer Token in production.

## Verify

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check joyhousebot tests
cd frontend && npm run build
```

## License

Joyhousebot is released under the Apache License 2.0. Commercial use is permitted; redistributions must retain the license, copyright notices, and comply with the Apache 2.0 patent and NOTICE terms.
