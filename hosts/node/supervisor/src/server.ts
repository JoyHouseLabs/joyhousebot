import {createServer, type IncomingMessage, type Server, type ServerResponse} from "node:http";

import {
  canonicalJson,
  NonceReplayGuard,
  readBoundedBody,
  requestMetadata,
  sha256Hex,
  signResponseBody,
  validateInvocationIdentity,
  verifySignedRequest,
  type InvocationRequest,
  type SignedRequestMetadata,
} from "@joyhousebot/extension-sdk";

import {NodeExtensionSupervisor} from "./supervisor.js";

export interface HostServerOptions {
  keyId: string;
  signingSecret: string;
  modelGatewayBaseUrl?: string;
  toolBrokerBaseUrl?: string;
}

export function createHostServer(
  supervisor: NodeExtensionSupervisor,
  options: HostServerOptions,
): Server {
  if (Buffer.byteLength(options.signingSecret, "utf8") < 32) {
    throw new Error("host signing secret must contain at least 32 UTF-8 bytes");
  }
  const nonceGuard = new NonceReplayGuard();
  const base = supervisor.config.listen.base_path.replace(/\/$/, "");
  const manifestDigest = `sha256:${sha256Hex(canonicalJson(supervisor.manifest))}`;
  return createServer((request, response) => {
    void handle(request, response).catch((error: unknown) => {
      sendJson(response, 500, {
        protocol_version: "1",
        status: "failed",
        error: {code: "HOST_INTERNAL_ERROR", message: safeMessage(error), retryable: true},
      }, "unknown", options.signingSecret);
    });
  });

  async function handle(request: IncomingMessage, response: ServerResponse): Promise<void> {
    if (request.method === "GET" && request.url === "/healthz") {
      sendJson(response, 200, {status: "ok"}, "healthz", options.signingSecret);
      return;
    }
    if (request.method !== "POST") {
      sendJson(response, 405, {error: {code: "METHOD_NOT_ALLOWED"}}, "unknown", options.signingSecret);
      return;
    }
    let nonce = "unknown";
    try {
      const rawBody = await readBoundedBody(request);
      const metadata = requestMetadata(request);
      nonce = metadata.nonce;
      verifySignedRequest({
        metadata,
        rawBody,
        secret: options.signingSecret,
        expectedKeyId: options.keyId,
        nonceGuard,
      });
      const envelope = parseObject(rawBody);
      const {payload, runtimeContext} = unwrapEnvelope(envelope, options);
      const path = request.url ?? "";
      let result: Record<string, unknown>;
      if (path === `${base}/meta:describe`) {
        result = {
          protocol_version: "1",
          status: "succeeded",
          manifest: supervisor.manifest,
          manifest_digest: manifestDigest,
          runtime: {language: "node", version: process.version},
        };
      } else if (
        path.startsWith(`${base}/capabilities/`)
        && /\/capabilities\/[^/]+:invoke$/.test(path)
      ) {
        const pathCapability = decodeURIComponent(path.slice(path.lastIndexOf("/") + 1, -7));
        const capability = payload.capability as Record<string, unknown> | undefined;
        if (String(capability?.capability_id ?? "") !== pathCapability) {
          throw new Error("capability path identity mismatch");
        }
        validateInvocationIdentity(payload as unknown as InvocationRequest, metadata);
        result = await supervisor.invoke(withRuntimeContext(payload, runtimeContext));
      } else if (path === `${base}/operations:reconcile`) {
        validateExecutionHeaders(payload, metadata);
        result = await supervisor.reconcile(withRuntimeContext(payload, runtimeContext));
      } else if (
        path.startsWith(`${base}/operations/`)
        && /\/operations\/[^/]+:command$/.test(path)
      ) {
        validateOperationPath(path, ":command", payload);
        validateExecutionHeaders(payload, metadata);
        result = await supervisor.command(payload);
      } else if (
        path.startsWith(`${base}/operations/`)
        && /\/operations\/[^/]+:cancel$/.test(path)
      ) {
        validateOperationPath(path, ":cancel", payload);
        validateExecutionHeaders(payload, metadata);
        result = await supervisor.cancel(payload);
      } else {
        sendJson(response, 404, {error: {code: "ENDPOINT_NOT_FOUND"}}, nonce, options.signingSecret);
        return;
      }
      const status = result.status === "accepted" ? 202 : 200;
      sendJson(response, status, result, nonce, options.signingSecret);
    } catch (error) {
      sendJson(response, 400, {
        protocol_version: "1",
        status: "failed",
        error: {code: "HOST_REQUEST_REJECTED", message: safeMessage(error), retryable: false},
      }, nonce, options.signingSecret);
    }
  }
}

