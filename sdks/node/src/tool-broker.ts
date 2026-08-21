import type {HostToolRequest, HostToolRequestRecord} from "./types.js";

export class HostToolBrokerClient {
  readonly #baseUrl: string;
  readonly #token: string;
  readonly #timeoutMs: number;

  constructor(options: {baseUrl: string; token: string; timeoutMs?: number}) {
    const parsed = new URL(options.baseUrl);
    const loopback = ["127.0.0.1", "::1", "localhost"].includes(parsed.hostname);
    if (parsed.protocol !== "https:" && !(parsed.protocol === "http:" && loopback)) {
      throw new Error("Host Tool Broker requires HTTPS; HTTP is loopback-only");
    }
    if (parsed.username || parsed.password || parsed.search || parsed.hash) {
      throw new Error("Host Tool Broker URL cannot contain credentials, query, or fragment");
    }
    if (!options.token.startsWith("jht_") || options.token.length < 40) {
      throw new Error("Host Tool grant is invalid");
    }
    this.#baseUrl = parsed.toString().replace(/\/$/, "");
    this.#token = options.token;
    this.#timeoutMs = options.timeoutMs ?? 30_000;
  }

  async submit<TInput extends Record<string, unknown>>(
    request: HostToolRequest<TInput>,
  ): Promise<{request: HostToolRequestRecord; created: boolean}> {
    return this.#request("/host/v1/host-tool-requests", {
      method: "POST",
      body: JSON.stringify(request),
    }) as Promise<{request: HostToolRequestRecord; created: boolean}>;
  }

  async get<TOutput extends Record<string, unknown>>(
    requestId: string,
  ): Promise<HostToolRequestRecord<TOutput>> {
    const value = await this.#request(
      `/host/v1/host-tool-requests/${encodeURIComponent(requestId)}`,
      {method: "GET"},
    ) as {request: HostToolRequestRecord<TOutput>};
    return value.request;
  }

  async wait<TOutput extends Record<string, unknown>>(
    requestId: string,
    options: {pollIntervalMs?: number; timeoutMs?: number} = {},
  ): Promise<HostToolRequestRecord<TOutput>> {
    const deadline = Date.now() + (options.timeoutMs ?? 120_000);
    for (;;) {
      const request = await this.get<TOutput>(requestId);
      if (["succeeded", "failed", "manual_required", "cancelled"].includes(request.status)) {
        return request;
      }
      if (Date.now() >= deadline) throw new Error("Host Tool request polling timed out");
      await new Promise((resolve) => setTimeout(resolve, options.pollIntervalMs ?? 500));
    }
  }

  async #request(path: string, init: RequestInit): Promise<Record<string, unknown>> {
    const response = await fetch(`${this.#baseUrl}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${this.#token}`,
        ...(init.body ? {"Content-Type": "application/json"} : {}),
      },
      signal: AbortSignal.timeout(this.#timeoutMs),
    });
    const value = await response.json() as Record<string, unknown>;
    if (!response.ok) {
      const error = value.error as Record<string, unknown> | undefined;
      throw new Error(String(error?.message ?? value.detail ?? `Runtime HTTP ${response.status}`));
    }
    return value;
  }
}
