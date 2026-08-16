# Echo Host fixture

This test-only Node Host implements `host.echo` and `host.delayed_echo` over signed Remote Capability v1.
It keeps operations in memory, so a process restart deliberately makes unfinished operations `unknown`; it is
not a production Supervisor or a second durable Runtime.

Required environment variables:

- `ECHO_HOST_KEY_ID`
- `ECHO_HOST_SIGNING_SECRET` (at least 32 UTF-8 bytes)
- `ECHO_HOST_PORT` (defaults to `9019`)
