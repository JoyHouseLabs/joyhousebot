import {createHash} from "node:crypto";
import {mkdir, readFile, realpath} from "node:fs/promises";
import {dirname, isAbsolute, relative, resolve} from "node:path";
import {createInterface} from "node:readline";

import type {OperationProgressEvent} from "@porthouse/extension-sdk";

import {loadCompiledCatalog} from "./catalog.js";
import {captureMarkdownArtifact, type CapturedArtifact} from "./capture.js";
import {
  OpenCliOperationStore,
  operationIdentity,
  type StoredOperation,
} from "./operations.js";
import {runOpenCli, type RunningOpenCli} from "./runner.js";
import type {CompiledCatalog, CompiledCommand} from "./types.js";

interface IpcRequest {
  id: string;
  type: "health" | "invoke" | "reconcile" | "command" | "cancel";
  payload?: Record<string, unknown>;
}

const MAX_IPC_FRAME_BYTES = 1_048_576;
const MAX_STDOUT_BYTES = integerEnv("OPENCLI_MAX_STDOUT_BYTES", 1_048_576, 1_024, 16_777_216);
const MAX_STDERR_BYTES = integerEnv("OPENCLI_MAX_STDERR_BYTES", 65_536, 1_024, 1_048_576);
const MAX_ACTIVE = integerEnv("OPENCLI_MAX_CONCURRENCY", 2, 1, 16);
const CATALOG_PATH = requiredEnv("OPENCLI_CATALOG_PATH");
const STATE_PATH = requiredEnv("OPENCLI_STATE_PATH");
const WORKSPACE_ROOT = requiredEnv("OPENCLI_WORKSPACE_ROOT");
const OPENCLI_ENTRYPOINT = requiredEnv("OPENCLI_ENTRYPOINT");
const OPENCLI_PACKAGE_JSON = requiredEnv("OPENCLI_PACKAGE_JSON");

class OpenCliExtensionWorker {
  readonly catalog: CompiledCatalog;
  readonly catalogDigest: string;
  readonly store: OpenCliOperationStore;
  readonly commands = new Map<string, CompiledCommand>();
  readonly active = new Map<string, RunningOpenCli>();
  readonly entrypoint: string;

  private constructor(catalog: CompiledCatalog, digest: string, entrypoint: string) {
    this.catalog = catalog;
    this.catalogDigest = digest;
    this.entrypoint = entrypoint;
    this.store = new OpenCliOperationStore(STATE_PATH);
    for (const command of catalog.commands) this.commands.set(command.capability.capability_id, command);
  }

  static async create(): Promise<OpenCliExtensionWorker> {
    const catalogBytes = await readFile(CATALOG_PATH);
    const catalog = loadCompiledCatalog(JSON.parse(catalogBytes.toString("utf8")));
    if (process.version !== catalog.runtime.node_version) {
      throw new Error(
        `OpenCLI Extension requires exact Node ${catalog.runtime.node_version}; received ${process.version}`,
      );
    }
    const entrypoint = await realpath(OPENCLI_ENTRYPOINT);
    const entrypointDigest = createHash("sha256").update(await readFile(entrypoint)).digest("hex");
    if (entrypointDigest !== catalog.runtime.opencli_entrypoint_sha256) {
      throw new Error("OpenCLI entrypoint digest does not match the frozen catalog");
    }
    const packageJsonPath = await realpath(OPENCLI_PACKAGE_JSON);
    const packageRoot = dirname(packageJsonPath);
    const escaped = relative(packageRoot, entrypoint);
    if (escaped.startsWith("..") || isAbsolute(escaped)) {
      throw new Error("OpenCLI entrypoint must be inside the verified package root");
    }
    const packageIdentity = JSON.parse(await readFile(packageJsonPath, "utf8")) as Record<string, unknown>;
    if (
      packageIdentity.name !== "@jackwener/opencli"
      || packageIdentity.version !== catalog.runtime.opencli_version
    ) {
      throw new Error("OpenCLI installed package identity does not match the frozen catalog");
    }
    const worker = new OpenCliExtensionWorker(
      catalog,
      `sha256:${createHash("sha256").update(catalogBytes).digest("hex")}`,
      entrypoint,
    );
    await mkdir(WORKSPACE_ROOT, {recursive: true, mode: 0o700});
    await worker.store.load();
    return worker;
  }

