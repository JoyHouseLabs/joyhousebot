import {createHash, randomUUID} from "node:crypto";
import {realpath} from "node:fs/promises";
import {isAbsolute, resolve} from "node:path";

import {runPi} from "./pi-rpc.js";
import {PiOperationStore} from "./store.js";
import type {PiOperation, PiRuntimeContext, WorkspaceDefinition} from "./types.js";
import {createWorktree, gitPatch, runTests} from "./workspace.js";

const CAPABILITY_ID = "coding.pi.execute";
const CAPABILITY_VERSION = "1.0.0";
const IMPLEMENTATION_DIGEST = process.env.PI_IMPLEMENTATION_DIGEST ?? "";
const STATE_PATH = requiredPath("PI_STATE_PATH");
const WORKSPACE_ROOT = requiredPath("PI_WORKSPACE_ROOT");
const PI_ENTRYPOINT = requiredPath("PI_ENTRYPOINT");
const WORKSPACES = parseWorkspaces(process.env.PI_WORKSPACES_JSON ?? "");
const MAX_CONCURRENCY = integer(process.env.PI_MAX_CONCURRENCY ?? "1", 1, 4);
const store = new PiOperationStore(STATE_PATH);
const active = new Map<string, AbortController>();

await verifyRuntime();
await store.load();

let inputBuffer = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk: string) => {
  inputBuffer += chunk;
  for (;;) {
    const newline = inputBuffer.indexOf("\n");
    if (newline < 0) break;
    const line = inputBuffer.slice(0, newline).replace(/\r$/, "");
    inputBuffer = inputBuffer.slice(newline + 1);
    if (line) void handleLine(line);
  }
});

async function handleLine(line: string): Promise<void> {
  let request: {id: string; type: string; payload?: Record<string, unknown>} | undefined;
  try {
    request = JSON.parse(line) as Exclude<typeof request, undefined>;
    if (!request?.id || !request.type) throw new Error("worker request identity is invalid");
    let payload: Record<string, unknown>;
    if (request.type === "health") payload = {status: "ok", active: active.size};
    else if (request.type === "invoke") payload = await invoke(request.payload ?? {});
    else if (request.type === "reconcile") payload = await reconcile(request.payload ?? {});
    else if (request.type === "cancel") payload = await cancel(request.payload ?? {});
    else throw new Error("unsupported Pi worker request");
    send({id: request.id, type: "result", payload});
  } catch (error) {
    send({
      id: request?.id ?? "unknown",
      type: "error",
      error: {code: "PI_WORKER_ERROR", message: safeMessage(error), retryable: false},
    });
  }
}

async function invoke(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  const capability = object(payload.capability, "capability");
  const execution = object(payload.execution, "execution");
  const subject = object(payload.subject, "subject");
  if (
    capability.capability_id !== CAPABILITY_ID
    || capability.version !== CAPABILITY_VERSION
    || capability.implementation_digest !== IMPLEMENTATION_DIGEST
  ) throw new Error("Pi capability version identity mismatch");
  const input = parseInput(payload.input);
  const context = runtimeContext(payload.runtime_context);
  const modelPolicy = object(object(payload.authorization, "authorization").model_access, "model_access");
  const modelId = bounded(modelPolicy.model_id, "model_id", 256);
  const contextWindow = number(modelPolicy.context_window, "context_window", 1, 10_000_000);
  const maxOutputTokens = number(modelPolicy.max_output_tokens, "max_output_tokens", 1, 1_000_000);
  const userId = bounded(subject.user_id, "user_id", 256);
  const idempotencyKey = bounded(execution.idempotency_key, "idempotency_key", 512);
  const requestDigest = String(execution.request_digest ?? "");
  if (!/^sha256:[0-9a-f]{64}$/.test(requestDigest)) throw new Error("request_digest is invalid");
  const identity = createHash("sha256")
    .update(`${userId}\0${CAPABILITY_ID}\0${idempotencyKey}`)
    .digest("hex");
  const existing = store.findIdentity(identity);
  if (existing) {
    if (existing.request_digest !== requestDigest) throw new Error("Pi idempotency identity conflict");
    return observation(existing);
  }
  if (active.size >= MAX_CONCURRENCY) return failed("PI_CONCURRENCY_LIMIT", "Pi runner is busy", true);
  const operationId = `pi_${randomUUID().replaceAll("-", "")}`;
  const definition = WORKSPACES[input.workspace_ref];
  if (!definition) throw new Error("workspace_ref is not installed on this Host");
  const workspacePath = await createWorktree(
    definition,
    input.revision,
    WORKSPACE_ROOT,
    operationId,
  );
  const now = new Date().toISOString();
  const operation: PiOperation = {
    operation_id: operationId,
    identity_key: identity,
    request_digest: requestDigest,
    capability_id: CAPABILITY_ID,
    user_id: userId,
    input,
    status: "running",
    workspace_path: workspacePath,
    events: [],
    created_at: now,
    updated_at: now,
  };
  store.event(operation, "pi.started", "Pi started in an isolated worktree");
  store.set(operation);
  await store.persist();
  start(operation, definition, modelId, contextWindow, maxOutputTokens, context);
  return accepted(operation);
}

