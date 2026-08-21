# joyhousebot Shell Capability

Optional command execution inside a fail-closed container sandbox. The extension
contributes the versioned `exec` capability; Core owns workspace isolation, command
policy enforcement, resource limits, output caps, and the Docker execution boundary.

The initial package permits only `container_network=none`.