  async handle(request: IpcRequest): Promise<Record<string, unknown>> {
    if (request.type === "health") {
      return {
        status: "ok",
        extension_id: this.catalog.extension.extension_id,
        extension_version: this.catalog.extension.version,
        catalog_digest: this.catalogDigest,
        opencli_version: this.catalog.runtime.opencli_version,
        node_version: process.version,
        capabilities: this.catalog.commands.length,
        active_operations: this.active.size,
      };
    }
    const payload = request.payload ?? {};
    if (request.type === "invoke") return this.invoke(payload);
    if (request.type === "reconcile") return this.reconcile(payload);
    if (request.type === "command") return this.command(payload);
    if (request.type === "cancel") return this.cancel(payload);
    throw new Error("unsupported OpenCLI worker request");
  }

  async invoke(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    const capability = objectField(payload, "capability");
    const execution = objectField(payload, "execution");
    const subject = objectField(payload, "subject");
    const capabilityId = String(capability.capability_id ?? "");
    const command = this.commands.get(capabilityId);
    if (!command) throw new Error("OpenCLI capability is not in the frozen catalog");
    if (
      String(capability.version ?? "") !== command.capability.version
      || String(capability.implementation_digest ?? "") !== command.capability.implementation_digest
    ) {
      throw new Error("OpenCLI capability version identity mismatch");
    }
    const userId = String(subject.user_id ?? "");
    const idempotencyKey = String(execution.idempotency_key ?? "");
    const requestDigest = String(execution.request_digest ?? "");
    if (!userId || !idempotencyKey || !/^sha256:[0-9a-f]{64}$/.test(requestDigest)) {
      throw new Error("OpenCLI invocation identity is incomplete");
    }
    const identityKey = operationIdentity({userId, capabilityId, idempotencyKey});
    const existing = this.store.findIdentity(identityKey);
    if (existing) {
      if (existing.request_digest !== requestDigest) throw new Error("OpenCLI idempotency identity conflict");
      return this.accepted(existing);
    }
    if (this.active.size >= MAX_ACTIVE) {
      return failed("OPENCLI_CONCURRENCY_LIMIT", "OpenCLI Extension is at its concurrency limit", true);
    }
    const rawInput = payload.input;
    if (!rawInput || typeof rawInput !== "object" || Array.isArray(rawInput)) {
      throw new Error("OpenCLI invocation input must be an object");
    }
    const operation = await this.store.create({
      identity_key: identityKey,
      capability_id: capabilityId,
      request_digest: requestDigest,
      user_id: userId,
      action_id: execution.action_id ? String(execution.action_id) : null,
      idempotency_key: idempotencyKey,
      input: rawInput as Record<string, unknown>,
      access: command.access,
    });
    await this.start(operation, command);
    return this.accepted(operation);
  }

  async reconcile(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    const operationValue = objectField(payload, "operation");
    const operation = this.store.get(String(operationValue.operation_id ?? ""));
    if (!operation) return {protocol_version: "1", status: "unknown", summary: "OpenCLI operation is unknown"};
    const command = this.commands.get(operation.capability_id);
    if (!command) return {protocol_version: "1", status: "unknown", summary: "OpenCLI capability is no longer deployed"};
    if (
      !this.active.has(operation.operation_id)
      && (operation.status === "retryable" || (operation.status === "interrupted" && operation.access === "read"))
    ) {
      if (operation.attempt >= 3) {
        operation.status = "failed";
        operation.error = {
          code: "OPENCLI_RETRY_EXHAUSTED",
          message: "OpenCLI retry limit was reached",
          retryable: false,
          exit_code: null,
        };
        this.store.event(operation, "opencli.failed", operation.error.message);
        await this.store.persist();
      } else {
        await this.start(operation, command);
      }
    }
    return this.observation(operation, operationValue.cursor);
  }

  async command(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    const operationValue = objectField(payload, "operation");
    const operation = this.store.get(String(operationValue.operation_id ?? ""));
    if (!operation) return {protocol_version: "1", status: "unknown", summary: "OpenCLI operation is unknown"};
    const commandValue = objectField(payload, "command", false);
    if (String(commandValue.name ?? "") !== "resume") {
      throw new Error("OpenCLI operation only supports the resume command");
    }
    if (!new Set(["manual_required", "interrupted", "retryable"]).has(operation.status)) {
      return this.observation(operation, operationValue.cursor);
    }
    const command = this.commands.get(operation.capability_id);
    if (!command) throw new Error("OpenCLI capability is no longer deployed");
    if (this.active.size >= MAX_ACTIVE) {
      return failed("OPENCLI_CONCURRENCY_LIMIT", "OpenCLI Extension is at its concurrency limit", true);
    }
    await this.start(operation, command);
    return this.observation(operation, operationValue.cursor);
  }

