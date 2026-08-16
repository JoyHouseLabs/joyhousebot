import {randomBytes, timingSafeEqual} from "node:crypto";

import {
  canonicalJson,
  signRequestBody,
  signResponseBody,
  type InvocationRequest,
  type RemoteCapabilityResponse,
} from "@joyhousebot/extension-sdk";

import type {
  DeviceHostConfig,
  LocalCapabilityHost,
  LocalRuntimeContext,
} from "./types.js";

export class SignedLocalCapabilityHost implements LocalCapabilityHost {
  readonly #config: DeviceHostConfig["localHost"];

  constructor(config: DeviceHostConfig["localHost"]) {
    this.#config = config;
  }

  async invoke(
    request: InvocationRequest,
    runtimeContext?: LocalRuntimeContext,
  ): Promise<RemoteCapabilityResponse> {
    const id = encodeURIComponent(request.capability.capability_id);
    return this.#post(
      `${this.#config.basePath}/capabilities/${id}:invoke`,
      request,
      runtimeContext,
    );
  }

  async reconcile(
    request: InvocationRequest,
    operationId: string,
    cursor?: string,
    runtimeContext?: LocalRuntimeContext,
  ): Promise<RemoteCapabilityResponse> {
    return this.#post(`${this.#config.basePath}/operations:reconcile`, {
      protocol_version: "1",
      capability: request.capability,
      subject: request.subject,
      execution: request.execution,
      operation: {operation_id: operationId, ...(cursor ? {cursor} : {})},
    }, runtimeContext);
  }

  async #post(
    path: string,
    value: object,
    runtimeContext?: LocalRuntimeContext,
  ): Promise<RemoteCapabilityResponse> {
    const envelope = runtimeContext
      ? {invocation: value, runtime_context: runtimeContext}
      : value;
    const body = Buffer.from(canonicalJson(envelope), "utf8");
    const timestamp = String(Math.floor(Date.now() / 1000));
    const nonce = randomBytes(18).toString("base64url");
    const execution = (value as {execution?: InvocationRequest["execution"]}).execution;
    const response = await fetch(`${this.#config.baseUrl}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Joyhouse-Capability-Protocol": "1",
        "X-Joyhouse-Key-ID": this.#config.keyId,
        "X-Joyhouse-Timestamp": timestamp,
        "X-Joyhouse-Nonce": nonce,
        "X-Joyhouse-Signature": signRequestBody({
          method: "POST",
          path,
          timestamp,
          nonce,
          body,
          secret: this.#config.signingSecret,
        }),
        "Idempotency-Key": execution?.idempotency_key ?? "",
        ...(execution?.action_id
          ? {"X-Joyhouse-Action-ID": execution.action_id}
          : {}),
      },
      body,
      signal: AbortSignal.timeout(120_000),
    });
    const raw = Buffer.from(await response.arrayBuffer());
    if (this.#config.requireResponseSignature) {
      const actual = response.headers.get("x-joyhouse-response-signature") ?? "";
      const expected = signResponseBody({
        statusCode: response.status,
        nonce,
        body: raw,
        secret: this.#config.signingSecret,
      });
      if (!equal(actual, expected)) throw new Error("local Host response signature is invalid");
    }
    const result = JSON.parse(raw.toString("utf8")) as RemoteCapabilityResponse;
    if (!response.ok && result.status !== "failed") {
      throw new Error(`local Host HTTP ${response.status}`);
    }
    return result;
  }
}

function equal(left: string, right: string): boolean {
  const a = Buffer.from(left, "utf8");
  const b = Buffer.from(right, "utf8");
  return a.length === b.length && timingSafeEqual(a, b);
}
