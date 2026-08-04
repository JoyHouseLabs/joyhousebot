# MCP Capability Gateway

Joyhousebot exposes published executable capabilities through a Streamable HTTP
MCP endpoint at `/mcp`. The gateway is an adapter, not a second execution
runtime: every `tools/call` creates a durable one-node Run/Task graph through
the same PostgreSQL-backed runtime used by HTTP and Web UI requests.

## Endpoint

```text
https://<host>/mcp
```

Use a database API Token in the `Authorization: Bearer ...` header. In local
development, `allow_insecure_auth=true` permits the explicit `X-User-Id`
development identity; this must not be used for a public deployment.

## Tool names

Published `tool` and `connector` capabilities are exposed with a safe MCP name:

```text
dinq.talent.filter -> joy_dinq_talent_filter
```

The original capability id, version, schema, permissions and runtime settings
remain authoritative in the Capability Registry. Skills and workflows are not
presented as direct MCP tools unless they are published as executable tool or
connector capabilities.

## Execution semantics

An MCP call carries the caller's `user_id` and optional `X-Session-Id` and
`X-Agent-Id` headers into a durable graph task. The result includes `run_id`
and `task_id`. Calls that finish within the synchronous window return the
normalized result; long calls return `status=accepted` with polling and event
links:

```json
{
  "run_id": "run_...",
  "task_id": "mcp_...",
  "status": "accepted",
  "poll": "/v1/runs/run_...",
  "events": "/v1/runs/run_.../events"
}
```

MCP progress notifications are best-effort presentation; PostgreSQL Run and
event records remain the consistency source. Tool permissions, runtime
enablement, leases, retries, Trace and artifact retention are shared with
native Coordinator execution.

## Dinq Plugin contract

Dinq Plugin should publish one Capability Definition and one handler. The
native Joyhouse adapter and this MCP gateway consume that same definition and
handler. Business code must not call FastMCP directly; protocol-specific code
belongs in the plugin's adapter layer.
