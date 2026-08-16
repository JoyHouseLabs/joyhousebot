import {mkdir, readFile, rename, writeFile} from "node:fs/promises";
import {dirname} from "node:path";

import type {PiOperation} from "./types.js";

export class PiOperationStore {
  readonly #path: string;
  readonly #operations = new Map<string, PiOperation>();

  constructor(path: string) {
    this.#path = path;
  }

  async load(): Promise<void> {
    let parsed: {operations?: PiOperation[]};
    try {
      parsed = JSON.parse(await readFile(this.#path, "utf8")) as {operations?: PiOperation[]};
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
      throw error;
    }
    for (const operation of parsed.operations ?? []) {
      if (operation.status === "running") {
        operation.status = "manual_required";
        operation.error = {
          code: "PI_PROCESS_RESTARTED",
          message: "Pi process state was lost; inspect the retained worktree before retrying",
          retryable: false,
        };
        this.event(operation, "pi.manual_required", operation.error.message);
      }
      this.#operations.set(operation.operation_id, operation);
    }
    await this.persist();
  }

  values(): PiOperation[] { return [...this.#operations.values()]; }
  get(id: string): PiOperation | undefined { return this.#operations.get(id); }
  findIdentity(identity: string): PiOperation | undefined {
    return this.values().find((item) => item.identity_key === identity);
  }
  set(operation: PiOperation): void { this.#operations.set(operation.operation_id, operation); }

  event(
    operation: PiOperation,
    type: string,
    summary: string,
    payload: Record<string, unknown> = {},
  ): void {
    operation.events.push({
      event_id: `${operation.operation_id}:${operation.events.length}`,
      sequence: operation.events.length,
      event_type: type,
      summary: summary.slice(0, 500),
      payload,
    });
    operation.updated_at = new Date().toISOString();
  }

  async persist(): Promise<void> {
    await mkdir(dirname(this.#path), {recursive: true, mode: 0o700});
    const temporary = `${this.#path}.${process.pid}.tmp`;
    await writeFile(temporary, JSON.stringify({schema_version: 1, operations: this.values()}), {mode: 0o600});
    await rename(temporary, this.#path);
  }
}
