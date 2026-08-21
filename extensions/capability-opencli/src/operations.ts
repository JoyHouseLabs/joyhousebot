import {createHash} from "node:crypto";
import {mkdir, readFile, rename, writeFile} from "node:fs/promises";
import {dirname} from "node:path";

import type {OperationProgressEvent} from "@joyhousebot/extension-sdk";

import type {CapturedArtifact} from "./capture.js";

import type {OpenCliExecutionResult} from "./runner.js";

export type StoredOperationStatus =
  | "running"
  | "succeeded"
  | "retryable"
  | "manual_required"
  | "failed"
  | "cancelled"
  | "interrupted";

export interface StoredOperation {
  operation_id: string;
  identity_key: string;
  capability_id: string;
  request_digest: string;
  user_id: string;
  action_id: string | null;
  idempotency_key: string;
  input: Record<string, unknown>;
  access: "read" | "write";
  status: StoredOperationStatus;
  attempt: number;
  output?: unknown;
  artifacts?: CapturedArtifact[];
  error?: OpenCliExecutionResult["error"];
  events: OperationProgressEvent[];
  created_at: string;
  updated_at: string;
}

const MAX_OPERATIONS = 100_000;
const MAX_EVENTS = 1_000;

export class OpenCliOperationStore {
  readonly path: string;
  #byId = new Map<string, StoredOperation>();
  #byIdentity = new Map<string, StoredOperation>();
  #tail: Promise<void> = Promise.resolve();

  constructor(path: string) {
    this.path = path;
  }

  async load(): Promise<void> {
    try {
      const value: unknown = JSON.parse(await readFile(this.path, "utf8"));
      if (!Array.isArray(value) || value.length > MAX_OPERATIONS) {
        throw new Error("OpenCLI operation store is invalid or exceeds retention policy");
      }
      for (const item of value as StoredOperation[]) {
        validateStoredOperation(item);
        if (item.status === "running") item.status = "interrupted";
        this.#byId.set(item.operation_id, item);
        this.#byIdentity.set(item.identity_key, item);
      }
      await this.persist();
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
  }

  get(operationId: string): StoredOperation | undefined {
    return this.#byId.get(operationId);
  }

  findIdentity(identityKey: string): StoredOperation | undefined {
    return this.#byIdentity.get(identityKey);
  }

  async create(value: Omit<StoredOperation, "operation_id" | "events" | "created_at" | "updated_at" | "attempt" | "status">): Promise<StoredOperation> {
    const existing = this.#byIdentity.get(value.identity_key);
    if (existing) return existing;
    if (this.#byId.size >= MAX_OPERATIONS) throw new Error("OpenCLI operation retention limit reached");
    const now = new Date().toISOString();
    const operation: StoredOperation = {
      ...value,
      operation_id: operationId(value.identity_key),
      status: "running",
      attempt: 0,
      events: [],
      created_at: now,
      updated_at: now,
    };
    this.#byId.set(operation.operation_id, operation);
    this.#byIdentity.set(operation.identity_key, operation);
    this.event(operation, "opencli.accepted", "OpenCLI command accepted");
    await this.persist();
    return operation;
  }

  event(operation: StoredOperation, event_type: string, summary: string, payload: Record<string, unknown> = {}): void {
    const sequence = operation.events.at(-1)?.sequence === undefined
      ? 0
      : operation.events.at(-1)!.sequence + 1;
    operation.events.push({
      event_id: `${operation.operation_id}:${sequence}`,
      sequence,
      event_type,
      summary: summary.slice(0, 1_000),
      payload,
      created_at: new Date().toISOString(),
    });
    if (operation.events.length > MAX_EVENTS) operation.events.splice(0, operation.events.length - MAX_EVENTS);
    operation.updated_at = new Date().toISOString();
  }

  persist(): Promise<void> {
    this.#tail = this.#tail.then(async () => {
      await mkdir(dirname(this.path), {recursive: true});
      const temporary = `${this.path}.tmp`;
      await writeFile(temporary, JSON.stringify([...this.#byId.values()]), {mode: 0o600});
      await rename(temporary, this.path);
    });
    return this.#tail;
  }
}

export function operationIdentity(value: {
  userId: string;
  capabilityId: string;
  idempotencyKey: string;
}): string {
  return [value.userId, value.capabilityId, value.idempotencyKey].join("\u0000");
}

function operationId(identity: string): string {
  return `opencli_${createHash("sha256").update(identity).digest("hex").slice(0, 32)}`;
}

function validateStoredOperation(item: StoredOperation): void {
  if (!item.operation_id || !item.identity_key || !item.capability_id || !item.request_digest) {
    throw new Error("OpenCLI operation identity is invalid");
  }
  if (!Array.isArray(item.events) || item.events.length > MAX_EVENTS) {
    throw new Error("OpenCLI operation events exceed retention policy");
  }
}
