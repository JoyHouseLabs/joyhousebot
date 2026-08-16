# `@joyhousebot/extension-sdk`

Node.js SDK for implementing isolated JoyhouseBot Extension Host processes. It provides protocol types,
RFC 8785 request digests, Remote Capability HMAC helpers, bounded body parsing, replay protection and
request identity validation.

SDK 同时定义 Invocation、Channel Driver、Event Source 的 Manifest 身份，并提供
`HostToolBrokerClient`。Tool Broker 客户端只能使用 Runtime 签发的短期 `jht_` token，不接收 Device
token、数据库连接或 Runtime 内部对象。

`ExtensionHostManifest`, `HostDescribeRequest` and `HostDescribeResponse` define the signed
`POST /meta:describe` publication/preflight contract. Host packages must report exact Host, Extension,
lockfile and Capability identities; the Runtime independently recomputes the RFC 8785 manifest digest before
acknowledging a Remote Connection rollout.

The SDK does not submit Runs, access PostgreSQL or bypass Runtime Capability governance. See
[`docs/EXTENSION_HOST_PROTOCOL.md`](../../docs/EXTENSION_HOST_PROTOCOL.md) for the authoritative contract.

From the repository root, run `./scripts/test-extension-host.sh` to install locked Node dependencies, verify
the shared vectors, build the Echo Host fixture and execute the Python-to-Node integration tests.

The SDK supports contributor environments on Node 20 or newer. Distributed Hosts do not use a global Node;
they use the exact LTS binary pinned in [`hosts/node/runtime-lock.json`](../../hosts/node/runtime-lock.json).
