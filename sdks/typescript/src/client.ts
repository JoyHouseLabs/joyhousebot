import {JoyHouseBotError} from "./errors.js";
import type {ClientOptions, EntryPoint, Page, Run, RunEvent, RunOptions} from "./types.js";

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

export abstract class PublicClient {
  protected accessToken?: string;
  protected expiresAt = 0;
  protected readonly baseUrl: string;
  protected readonly timeoutMs: number;
  protected readonly fetcher: typeof globalThis.fetch;

  constructor(options: ClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/$/, "");
    this.timeoutMs = options.timeoutMs ?? 30_000;
    this.fetcher = options.fetch ?? globalThis.fetch;
  }

  protected abstract authenticate(): Promise<void>;

  async listEntryPoints(limit = 100, cursor?: string): Promise<Page<EntryPoint>> {
    const query = new URLSearchParams({limit: String(limit)});
    if (cursor) query.set("cursor", cursor);
    return this.request(`/v2/entrypoints?${query}`);
  }

  async getEntryPoint(id: string): Promise<EntryPoint> {
    return this.request(`/v2/entrypoints/${encodeURIComponent(id)}`);
  }

  async resolveEntryPoint(key: string, appId?: string): Promise<EntryPoint> {
    const matches = (await this.listEntryPoints()).items.filter(
      (item) => item.key === key && (!appId || item.app_id === appId),
    );
    if (matches.length !== 1) {
      const qualifier = appId ? ` in ${appId}` : "";
      throw new Error(
        `expected exactly one installed EntryPoint ${JSON.stringify(key)}${qualifier}; found ${matches.length}`,
      );
    }
    return matches[0]!;
  }

  async runEntryPoint(
    key: string,
    input: Record<string, unknown>,
    options: RunOptions,
  ): Promise<RunHandle> {
    const entryPoint = await this.resolveEntryPoint(key, options.appId);
    return this.run(entryPoint.id, input, options);
  }

  async run(
    entryPointId: string,
    input: Record<string, unknown>,
    options: RunOptions,
  ): Promise<RunHandle> {
    const run = await this.request<Run>(
      `/v2/entrypoints/${encodeURIComponent(entryPointId)}/runs`,
      {
        method: "POST",
        headers: {"Idempotency-Key": options.idempotencyKey},
        body: JSON.stringify({
          input,
          idempotency_key: options.idempotencyKey,
          client_context: options.clientContext ?? {},
          ...(options.sessionId ? {session_id: options.sessionId} : {}),
        }),
      },
    );
    return new RunHandle(this, run);
  }

  handle(runId: string): RunHandle {
    return new RunHandle(this, {id: runId, status: "queued", progress: {summary: "", completed: 0, total: 0}});
  }

  async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    await this.ensureToken();
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${this.accessToken}`);
    if (init.body) headers.set("Content-Type", "application/json");
    const idempotent = ["GET", "HEAD", "OPTIONS"].includes(init.method ?? "GET") || headers.has("Idempotency-Key");
    const attempts = idempotent ? 3 : 1;
    for (let attempt = 0; attempt < attempts; attempt += 1) {
      let response: Response;
      try {
        response = await this.fetcher(`${this.baseUrl}${path}`, {
          ...init,
          headers,
          signal: AbortSignal.timeout(this.timeoutMs),
        });
      } catch (error) {
        if (attempt + 1 >= attempts) throw error;
        await delay(100 * (2 ** attempt));
        continue;
      }
      const value = response.status === 204 ? undefined : await response.json();
      if (response.status === 401 && attempt === 0) {
        this.accessToken = undefined;
        await this.ensureToken();
        headers.set("Authorization", `Bearer ${this.accessToken}`);
        continue;
      }
      if (!response.ok) {
        const error = JoyHouseBotError.fromResponse(response.status, value);
        if (error.retryable && attempt + 1 < attempts) {
          await delay(100 * (2 ** attempt));
          continue;
        }
        throw error;
      }
      return value as T;
    }
    throw new Error("unreachable");
  }

  async stream(path: string, afterSequence = 0): Promise<Response> {
    await this.ensureToken();
    const response = await this.fetcher(`${this.baseUrl}${path}`, {
      headers: {
        Authorization: `Bearer ${this.accessToken}`,
        ...(afterSequence ? {"Last-Event-ID": String(afterSequence)} : {}),
      },
      signal: AbortSignal.timeout(this.timeoutMs),
    });
    if (!response.ok) throw JoyHouseBotError.fromResponse(response.status, await response.json());
    return response;
  }

  protected setToken(value: {access_token: string; expires_at: string}): void {
    this.accessToken = value.access_token;
    this.expiresAt = Date.parse(value.expires_at);
  }

  private async ensureToken(): Promise<void> {
    if (!this.accessToken || this.expiresAt - Date.now() < 30_000) await this.authenticate();
  }
}

export class AppClient extends PublicClient {
  constructor(
    options: ClientOptions & {clientId: string; clientSecret: string; installationId: string; scopes?: string[]},
  ) {
    super(options);
    this.options = options;
  }
  private readonly options;

  static fromEnv(env: NodeJS.ProcessEnv = process.env): AppClient {
    return new AppClient({
      baseUrl: required(env, "JOYHOUSEBOT_URL"),
      clientId: required(env, "JOYHOUSEBOT_CLIENT_ID"),
      clientSecret: required(env, "JOYHOUSEBOT_CLIENT_SECRET"),
      installationId: required(env, "JOYHOUSEBOT_INSTALLATION_ID"),
    });
  }

  protected async authenticate(): Promise<void> {
    const response = await this.fetcher(`${this.baseUrl}/v2/app-auth/token`, {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        client_id: this.options.clientId,
        client_secret: this.options.clientSecret,
        installation_id: this.options.installationId,
        scopes: this.options.scopes ?? ["apps.read", "apps.install", "apps.launch", "runs.read", "runs.write"],
        ttl_seconds: 900,
      }),
    });
    const value = await response.json();
    if (!response.ok) throw JoyHouseBotError.fromResponse(response.status, value);
    this.setToken(value as {access_token: string; expires_at: string});
  }
}

export class OwnerClient extends PublicClient {
  private refreshToken?: string;
  constructor(
    options: ClientOptions & {
      clientId: string;
      subjectToken: string | (() => string | Promise<string>);
      scopes?: string[];
    },
  ) {
    super(options);
    this.options = options;
  }
  private readonly options;

  static fromEnv(env: NodeJS.ProcessEnv = process.env): OwnerClient {
    return new OwnerClient({
      baseUrl: required(env, "JOYHOUSEBOT_URL"),
      clientId: required(env, "JOYHOUSEBOT_OWNER_CLIENT_ID"),
      subjectToken: required(env, "JOYHOUSEBOT_OWNER_SUBJECT_TOKEN"),
    });
  }

  protected async authenticate(): Promise<void> {
    const refresh = Boolean(this.refreshToken);
    const subject = typeof this.options.subjectToken === "function"
      ? await this.options.subjectToken() : this.options.subjectToken;
    const response = await this.fetcher(
      `${this.baseUrl}/v2/owner-auth/${refresh ? "refresh" : "token"}`,
      {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify(refresh ? {
          client_id: this.options.clientId,
          refresh_token: this.refreshToken,
        } : {
          client_id: this.options.clientId,
          subject_token: subject,
          scopes: this.options.scopes ?? ["apps.read", "apps.launch", "runs.read", "runs.write"],
        }),
      },
    );
    const value = await response.json();
    if (!response.ok) {
      this.refreshToken = undefined;
      throw JoyHouseBotError.fromResponse(response.status, value);
    }
    const token = value as {access_token: string; expires_at: string; refresh_token: string};
    this.refreshToken = token.refresh_token;
    this.setToken(token);
  }

  async revoke(): Promise<void> {
    await this.request("/v2/owner-auth/revoke", {method: "POST"});
    this.accessToken = undefined;
    this.refreshToken = undefined;
  }

  async listApps(): Promise<Page<Record<string, unknown>>> {
    return this.request("/v2/apps");
  }

  async ensureApp(
    appId: string,
    version: string,
    configuration: Record<string, unknown> = {},
  ): Promise<Record<string, unknown>> {
    const existing = (await this.listApps()).items.find(
      (item) => item.app_id === appId && item.version === version && item.status === "active",
    );
    if (existing) return existing;
    return this.request(`/v2/apps/${encodeURIComponent(appId)}/install`, {
      method: "POST",
      body: JSON.stringify({version, configuration}),
    });
  }

}

export class RunHandle {
  constructor(readonly client: PublicClient, public current: Run) {}
  get id(): string { return this.current.id; }

  async get(): Promise<Run> {
    this.current = await this.client.request(`/v2/runs/${encodeURIComponent(this.id)}`);
    return this.current;
  }

  async wait(options: {timeoutMs?: number; pollMs?: number} = {}): Promise<Run> {
    const deadline = Date.now() + (options.timeoutMs ?? 300_000);
    for (;;) {
      const run = await this.get();
      if (TERMINAL.has(run.status)) return run;
      if (Date.now() >= deadline) throw new Error(`Run did not reach a terminal state: ${this.id}`);
      await delay(options.pollMs ?? 1_000);
    }
  }

  async cancel(): Promise<Run> {
    this.current = await this.client.request(`/v2/runs/${encodeURIComponent(this.id)}/cancel`, {method: "POST"});
    return this.current;
  }

  artifacts(): Promise<Page<Record<string, unknown>>> {
    return this.client.request(`/v2/runs/${encodeURIComponent(this.id)}/artifacts`);
  }

  approvals(): Promise<Page<Record<string, unknown>>> {
    return this.client.request(`/v2/runs/${encodeURIComponent(this.id)}/approvals`);
  }

  operations(): Promise<Page<Record<string, unknown>>> {
    return this.client.request(`/v2/runs/${encodeURIComponent(this.id)}/operations`);
  }

  inputs(): Promise<Page<Record<string, unknown>>> {
    return this.client.request(`/v2/runs/${encodeURIComponent(this.id)}/inputs`);
  }

  decide(approvalId: string, decision: string, note?: string): Promise<Record<string, unknown>> {
    return this.client.request(`/v2/approvals/${encodeURIComponent(approvalId)}/decisions`, {
      method: "POST", body: JSON.stringify({decision, note: note ?? null}),
    });
  }

  answer(inputRequestId: string, answers: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.client.request(`/v2/runs/${encodeURIComponent(this.id)}/inputs`, {
      method: "POST", body: JSON.stringify({input_request_id: inputRequestId, answers}),
    });
  }

  async *events(afterSequence = 0): AsyncGenerator<RunEvent> {
    const response = await this.client.stream(`/v2/runs/${encodeURIComponent(this.id)}/events`, afterSequence);
    if (!response.body) return;
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    for (;;) {
      const {done, value} = await reader.read();
      if (done) break;
      buffer += value;
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = frame.split("\n").filter((line) => line.startsWith("data:"))
          .map((line) => line.slice(5).trimStart()).join("\n");
        if (data) yield JSON.parse(data) as RunEvent;
        boundary = buffer.indexOf("\n\n");
      }
    }
  }
}

function required(env: NodeJS.ProcessEnv, name: string): string {
  const value = env[name]?.trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