  async cancel(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    const operationValue = objectField(payload, "operation");
    const operation = this.store.get(String(operationValue.operation_id ?? ""));
    if (!operation) return {protocol_version: "1", status: "unknown", summary: "OpenCLI operation is unknown"};
    this.active.get(operation.operation_id)?.child.kill("SIGTERM");
    operation.status = "cancelled";
    operation.error = {code: "OPENCLI_CANCELLED", message: "OpenCLI operation was cancelled", retryable: false, exit_code: 130};
    this.store.event(operation, "opencli.cancelled", operation.error.message);
    await this.store.persist();
    return this.observation(operation, operationValue.cursor);
  }

  async start(operation: StoredOperation, command: CompiledCommand): Promise<void> {
    if (this.active.has(operation.operation_id)) return;
    operation.status = "running";
    operation.attempt += 1;
    operation.error = undefined;
    this.store.event(operation, "opencli.started", "OpenCLI command started", {attempt: operation.attempt});
    await this.store.persist();
    const workspace = resolve(WORKSPACE_ROOT, operation.operation_id);
    await mkdir(workspace, {recursive: true, mode: 0o700});
    let running: RunningOpenCli;
    try {
      running = runOpenCli({
        entrypoint: this.entrypoint,
        command,
        input: operation.input,
        workspace,
        timeoutMs: command.capability.timeout_seconds * 1_000,
        maxStdoutBytes: MAX_STDOUT_BYTES,
        maxStderrBytes: MAX_STDERR_BYTES,
      });
    } catch (error) {
      operation.status = "failed";
      operation.error = {code: "OPENCLI_INPUT_INVALID", message: safeMessage(error), retryable: false, exit_code: null};
      this.store.event(operation, "opencli.failed", operation.error.message);
      await this.store.persist();
      return;
    }
    this.active.set(operation.operation_id, running);
    void running.result.then(async (result) => {
      if (operation.status === "cancelled") return;
      operation.status = result.state;
      operation.output = result.output;
      operation.error = result.error;
      try {
        operation.artifacts = result.state === "succeeded" && command.capture_output_markdown
          ? await this.captureArtifacts(operation, command)
          : [];
      } catch (error) {
        operation.status = "failed";
        operation.error = {
          code: "OPENCLI_CAPTURE_FAILED",
          message: safeMessage(error),
          retryable: false,
          exit_code: null,
        };
      }
      if (result.state === "retryable" && operation.access === "write") {
        operation.status = "manual_required";
        operation.error = {
          code: "OPENCLI_WRITE_OUTCOME_UNKNOWN",
          message: "OpenCLI write may have crossed the side-effect boundary; review before retrying",
          retryable: false,
          exit_code: result.error?.exit_code ?? null,
        };
      }
      const eventType = operation.status === "succeeded"
        ? "opencli.succeeded"
        : operation.status === "manual_required"
          ? "opencli.needs_user"
          : operation.status === "retryable"
            ? "opencli.retryable"
            : operation.status === "cancelled"
              ? "opencli.cancelled"
              : "opencli.failed";
      this.store.event(
        operation,
        eventType,
        operation.status === "succeeded" ? "OpenCLI command completed" : operation.error?.message ?? "OpenCLI command failed",
        {attempt: operation.attempt, exit_code: operation.error?.exit_code ?? 0},
      );
      await this.store.persist();
    }).catch(async () => {
      operation.status = operation.access === "read" ? "retryable" : "manual_required";
      operation.error = {
        code: "OPENCLI_RUNNER_FAILED",
        message: "OpenCLI runner failed before producing a durable result",
        retryable: operation.access === "read",
        exit_code: null,
      };
      this.store.event(operation, "opencli.failed", operation.error.message);
      await this.store.persist();
    }).finally(() => this.active.delete(operation.operation_id));
  }

  async captureArtifacts(
    operation: StoredOperation,
    command: CompiledCommand,
  ): Promise<CapturedArtifact[]> {
    const workspace = resolve(WORKSPACE_ROOT, operation.operation_id);
    return captureMarkdownArtifact({
      workspace,
      operationId: operation.operation_id,
      capabilityId: command.capability.capability_id,
      sourceUrl: typeof operation.input.url === "string" ? operation.input.url : null,
    });
  }

