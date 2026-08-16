import {randomUUID} from "node:crypto";
import {createServer, type IncomingMessage, type ServerResponse} from "node:http";

import {
  canonicalJson,
  NonceReplayGuard,
  ProtocolError,
  readBoundedBody,
  requestMetadata,
  sha256Hex,
  signResponseBody,
  validateInvocationIdentity,
  verifySignedRequest,
  type InvocationExecution,
  type InvocationRequest,
  type CapabilityIdentity,
  type ExtensionHostManifest,
  type OperationProgressEvent,
  type ReconcileRequest,
  type RemoteCapabilityResponse,
  type SignedRequestMetadata,
} from "@joyhousebot/extension-sdk";

const KEY_ID = process.env.ECHO_HOST_KEY_ID ?? "echo-test-key";
const SECRET = process.env.ECHO_HOST_SIGNING_SECRET ?? "";
const PORT = Number.parseInt(process.env.ECHO_HOST_PORT ?? "9019", 10);
const BASE_PATH = "/joyhousebot/v1";
const ECHO_DIGEST = `sha256:${"1".repeat(64)}`;
const DELAYED_ECHO_DIGEST = `sha256:${"5".repeat(64)}`;
const nonceGuard = new NonceReplayGuard();

function capabilityManifest(
  capabilityId: string,
  digest: string,
): CapabilityIdentity & Record<string, unknown> {
  return {
    capability_id: capabilityId,
    version: "1.0.0",
    implementation_digest: digest,
    name: capabilityId,
    description: `Contract fixture ${capabilityId}`,
    input_schema: {type: "object", additionalProperties: true},
    output_schema: {type: "object"},
    permissions: [`${capabilityId}.invoke`],
    tags: [],
    side_effect: "read",
    idempotent: true,
    retryable: true,
    data_classification: "confidential",
    timeout_seconds: 60,
    expected_duration_seconds: 10,
    invocation_concurrency: "parallel_safe",
    max_concurrent_invocations: 1,
    cost_policy: {},
    execution_mode: "immediate",
    supports_stream: false,
    provenance: {},
  };
}

const HOST_MANIFEST = {
  host_protocol_version: "1",
  host: {
    host_id: "joyhousebot-node-echo-host",
    version: "0.1.0",
    build_digest: `sha256:${"2".repeat(64)}`,
  },
  extensions: [{
    extension_id: "capability-echo-fixture",
    version: "1.0.0",
    build_digest: `sha256:${"3".repeat(64)}`,
    lockfile_digest: `sha256:${"4".repeat(64)}`,
    sdk_version: "1",
  }],
  capabilities: [
    capabilityManifest("host.echo", ECHO_DIGEST),
    capabilityManifest("host.delayed_echo", DELAYED_ECHO_DIGEST),
  ],
} satisfies ExtensionHostManifest;
const HOST_MANIFEST_DIGEST = `sha256:${sha256Hex(canonicalJson(HOST_MANIFEST))}`;

interface StoredOperation {
  operationId: string;
  capabilityId: string;
  requestDigest: string;
  idempotencyKey: string;
  userId: string;
  actionId: string | null;
  status: "pending" | "succeeded";
  output: unknown;
  events: OperationProgressEvent[];
}

const operationsById = new Map<string, StoredOperation>();
const operationsByIdentity = new Map<string, StoredOperation>();

function assertConfiguration(): void {
  if (!Number.isInteger(PORT) || PORT < 0 || PORT > 65_535) {
    throw new Error("ECHO_HOST_PORT must be a valid TCP port");
  }
  if (Buffer.byteLength(SECRET, "utf8") < 32) {
    throw new Error("ECHO_HOST_SIGNING_SECRET must contain at least 32 UTF-8 bytes");
  }
}

function identityKey(request: InvocationRequest): string {
  return [
    request.subject.user_id,
    request.capability.capability_id,
    request.execution.idempotency_key,
  ].join("\u0000");
}

function sendJson(
  response: ServerResponse,
  statusCode: number,
  value: RemoteCapabilityResponse | Record<string, unknown>,
  nonce: string,
): void {
  const body = Buffer.from(canonicalJson(value), "utf8");
  response.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": String(body.length),
    "X-Joyhouse-Response-Signature": signResponseBody({
      statusCode,
      nonce,
      body,
      secret: SECRET,
    }),
  });
  response.end(body);
}

function sendError(
  response: ServerResponse,
  error: unknown,
  nonce: string,
): void {
  const protocolError = error instanceof ProtocolError
    ? error
    : new ProtocolError("INTERNAL_ERROR", "echo host failed", 500, true);
  sendJson(
    response,
    protocolError.httpStatus,
    {
      protocol_version: "1",
      status: "failed",
      summary: protocolError.message,
      error: {
        code: protocolError.code,
        message: protocolError.message,
        retryable: protocolError.retryable,
      },
    },
    nonce,
  );
}