async function reconcile(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  const operationValue = object(payload.operation, "operation");
  const operation = store.get(String(operationValue.operation_id ?? ""));
  if (!operation) return {protocol_version: "1", status: "unknown", summary: "Pi operation is unknown"};
  return observation(operation, Number(operationValue.cursor ?? -1));
}

async function cancel(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
  const operationValue = object(payload.operation, "operation");
  const operation = store.get(String(operationValue.operation_id ?? ""));
  if (!operation) return {protocol_version: "1", status: "unknown", summary: "Pi operation is unknown"};
  active.get(operation.operation_id)?.abort();
  operation.status = "cancelled";
  operation.error = {code: "PI_CANCELLED", message: "Pi operation was cancelled", retryable: false};
  store.event(operation, "pi.cancelled", operation.error.message);
  await store.persist();
  return observation(operation);
}

function start(
  operation: PiOperation,
  definition: WorkspaceDefinition,
  modelId: string,
  contextWindow: number,
  maxOutputTokens: number,
  context: PiRuntimeContext,
): void {
  const controller = new AbortController();
  active.set(operation.operation_id, controller);
  void execute(
    operation,
    definition,
    modelId,
    contextWindow,
    maxOutputTokens,
    context,
    controller.signal,
  )
    .catch(async (error) => {
      if (operation.status === "cancelled") return;
      operation.status = "failed";
      operation.error = {code: "PI_EXECUTION_FAILED", message: safeMessage(error), retryable: false};
      store.event(operation, "pi.failed", operation.error.message);
      await store.persist();
    })
    .finally(() => active.delete(operation.operation_id));
}

async function execute(
  operation: PiOperation,
  definition: WorkspaceDefinition,
  modelId: string,
  contextWindow: number,
  maxOutputTokens: number,
  context: PiRuntimeContext,
  signal: AbortSignal,
): Promise<void> {
  const pi = await runPi({
    entrypoint: PI_ENTRYPOINT,
    workspace: operation.workspace_path,
    stateRoot: resolve(WORKSPACE_ROOT, `${operation.operation_id}-state`),
    instruction: `${operation.input.instruction}\n\nDo not commit, publish, deploy, or access external systems.`,
    modelId,
    contextWindow,
    maxOutputTokens,
    runtimeContext: context,
    timeoutMs: 30 * 60_000,
    signal,
  });
  if (signal.aborted) throw new Error("Pi operation was cancelled");
  const patch = await gitPatch(operation.workspace_path);
  let tests: Record<string, unknown> | null = null;
  if (operation.input.test_profile) {
    const profile = definition.tests[operation.input.test_profile];
    if (!profile) throw new Error("test_profile is not installed for this workspace");
    tests = await runTests(operation.workspace_path, profile);
  }
  operation.status = "succeeded";
  operation.output = {
    patch,
    patch_sha256: createHash("sha256").update(patch).digest("hex"),
    tests,
    agent_summary: pi.summary,
    pi_event_count: pi.event_count,
    workspace_ref: operation.input.workspace_ref,
    revision: operation.input.revision,
    applied: false,
  };
  store.event(operation, "pi.succeeded", "Pi produced a reviewable patch and evidence");
  await store.persist();
}

function accepted(operation: PiOperation): Record<string, unknown> {
  return {
    protocol_version: "1",
    status: "accepted",
    operation: {operation_id: operation.operation_id},
    provider_cursor: String(operation.events.length - 1),
    retry_after_seconds: 1,
  };
}

function observation(operation: PiOperation, cursor = -1): Record<string, unknown> {
  const events = operation.events.filter((item) => Number(item.sequence) > cursor);
  const base = {
    protocol_version: "1",
    operation: {operation_id: operation.operation_id},
    provider_cursor: String(operation.events.length - 1),
    events,
  };
  if (operation.status === "running") return {...base, status: "pending", retry_after_seconds: 1};
  if (operation.status === "succeeded") return {...base, status: "succeeded", output: operation.output};
  if (operation.status === "manual_required") {
    return {...base, status: "unknown", summary: operation.error?.message, error: operation.error};
  }
  return {...base, status: "failed", summary: operation.error?.message, error: operation.error};
}

