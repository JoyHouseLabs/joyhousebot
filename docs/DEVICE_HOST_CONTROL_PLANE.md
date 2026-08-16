# Device Host Control Plane

Porthouse Console is the advanced control plane for paired HappyHouse Desktop
devices. HappyHouse remains the ordinary-user product: it owns local payload
installation, Keychain storage, browser sessions and local file permissions;
it does not expose Node, Pi, OpenCLI or raw Host configuration in its normal
workflow.

## Boundary

`device_host_control_requests` is intentionally separate from
`device_operation_deliveries`.

- A delivery is a frozen Run/Action reconciliation and can affect business
  state.
- A control request is bounded device maintenance. It never creates a Run,
  Action, Artifact or Tool call and never bypasses capability approval.
- Console submits intent over the normal versioned HTTP API; the paired Device
  Host pulls it with its device token and returns a sanitised result.

There is no shell command, npm package name, URL or arbitrary environment
variable in the protocol.

## Fixed actions

The v1 allowlist is `preflight`, `diagnose_opencli`, `diagnose_pi`,
`enable_opencli`, `disable_opencli`, `enable_pi`, `disable_pi` and
`restart_host`.

Only `browser_profile_ref` and `workspace_ref` may be attached, and they are
references rather than browser cookies, filesystem paths or source code.
Every request has a durable status, fencing claim version, expiry and bounded
result (128 KiB). Stale or revoked device credentials cannot complete it.

`enable_*`, `disable_*` and `restart_host` are deliberately not interpreted as
host-shell instructions. A signed Desktop capability-profile manager must
apply the corresponding immutable bundle release and report its outcome. If
that manager is absent or the Desktop needs an update, the result is
`manual_required`; Porthouse never guesses success.

## Operator flow

1. HappyHouse starts and pairs its local Device Host using the current user
   identity; the device token stays in Keychain.
2. An operator opens **集成中心 → Device Hosts** in Porthouse Console.
3. Console submits a fixed diagnostic or deployment request and displays its
   durable status/history.
4. Desktop executes it when online, reporting only safe diagnostics such as
   Node version and local Host reachability.
5. Once a signed capability profile reports exact revisions, normal Agent
   capability binding and Run tests continue through the existing dispatcher,
   approval and reconciliation paths.

This keeps the Console useful for full-flow debugging while retaining a
small, auditable local attack surface.
