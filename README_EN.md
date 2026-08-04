# Joyhousebot Cloud

Joyhousebot is a distributed Agent runtime for multi-user cloud services. It provides a FastAPI HTTP/SSE gateway, durable Run/Task state machines, multi-Agent DAG execution, and independently scalable Worker, Scheduler, and Channel Worker roles. The runtime is implemented in this repository and does not depend on an external Agent SDK.

There is no tenant abstraction. Authentication resolves a `user_id`; a conversation is uniquely identified by `user_id + agent_id + session_id`. Agents, skills, tools, and child-Agent capacity are shared platform capabilities, while user state remains isolated.

## Architecture

```text
Browser / API client
       │ HTTP + SSE
       ▼
 FastAPI replicas ──────────────┐
                                ▼
                           PostgreSQL
                 ┌──────────────┼──────────────┐
                 ▼              ▼              ▼
            Agent Worker    Scheduler     Channel Worker
```

- `/v1` HTTP and SSE are the only public application protocols.
- API replicas authenticate, submit, and query; model and tool execution happens in Workers.
- PostgreSQL is the only runtime source of truth in development and production; SQLite is not supported.
- Runs, tasks, events, logs, artifacts, memory, knowledge, schedules, channel delivery, and provider health are durable.
- Normal clients receive structured progress. Privileged diagnostics can inspect exact model requests/responses and reasoning blocks actually returned by the provider.
- Shell execution is Docker-only and fails closed when isolation is unavailable.
- `/mcp/` is a Streamable HTTP protocol adapter; MCP tool calls still create the same durable Run/Task and do not introduce a second execution runtime.

See [Architecture](docs/ARCHITECTURE.md), [Operations](docs/OPERATIONS.md), and the [Development Plan](docs/DEVELOPMENT_PLAN.md).

## Start locally

```bash
export LLM_PROVIDER='anthropic'
export LLM_API_KEY='your-key'
uv sync
docker compose -f docker-compose.runtime.yml up --build
```

To run roles against an existing PostgreSQL database:

```bash
export JOYHOUSEBOT_DATABASE_URL='postgresql://joyhousebot:password@127.0.0.1:5432/joyhousebot'
uv run joyhousebot check
uv run joyhousebot api --surface combined --port 18790
uv run joyhousebot worker
uv run joyhousebot scheduler
uv run joyhousebot channel-worker
```

OpenAPI is at `http://127.0.0.1:18790/docs`; the built-in UI is at `http://127.0.0.1:18790/ui/`.

Channel connectors are visible under “Configuration → Channels” in the console. Their credentials remain environment or `env://VARIABLE` references; database-backed hot editing is planned, not currently implemented.

## Submit a run

```bash
curl -X POST http://127.0.0.1:18790/v1/runs \
  -H 'Content-Type: application/json' \
  -H 'X-User-ID: local-user' \
  -d '{"agent_id":"joy","session_id":"demo","input":{"content":"Analyze this task"}}'
```

`X-User-ID` is accepted only in explicit development mode. Production deployments issue database-backed Bearer tokens; only token hashes are retained.

## License

Joyhousebot is released under the Apache License 2.0. Commercial use is permitted; redistributions must retain the license, copyright notices, and comply with the Apache 2.0 patent and NOTICE terms.

## Verify

```bash
.venv/bin/python -m pytest
.venv/bin/ruff check joyhousebot tests
cd frontend && npm run build
```
