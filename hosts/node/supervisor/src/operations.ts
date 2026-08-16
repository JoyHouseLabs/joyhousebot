import {mkdir, readFile, rename, writeFile} from "node:fs/promises";
import {dirname} from "node:path";

import type {OperationBinding} from "./types.js";

const MAX_BINDINGS = 100_000;

export class OperationRegistry {
  readonly path: string;
  #items = new Map<string, OperationBinding>();
  #writeTail: Promise<void> = Promise.resolve();

  constructor(path: string) {
    this.path = path;
  }

  async load(): Promise<void> {
    try {
      const parsed: unknown = JSON.parse(await readFile(this.path, "utf8"));
      if (!Array.isArray(parsed) || parsed.length > MAX_BINDINGS) {
        throw new Error("operation registry is invalid or exceeds retention policy");
      }
      for (const value of parsed as OperationBinding[]) {
        if (!value.operation_id || !value.extension_id || !value.capability_id) {
          throw new Error("operation registry entry is invalid");
        }
        this.#items.set(value.operation_id, value);
      }
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") throw error;
    }
  }

  get(operationId: string): OperationBinding | undefined {
    return this.#items.get(operationId);
  }

  async bind(binding: OperationBinding): Promise<void> {
    const existing = this.#items.get(binding.operation_id);
    if (existing) {
      if (JSON.stringify(existing) !== JSON.stringify(binding)) {
        throw new Error("operation identity conflict");
      }
      return;
    }
    if (this.#items.size >= MAX_BINDINGS) {
      throw new Error("operation registry retention limit reached");
    }
    this.#items.set(binding.operation_id, binding);
    await this.#persist();
  }

  #persist(): Promise<void> {
    this.#writeTail = this.#writeTail.then(async () => {
      await mkdir(dirname(this.path), {recursive: true});
      const temporary = `${this.path}.tmp`;
      await writeFile(temporary, JSON.stringify([...this.#items.values()]), {mode: 0o600});
      await rename(temporary, this.path);
    });
    return this.#writeTail;
  }
}