function parseInput(value: unknown): PiOperation["input"] {
  const input = object(value, "input");
  const unknown = Object.keys(input).filter(
    (key) => !["workspace_ref", "revision", "instruction", "test_profile"].includes(key),
  );
  if (unknown.length) throw new Error(`Pi input contains unknown fields: ${unknown.join(", ")}`);
  return {
    workspace_ref: bounded(input.workspace_ref, "workspace_ref", 128),
    revision: bounded(input.revision, "revision", 64),
    instruction: bounded(input.instruction, "instruction", 32_768),
    ...(input.test_profile ? {test_profile: bounded(input.test_profile, "test_profile", 128)} : {}),
  };
}

function runtimeContext(value: unknown): PiRuntimeContext {
  const context = object(value, "runtime_context");
  const token = bounded(context.model_grant_token, "model_grant_token", 512);
  const base = new URL(bounded(context.model_gateway_base_url, "model_gateway_base_url", 2048));
  if (!token.startsWith("jhm_")) throw new Error("model grant token is invalid");
  if (!["127.0.0.1", "::1", "localhost"].includes(base.hostname) || base.protocol !== "http:") {
    throw new Error("Pi pilot model gateway must be loopback HTTP");
  }
  const toolToken = context.tool_grant_token
    ? bounded(context.tool_grant_token, "tool_grant_token", 512)
    : undefined;
  const toolBase = context.tool_broker_base_url
    ? new URL(bounded(context.tool_broker_base_url, "tool_broker_base_url", 2048))
    : undefined;
  if (Boolean(toolToken) !== Boolean(toolBase)) {
    throw new Error("tool broker URL and grant token must be provided together");
  }
  if (toolToken && !toolToken.startsWith("jht_")) {
    throw new Error("tool grant token is invalid");
  }
  if (
    toolBase
    && toolBase.protocol !== "https:"
    && !(toolBase.protocol === "http:" && ["127.0.0.1", "::1", "localhost"].includes(toolBase.hostname))
  ) {
    throw new Error("tool broker requires HTTPS; HTTP is loopback-only");
  }
  return {
    model_grant_token: token,
    model_gateway_base_url: base.toString().replace(/\/$/, ""),
    ...(toolToken && toolBase
      ? {
          tool_grant_token: toolToken,
          tool_broker_base_url: toolBase.toString().replace(/\/$/, ""),
        }
      : {}),
  };
}

function parseWorkspaces(raw: string): Record<string, WorkspaceDefinition> {
  const parsed = JSON.parse(raw || "{}") as Record<string, WorkspaceDefinition>;
  for (const [key, value] of Object.entries(parsed)) {
    if (!/^[A-Za-z0-9_.:-]{1,128}$/.test(key) || !value || typeof value !== "object") {
      throw new Error("PI_WORKSPACES_JSON contains an invalid workspace");
    }
    if (!isAbsolute(value.repository) || !value.tests || typeof value.tests !== "object") {
      throw new Error(`workspace ${key} is invalid`);
    }
    for (const profile of Object.values(value.tests)) {
      if (!profile.command || !Array.isArray(profile.args)) throw new Error(`workspace ${key} test profile is invalid`);
    }
  }
  return parsed;
}

async function verifyRuntime(): Promise<void> {
  if (process.version !== "v24.19.0") throw new Error("Pi runner requires packaged Node v24.19.0");
  await Promise.all([STATE_PATH, WORKSPACE_ROOT, PI_ENTRYPOINT].map(async (path) => {
    const target = await realpath(path).catch(() => path);
    if (!isAbsolute(target)) throw new Error("Pi runner paths must be absolute");
  }));
  if (!/^sha256:[0-9a-f]{64}$/.test(IMPLEMENTATION_DIGEST)) {
    throw new Error("PI_IMPLEMENTATION_DIGEST is required");
  }
}

function object(value: unknown, field: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${field} must be an object`);
  return value as Record<string, unknown>;
}
function bounded(value: unknown, field: string, max: number): string {
  const text = String(value ?? "").trim();
  if (!text || Buffer.byteLength(text, "utf8") > max || text.includes("\0")) throw new Error(`${field} is invalid`);
  return text;
}
function requiredPath(name: string): string {
  const value = process.env[name] ?? "";
  if (!isAbsolute(value)) throw new Error(`${name} must be an absolute path`);
  return value;
}
function integer(value: string, min: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < min || parsed > max) throw new Error("integer config is invalid");
  return parsed;
}
function number(value: unknown, field: string, min: number, max: number): number {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < min || parsed > max) throw new Error(`${field} is invalid`);
  return parsed;
}
function safeMessage(error: unknown): string {
  return (error instanceof Error ? error.message : "Pi execution failed").replace(/[\r\n]+/g, " ").slice(0, 500);
}
function failed(code: string, message: string, retryable: boolean): Record<string, unknown> {
  return {protocol_version: "1", status: "failed", error: {code, message, retryable}};
}
function send(value: Record<string, unknown>): void { process.stdout.write(`${JSON.stringify(value)}\n`); }