function unwrapEnvelope(
  value: Record<string, unknown>,
  options: HostServerOptions,
): {payload: Record<string, unknown>; runtimeContext?: Record<string, unknown>} {
  if (!("invocation" in value) && !("runtime_context" in value)) {
    return {payload: value};
  }
  const payload = value.invocation;
  const runtimeContext = value.runtime_context;
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("signed Host envelope invocation is invalid");
  }
  if (!runtimeContext || typeof runtimeContext !== "object" || Array.isArray(runtimeContext)) {
    throw new Error("signed Host envelope runtime_context is invalid");
  }
  const context = runtimeContext as Record<string, unknown>;
  const modelToken = String(context.model_grant_token ?? "");
  const gateway = String(context.model_gateway_base_url ?? "").replace(/\/$/, "");
  const toolToken = String(context.tool_grant_token ?? "");
  const broker = String(context.tool_broker_base_url ?? "").replace(/\/$/, "");
  if (modelToken || gateway) {
    if (!modelToken.startsWith("jhm_") || modelToken.length < 40) {
      throw new Error("Host model grant token is invalid");
    }
    if (!options.modelGatewayBaseUrl || gateway !== options.modelGatewayBaseUrl) {
      throw new Error("Host model gateway URL is not allowlisted");
    }
  }
  if (toolToken || broker) {
    if (!toolToken.startsWith("jht_") || toolToken.length < 40) {
      throw new Error("Host Tool grant token is invalid");
    }
    if (!options.toolBrokerBaseUrl || broker !== options.toolBrokerBaseUrl) {
      throw new Error("Host Tool Broker URL is not allowlisted");
    }
  }
  if (!modelToken && !toolToken) {
    throw new Error("Host runtime context contains no scoped grant");
  }
  const allowed = [
    "model_gateway_base_url",
    "model_grant_token",
    "tool_broker_base_url",
    "tool_grant_token",
  ];
  if (Object.keys(context).some((key) => !allowed.includes(key))) {
    throw new Error("Host runtime context contains unsupported fields");
  }
  return {payload: payload as Record<string, unknown>, runtimeContext: context};
}

function withRuntimeContext(
  payload: Record<string, unknown>,
  runtimeContext?: Record<string, unknown>,
): Record<string, unknown> {
  return runtimeContext ? {...payload, runtime_context: runtimeContext} : payload;
}

function parseObject(rawBody: Buffer): Record<string, unknown> {
  const value: unknown = JSON.parse(rawBody.toString("utf8"));
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("request body must be an object");
  }
  return value as Record<string, unknown>;
}

function sendJson(
  response: ServerResponse,
  statusCode: number,
  value: Record<string, unknown>,
  nonce: string,
  secret: string,
): void {
  if (response.headersSent) return;
  const body = Buffer.from(canonicalJson(value), "utf8");
  response.writeHead(statusCode, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": String(body.length),
    "X-Joyhouse-Response-Signature": signResponseBody({
      statusCode,
      nonce,
      body,
      secret,
    }),
  });
  response.end(body);
}

function safeMessage(error: unknown): string {
  const message = error instanceof Error ? error.message : "host request failed";
  return message.replace(/[\r\n]+/g, " ").slice(0, 500);
}

function validateExecutionHeaders(
  payload: Record<string, unknown>,
  metadata: SignedRequestMetadata,
): void {
  const execution = payload.execution as Record<string, unknown> | undefined;
  if (String(execution?.idempotency_key ?? "") !== metadata.idempotencyKey) {
    throw new Error("idempotency header does not match body");
  }
  const actionId = execution?.action_id ? String(execution.action_id) : undefined;
  if (actionId !== metadata.actionId) {
    throw new Error("action header does not match body");
  }
}

function validateOperationPath(
  path: string,
  suffix: string,
  payload: Record<string, unknown>,
): void {
  const pathOperation = decodeURIComponent(path.slice(path.lastIndexOf("/") + 1, -suffix.length));
  const operation = payload.operation as Record<string, unknown> | undefined;
  if (String(operation?.operation_id ?? "") !== pathOperation) {
    throw new Error("operation path identity mismatch");
  }
}
