import {randomUUID} from "node:crypto";
import {spawn, type ChildProcessWithoutNullStreams} from "node:child_process";
import {resolve} from "node:path";

import type {InstalledExtension, IpcRequest, IpcResponse, ResourcePolicy} from "./types.js";

const DEFAULT_POLICY: ResourcePolicy = {
  request_timeout_ms: 60_000,
  startup_timeout_ms: 10_000,
  max_frame_bytes: 1_048_576,
  max_stderr_bytes: 65_536,
  max_crashes: 3,
  crash_window_seconds: 60,
};

interface PendingCall {
  resolve: (value: Record<string, unknown>) => void;
  reject: (error: Error) => void;
  timer: NodeJS.Timeout;
}

export class ExtensionProcess {
  readonly extension: InstalledExtension;
  readonly policy: ResourcePolicy;
  #child: ChildProcessWithoutNullStreams | null = null;
  #stdout = Buffer.alloc(0);
  #stderrBytes = 0;
  #pending = new Map<string, PendingCall>();
  #crashes: number[] = [];
  #starting: Promise<void> | null = null;

  constructor(extension: InstalledExtension) {
    this.extension = extension;
    this.policy = {...DEFAULT_POLICY, ...(extension.policy ?? {})};
  }

  async start(): Promise<void> {
    if (this.#child && !this.#child.killed) return;
    if (this.#starting) return this.#starting;
    this.#starting = this.#start();
    try {
      await this.#starting;
    } finally {
      this.#starting = null;
    }
  }

  async request(
    type: IpcRequest["type"],
    payload: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    await this.start();
    return this.#requestStarted(type, payload);
  }

  #requestStarted(
    type: IpcRequest["type"],
    payload: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const child = this.#child;
    if (!child || child.killed) throw new Error("extension process is unavailable");
    const id = randomUUID();
    const frame = Buffer.from(`${JSON.stringify({id, type, payload} satisfies IpcRequest)}\n`);
    if (frame.length > this.policy.max_frame_bytes) {
      throw new Error("extension request frame exceeds policy");
    }
    return new Promise((resolveValue, reject) => {
      const timer = setTimeout(() => {
        this.#pending.delete(id);
        this.#terminate("request timeout");
        reject(new Error(`extension ${this.extension.extension_id} request timed out`));
      }, this.policy.request_timeout_ms);
      timer.unref();
      this.#pending.set(id, {resolve: resolveValue, reject, timer});
      child.stdin.write(frame, (error) => {
        if (!error) return;
        const pending = this.#pending.get(id);
        if (!pending) return;
        clearTimeout(pending.timer);
        this.#pending.delete(id);
        reject(error);
      });
    });
  }

  async close(): Promise<void> {
    this.#terminate("supervisor shutdown");
  }

  async #start(): Promise<void> {
    if (this.extension.runner === "oci") {
      throw new Error("OCI runner requires the server deployment adapter");
    }
    const now = Date.now();
    const windowStart = now - this.policy.crash_window_seconds * 1000;
    this.#crashes = this.#crashes.filter((item) => item >= windowStart);
    if (this.#crashes.length >= this.policy.max_crashes) {
      throw new Error(`extension ${this.extension.extension_id} crash loop breaker is open`);
    }
    const environment = resolveEnvironment(this.extension.environment ?? {});
    const child = spawn(
      process.execPath,
      [resolve(this.extension.bundle_root, this.extension.entrypoint)],
      {
        cwd: this.extension.bundle_root,
        env: {PATH: process.env.PATH ?? "", ...environment},
        shell: false,
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
    this.#child = child;
    this.#stdout = Buffer.alloc(0);
    this.#stderrBytes = 0;
    child.stdout.on("data", (chunk: Buffer) => this.#onStdout(chunk));
    child.stderr.on("data", (chunk: Buffer) => this.#onStderr(chunk));
    child.once("exit", (code, signal) => this.#onExit(code, signal));
    child.once("error", (error) => this.#failAll(error));
    await this.#startupHealth();
  }

  async #startupHealth(): Promise<void> {
    const previous = this.policy.request_timeout_ms;
    this.policy.request_timeout_ms = this.policy.startup_timeout_ms;
    try {
      await this.#requestStarted("health", {});
    } finally {
      this.policy.request_timeout_ms = previous;
    }
  }

  #onStdout(chunk: Buffer): void {
    this.#stdout = Buffer.concat([this.#stdout, chunk]);
    if (this.#stdout.length > this.policy.max_frame_bytes) {
      this.#terminate("stdout frame exceeds policy");
      return;
    }
    for (;;) {
      const newline = this.#stdout.indexOf(0x0a);
      if (newline < 0) return;
      const line = this.#stdout.subarray(0, newline);
      this.#stdout = this.#stdout.subarray(newline + 1);
      this.#handleFrame(line);
    }
  }

  #handleFrame(line: Buffer): void {
    let frame: IpcResponse;
    try {
      frame = JSON.parse(line.toString("utf8")) as IpcResponse;
    } catch {
      this.#terminate("stdout contains non-JSON protocol data");
      return;
    }
    const pending = this.#pending.get(frame.id);
    if (!pending) {
      this.#terminate("stdout contains an unsolicited protocol frame");
      return;
    }
    clearTimeout(pending.timer);
    this.#pending.delete(frame.id);
    if (frame.type === "error") {
      pending.reject(new Error(frame.error?.message ?? "extension request failed"));
      return;
    }
    if (frame.type !== "result" && frame.type !== "pong") {
      pending.reject(new Error("extension response type is invalid"));
      return;
    }
    pending.resolve(frame.payload ?? {});
  }

  #onStderr(chunk: Buffer): void {
    const remaining = Math.max(0, this.policy.max_stderr_bytes - this.#stderrBytes);
    if (remaining === 0) return;
    const text = chunk.subarray(0, remaining).toString("utf8").replace(/[\r\n]+/g, " ");
    this.#stderrBytes += Buffer.byteLength(text);
    process.stderr.write(`[extension:${this.extension.extension_id}] ${text}\n`);
  }

  #onExit(code: number | null, signal: NodeJS.Signals | null): void {
    this.#crashes.push(Date.now());
    this.#child = null;
    this.#failAll(
      new Error(`extension ${this.extension.extension_id} exited (${code ?? signal ?? "unknown"})`),
    );
  }

  #terminate(reason: string): void {
    const child = this.#child;
    this.#child = null;
    if (child && !child.killed) child.kill("SIGKILL");
    this.#failAll(new Error(reason));
  }

  #failAll(error: Error): void {
    for (const pending of this.#pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.#pending.clear();
  }
}

function resolveEnvironment(values: Record<string, string>): NodeJS.ProcessEnv {
  const resolved: NodeJS.ProcessEnv = {};
  for (const [key, value] of Object.entries(values)) {
    if (!/^[A-Z][A-Z0-9_]*$/.test(key)) throw new Error(`invalid extension env key: ${key}`);
    if (value.startsWith("env://")) {
      const source = value.slice(6);
      const secret = process.env[source];
      if (secret === undefined) throw new Error(`required extension env is missing: ${source}`);
      resolved[key] = secret;
    } else {
      resolved[key] = value;
    }
  }
  return resolved;
}