function parseJson<T>(rawBody: Buffer): T {
  try {
    const value: unknown = JSON.parse(rawBody.toString("utf8"));
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new TypeError("body must be an object");
    }
    return value as T;
  } catch {
    throw new ProtocolError("REQUEST_INVALID", "request body is invalid JSON", 400);
  }
}

function verifyRequest(
  request: IncomingMessage,
  rawBody: Buffer,
): SignedRequestMetadata {
  const metadata = requestMetadata(request);
  verifySignedRequest({
    metadata,
    rawBody,
    secret: SECRET,
    expectedKeyId: KEY_ID,
    nonceGuard,
  });
  return metadata;
}

function validateCapability(request: InvocationRequest, expectedId: string): void {
  const expectedDigest = expectedId === "host.echo" ? ECHO_DIGEST : DELAYED_ECHO_DIGEST;
  if (
    request.protocol_version !== "1"
    || request.capability.capability_id !== expectedId
    || request.capability.version !== "1.0.0"
    || request.capability.implementation_digest !== expectedDigest
  ) {
    throw new ProtocolError("CAPABILITY_MISMATCH", "capability identity is not published", 409);
  }
  if (!request.authorization.permissions.includes(`${expectedId}.invoke`)) {
    throw new ProtocolError("PERMISSION_DENIED", "required capability permission is missing", 403);
  }
}

function validateReconcileIdentity(
  request: ReconcileRequest,
  metadata: SignedRequestMetadata,
  operation: StoredOperation,
): void {
  const execution: InvocationExecution = request.execution;
  if (metadata.idempotencyKey !== execution.idempotency_key) {
    throw new ProtocolError("IDEMPOTENCY_HEADER_MISMATCH", "idempotency identity changed", 409);
  }
  if ((metadata.actionId ?? null) !== execution.action_id) {
    throw new ProtocolError("ACTION_HEADER_MISMATCH", "action identity changed", 409);
  }
  if (
    operation.capabilityId !== request.capability.capability_id
    || operation.requestDigest !== execution.request_digest
    || operation.idempotencyKey !== execution.idempotency_key
    || operation.userId !== request.subject.user_id
    || operation.actionId !== execution.action_id
  ) {
    throw new ProtocolError("OPERATION_IDENTITY_MISMATCH", "operation identity changed", 409);
  }
}

function operationResponse(
  operation: StoredOperation,
  cursor?: string,
): RemoteCapabilityResponse {
  const parsedCursor = cursor === undefined ? -1 : Number.parseInt(cursor, 10);
  const cursorReset = cursor !== undefined && !Number.isInteger(parsedCursor);
  const after = cursorReset ? -1 : parsedCursor;
  const events = operation.events.filter((event) => event.sequence > after).slice(0, 100);
  const latestSequence = events.at(-1)?.sequence ?? after;
  const observation = {
    provider_cursor: String(latestSequence),
    checkpoint_ref: `echo-checkpoint:${operation.operationId}`,
    progress_summary: operation.status === "pending"
      ? "delayed echo is pending"
      : "delayed echo completed",
    progress_percent: operation.status === "pending" ? 10 : 100,
    events,
    ...(cursorReset ? {cursor_reset: true} : {}),
  };
  if (operation.status === "pending") {
    return {
      protocol_version: "1",
      status: "pending",
      summary: "delayed echo is pending",
      operation: {operation_id: operation.operationId},
      retry_after_seconds: 1,
      ...observation,
    };
  }
  return {
    protocol_version: "1",
    status: "succeeded",
    summary: "delayed echo completed",
    operation: {operation_id: operation.operationId},
    output: operation.output,
    artifacts: [],
    ...observation,
  };
}

function invokeResponse(operation: StoredOperation): RemoteCapabilityResponse {
  if (operation.capabilityId === "host.echo" || operation.status === "succeeded") {
    return {
      protocol_version: "1",
      status: "succeeded",
      summary: operation.capabilityId === "host.echo" ? "echoed" : "delayed echo completed",
      output: operation.output,
      artifacts: [],
    };
  }
  return {
    protocol_version: "1",
    status: "accepted",
    summary: "delayed echo accepted",
    operation: {operation_id: operation.operationId},
  };
}

