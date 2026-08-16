import type {OperationProgressEvent} from "@joyhousebot/extension-sdk";

import type {CloudDeviceTransport, DeviceDelivery, DeviceHostConfig} from "./types.js";

export class RuntimeDeviceTransport implements CloudDeviceTransport {
  readonly #config: DeviceHostConfig;

  constructor(config: DeviceHostConfig) {
    this.#config = config;
  }

  get modelGatewayBaseUrl(): string {
    return this.#config.modelGatewayBaseUrl;
  }

  get runtimeBaseUrl(): string {
    return this.#config.runtimeBaseUrl;
  }

  async heartbeat(): Promise<void> {
    await this.#request("/v1/device-host/heartbeat", {
      host_revision: this.#config.hostRevision,
      host_manifest_digest: this.#config.hostManifestDigest,
    });
  }

  async claim(sessionId: string): Promise<DeviceDelivery[]> {
    const response = await this.#request("/v1/device-host/operations:claim", {
      claim_session_id: sessionId,
      limit: 5,
      lease_seconds: this.#config.claimLeaseSeconds,
    });
    return Array.isArray(response.items) ? (response.items as DeviceDelivery[]) : [];
  }

  async renew(delivery: DeviceDelivery, sessionId: string): Promise<void> {
    await this.#request(
      `/v1/device-host/operations/${encodeURIComponent(delivery.delivery_id)}:heartbeat`,
      {
        claim_session_id: sessionId,
        claim_version: delivery.claim_version,
        lease_seconds: this.#config.claimLeaseSeconds,
      },
    );
  }

  async issueModelGrant(delivery: DeviceDelivery, sessionId: string): Promise<string> {
    const response = await this.#request(
      `/v1/device-host/operations/${encodeURIComponent(delivery.delivery_id)}/model-grant`,
      {
        claim_session_id: sessionId,
        claim_version: delivery.claim_version,
      },
    );
    const token = String(response.model_grant_token ?? "");
    if (!token.startsWith("jhm_") || token.length < 40) {
      throw new Error("Runtime returned an invalid Host model grant");
    }
    return token;
  }

  async issueToolGrant(delivery: DeviceDelivery, sessionId: string): Promise<string> {
    const response = await this.#request(
      `/v1/device-host/operations/${encodeURIComponent(delivery.delivery_id)}/tool-grant`,
      {
        claim_session_id: sessionId,
        claim_version: delivery.claim_version,
        expires_in_seconds: Math.min(3600, this.#config.claimLeaseSeconds),
      },
    );
    const token = String(response.tool_grant_token ?? "");
    if (!token.startsWith("jht_") || token.length < 40) {
      throw new Error("Runtime returned an invalid Host Tool grant");
    }
    return token;
  }

  async appendEvents(
    delivery: DeviceDelivery,
    sessionId: string,
    events: OperationProgressEvent[],
  ): Promise<void> {
    if (events.length === 0) return;
    await this.#request(
      `/v1/device-host/operations/${encodeURIComponent(delivery.delivery_id)}/events:append`,
      {
        claim_session_id: sessionId,
        claim_version: delivery.claim_version,
        events,
      },
    );
  }

  async complete(
    delivery: DeviceDelivery,
    sessionId: string,
    result: Record<string, unknown>,
  ): Promise<void> {
    await this.#request(
      `/v1/device-host/operations/${encodeURIComponent(delivery.delivery_id)}:complete`,
      {
        claim_session_id: sessionId,
        claim_version: delivery.claim_version,
        result,
      },
    );
  }

  async #request(path: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
    const response = await fetch(`${this.#config.runtimeBaseUrl}${path}`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${this.#config.deviceToken}`,
        "Content-Type": "application/json",
        "X-Joyhouse-Device-ID": this.#config.deviceId,
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(30_000),
    });
    const value = (await response.json()) as Record<string, unknown>;
    if (!response.ok) {
      const error = value.error as Record<string, unknown> | undefined;
      throw new Error(String(error?.message ?? value.detail ?? `Runtime HTTP ${response.status}`));
    }
    return value;
  }
}