  accepted(operation: StoredOperation): Record<string, unknown> {
    return {
      protocol_version: "1",
      status: "accepted",
      summary: "OpenCLI command accepted",
      operation: {operation_id: operation.operation_id},
      ...(operation.access === "write" ? {
        write_receipt: {action_id: operation.action_id, idempotency_key: operation.idempotency_key},
      } : {}),
    };
  }

  observation(operation: StoredOperation, rawCursor: unknown): Record<string, unknown> {
    const cursor = rawCursor === undefined ? -1 : Number(rawCursor);
    const minimum = operation.events.at(0)?.sequence ?? 0;
    const cursorReset = !Number.isInteger(cursor) || cursor < minimum - 1;
    const after = cursorReset ? minimum - 1 : cursor;
    const events = operation.events.filter((event) => event.sequence > after).slice(0, 100);
    const latest = events.at(-1)?.sequence ?? after;
    const common = {
      protocol_version: "1",
      operation: {operation_id: operation.operation_id},
      provider_cursor: String(latest),
      checkpoint_ref: `opencli:${operation.operation_id}:${operation.attempt}`,
      progress_summary: progressSummary(operation),
      progress_percent: operation.status === "succeeded" ? 100 : operation.status === "running" ? 25 : null,
      events: events as OperationProgressEvent[],
      ...(cursorReset ? {cursor_reset: true} : {}),
    };
    if (operation.status === "running" || operation.status === "retryable") {
      return {...common, status: "pending", summary: progressSummary(operation), retry_after_seconds: 2};
    }
    if (operation.status === "manual_required" || operation.status === "interrupted") {
      return {...common, status: "unknown", summary: operation.error?.message ?? "OpenCLI operation requires review"};
    }
    if (operation.status === "succeeded") {
      return {
        ...common,
        status: "succeeded",
        summary: "OpenCLI command completed",
        output: operation.output,
        artifacts: operation.artifacts ?? [],
      };
    }
    return {
      ...common,
      status: "failed",
      summary: operation.error?.message ?? "OpenCLI command failed",
      error: operation.error ?? {code: "OPENCLI_COMMAND_FAILED", message: "OpenCLI command failed", retryable: false},
    };
  }
}

async function main(): Promise<void> {
  const worker = await OpenCliExtensionWorker.create();
  const lines = createInterface({input: process.stdin, crlfDelay: Infinity});
  lines.on("line", (line) => {
    void handleLine(worker, line);
  });
}

async function handleLine(worker: OpenCliExtensionWorker, line: string): Promise<void> {
  let id = "unknown";
  try {
    if (Buffer.byteLength(line, "utf8") > MAX_IPC_FRAME_BYTES) throw new Error("OpenCLI IPC frame exceeds policy");
    const request = JSON.parse(line) as IpcRequest;
    id = String(request.id ?? "");
    if (!id) throw new Error("OpenCLI IPC request id is required");
    const payload = await worker.handle(request);
    reply({id, type: request.type === "health" ? "pong" : "result", payload});
  } catch (error) {
    reply({id, type: "error", error: {code: "OPENCLI_EXTENSION_ERROR", message: safeMessage(error), retryable: false}});
  }
}

function reply(value: Record<string, unknown>): void {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

function objectField(value: Record<string, unknown>, name: string, required = true): Record<string, unknown> {
  const item = value[name];
  if (!item || typeof item !== "object" || Array.isArray(item)) {
    if (!required) return {};
    throw new Error(`OpenCLI ${name} must be an object`);
  }
  return item as Record<string, unknown>;
}

function failed(code: string, message: string, retryable: boolean): Record<string, unknown> {
  return {protocol_version: "1", status: "failed", summary: message, error: {code, message, retryable}};
}

function progressSummary(operation: StoredOperation): string {
  if (operation.status === "running") return `OpenCLI command is running (attempt ${operation.attempt})`;
  if (operation.status === "retryable") return "OpenCLI command will be retried safely";
  return operation.error?.message ?? `OpenCLI operation is ${operation.status}`;
}

function requiredEnv(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function integerEnv(name: string, fallback: number, minimum: number, maximum: number): number {
  const raw = process.env[name];
  if (raw === undefined) return fallback;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < minimum || value > maximum) throw new Error(`${name} is invalid`);
  return value;
}

function safeMessage(error: unknown): string {
  return (error instanceof Error ? error.message : "OpenCLI Extension failed").replace(/[\r\n]+/g, " ").slice(0, 500);
}

void main().catch((error: unknown) => {
  process.stderr.write(`OpenCLI Extension failed to start: ${safeMessage(error)}\n`);
  process.exitCode = 1;
});