function invoke(
  request: InvocationRequest,
  metadata: SignedRequestMetadata,
  expectedId: string,
): {statusCode: number; body: RemoteCapabilityResponse} {
  validateInvocationIdentity(request, metadata);
  validateCapability(request, expectedId);
  const key = identityKey(request);
  const existing = operationsByIdentity.get(key);
  if (existing) {
    if (existing.requestDigest !== request.execution.request_digest) {
      throw new ProtocolError(
        "IDEMPOTENCY_CONFLICT",
        "idempotency key was already used for another request",
        409,
      );
    }
    const body = invokeResponse(existing);
    return {statusCode: body.status === "accepted" ? 202 : 200, body};
  }

  const operation: StoredOperation = {
    operationId: `echo_${randomUUID()}`,
    capabilityId: expectedId,
    requestDigest: request.execution.request_digest,
    idempotencyKey: request.execution.idempotency_key,
    userId: request.subject.user_id,
    actionId: request.execution.action_id,
    status: expectedId === "host.echo" ? "succeeded" : "pending",
    output: request.input,
    events: [{
      event_id: "accepted",
      sequence: 0,
      event_type: "operation.accepted",
      summary: "delayed echo accepted",
      payload: {},
      created_at: new Date().toISOString(),
    }],
  };
  operationsByIdentity.set(key, operation);
  operationsById.set(operation.operationId, operation);
  if (expectedId === "host.delayed_echo") {
    const delay = typeof request.input === "object" && request.input !== null
      ? Number((request.input as Record<string, unknown>).delay_ms ?? 50)
      : 50;
    setTimeout(() => {
      operation.status = "succeeded";
      operation.events.push({
        event_id: "completed",
        sequence: 1,
        event_type: "operation.completed",
        summary: "delayed echo completed",
        payload: {},
        created_at: new Date().toISOString(),
      });
    }, Math.min(5_000, Math.max(10, delay))).unref();
  }
  const body = invokeResponse(operation);
  return {statusCode: body.status === "accepted" ? 202 : 200, body};
}

async function handle(request: IncomingMessage, response: ServerResponse): Promise<void> {
  if (request.method === "GET" && request.url === "/healthz") {
    sendJson(response, 200, {status: "ok"}, "healthz");
    return;
  }
  if (request.method !== "POST") {
    response.writeHead(405, {"Content-Type": "text/plain"});
    response.end("method not allowed");
    return;
  }

  let nonce = "unknown";
  try {
    const rawBody = await readBoundedBody(request);
    const metadata = verifyRequest(request, rawBody);
    nonce = metadata.nonce;
    const path = request.url ?? "";
    if (path === `${BASE_PATH}/meta:describe`) {
      const body = parseJson<Record<string, unknown>>(rawBody);
      if (body.protocol_version !== "1") {
        throw new ProtocolError("PROTOCOL_MISMATCH", "unsupported host protocol", 400);
      }
      sendJson(response, 200, {
        protocol_version: "1",
        status: "succeeded",
        manifest: HOST_MANIFEST,
        manifest_digest: HOST_MANIFEST_DIGEST,
        runtime: {language: "node", version: process.version},
      }, nonce);
      return;
    }
    if (path === `${BASE_PATH}/capabilities/host.echo:invoke`) {
      const result = invoke(parseJson<InvocationRequest>(rawBody), metadata, "host.echo");
      sendJson(response, result.statusCode, result.body, nonce);
      return;
    }
    if (path === `${BASE_PATH}/capabilities/host.delayed_echo:invoke`) {
      const result = invoke(
        parseJson<InvocationRequest>(rawBody),
        metadata,
        "host.delayed_echo",
      );
      sendJson(response, result.statusCode, result.body, nonce);
      return;
    }
    if (path === `${BASE_PATH}/operations:reconcile`) {
      const body = parseJson<ReconcileRequest>(rawBody);
      const operation = operationsById.get(body.operation.operation_id);
      if (!operation) {
        sendJson(
          response,
          200,
          {protocol_version: "1", status: "unknown", summary: "operation is unknown"},
          nonce,
        );
        return;
      }
      validateReconcileIdentity(body, metadata, operation);
      sendJson(
        response,
        200,
        operationResponse(operation, body.operation.cursor),
        nonce,
      );
      return;
    }
    throw new ProtocolError("ENDPOINT_NOT_FOUND", "endpoint is not published", 404);
  } catch (error) {
    sendError(response, error, nonce);
  }
}

assertConfiguration();
const server = createServer((request, response) => {
  void handle(request, response);
});
server.listen(PORT, "127.0.0.1", () => {
  const address = server.address();
  const boundPort = typeof address === "object" && address ? address.port : PORT;
  process.stdout.write(`${canonicalJson({
    event: "ready",
    port: boundPort,
    manifest_digest: HOST_MANIFEST_DIGEST,
  })}\n`);
});

function shutdown(): void {
  server.close(() => process.exit(0));
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
