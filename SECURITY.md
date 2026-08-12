# Security Policy

Report vulnerabilities through a private GitHub security advisory or directly to the maintainers. Do not publish credentials or exploitation details in a public issue.

## Production baseline

- Configure the shared `JOYHOUSE_DATABASE_URL` with a least-privilege PostgreSQL role and TLS as appropriate. The first integrated release uses one database for JoyHouse services; module repositories still restrict table ownership. Production never falls back to SQLite.
- Issue database-backed API tokens or configure an external OIDC adapter. Only token hashes are stored. Do not enable `gateway.allowInsecureAuth` in production.
- Treat operator tokens separately from user tokens. Operator impersonation must be explicit and audited.
- Inject model, channel, and MCP credentials through a secret manager or environment; never commit them to configuration files.
- Run API, Worker, Scheduler, and Channel Worker as separate non-root workloads with independent resource limits.
- Keep Worker hosts free of sensitive host files. Run-scoped files are isolated scratch data; persistent data belongs in PostgreSQL or the artifact store.
- Shell commands run only in Docker isolation. Keep container networking disabled unless a task explicitly requires it, pin images, and restrict mounted paths.
- Optional network and integration tools fail closed until named in `tools.optionalAllowlist`. Review MCP servers as privileged integrations.
- Restrict each Channel with its provider-specific sender allowlist and use one lease-owning Channel Worker per connection.
- Treat trace blobs, model prompts/responses, provider-returned reasoning, logs, and artifacts as sensitive user data. Normal Run/SSE APIs expose only structured summaries; grant `reasoning.read` and especially `reasoning.read_raw` only to trusted diagnostics operators, and audit every raw access.

## Credential response

If a secret may be compromised, revoke and rotate it immediately, inspect `runtime_logs`, `runtime_events`, approval history, channel delivery audit, and provider usage, then redeploy affected workloads. Preserve relevant audit records before cleanup.

## Dependency checks

Run Python and Node dependency auditing in CI, rebuild from the lockfiles, and update dependencies regularly. The optional WhatsApp extension keeps its Node sidecar lockfile under `extensions/channel-whatsapp/bridge/`.
