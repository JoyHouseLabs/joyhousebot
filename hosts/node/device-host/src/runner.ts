import {randomUUID} from "node:crypto";

import type {OperationProgressEvent, RemoteCapabilityResponse} from "@porthouse/extension-sdk";

import type {
  CloudDeviceTransport,
  DeviceDelivery,
  DeviceHostControlRequest,
  LocalCapabilityHost,
  LocalRuntimeContext,
} from "./types.js";

export class DeviceHostRunner {
  readonly #cloud: CloudDeviceTransport;
  readonly #local: LocalCapabilityHost;
  readonly #sessionId = `device-session-${randomUUID()}`;
  readonly #maximumReconciles: number;

  constructor(
    cloud: CloudDeviceTransport,
    local: LocalCapabilityHost,
    maximumReconciles = 1_000,
  ) {
    this.#cloud = cloud;
    this.#local = local;
    this.#maximumReconciles = maximumReconciles;
  }

  async runOnce(): Promise<number> {
    const deliveries = await this.#cloud.claim(this.#sessionId);
    for (const delivery of deliveries) {
      await this.#execute(delivery);
    }
    const controls = await this.#cloud.claimControls(this.#sessionId);
    for (const control of controls) {
      await this.#executeControl(control);
    }
    return deliveries.length + controls.length;
  }

  async #executeControl(control: DeviceHostControlRequest): Promise<void> {
    const outcome = await this.#controlOutcome(control);
    await this.#cloud.completeControl(control, this.#sessionId, outcome);
  }

  async #controlOutcome(control: DeviceHostControlRequest): Promise<{
    status: "succeeded" | "failed" | "manual_required";
    result?: Record<string, unknown>;
    error?: Record<string, unknown>;
  }> {
    try {
      const health = await fetch(`${this.#cloud.localHostBaseUrl}/healthz`, {
        signal: AbortSignal.timeout(5_000),
      });
      if (!health.ok) throw new Error(`Runtime health returned ${health.status}`);
      if (["preflight", "diagnose_opencli", "diagnose_pi"].includes(control.action)) {
        return {
          status: "succeeded",
          result: {
            action: control.action,
            node_version: process.version,
            local_host: "reachable",
            parameters: control.parameters,
          },
        };
      }
      return {
        status: "manual_required",
        error: {
          code: "HOST_PROFILE_UPDATE_REQUIRED",
          message: "This Desktop Host must apply the signed capability profile before changing deployment state.",
        },
      };
    } catch (error) {
      return {
        status: "failed",
        error: {code: "HOST_PREFLIGHT_FAILED", message: safeMessage(error)},
      };
    }
  }

  async #execute(delivery: DeviceDelivery): Promise<void> {
    let runtimeContext = await this.#runtimeContext(delivery);
    let response = await this.#local.invoke(delivery.request, runtimeContext);
    let eventOffset = 0;
    const initialEvents = (response.events ?? []).map((event, index) => ({
      ...event,
      sequence: index,
    }));
    await this.#append(delivery, initialEvents, eventOffset);
    eventOffset += initialEvents.length;
    if (terminal(response)) {
      await this.#complete(delivery, response);
      return;
    }
    const operationId = response.operation?.operation_id;
    if (response.status !== "accepted" || !operationId) {
      await this.#append(delivery, [unknownEvent(eventOffset, response)], eventOffset);
      return;
    }
    let cursor = response.provider_cursor;
    for (let attempt = 0; attempt < this.#maximumReconciles; attempt += 1) {
      await this.#cloud.renew(delivery, this.#sessionId);
      await wait(Math.max(250, Math.min(5_000, (response.retry_after_seconds ?? 1) * 1_000)));
      runtimeContext = await this.#runtimeContext(delivery);
      response = await this.#local.reconcile(
        delivery.request,
        operationId,
        cursor,
        runtimeContext,
      );
      const events = (response.events ?? []).map((event, index) => ({
        ...event,
        sequence: eventOffset + index,
      }));
      await this.#append(delivery, events, eventOffset);
      eventOffset += events.length;
      cursor = response.provider_cursor ?? cursor;
      if (terminal(response)) {
        await this.#complete(delivery, response);
        return;
      }
      if (response.status === "unknown") {
        await this.#append(delivery, [unknownEvent(eventOffset, response)], eventOffset);
        return;
      }
    }
    await this.#append(
      delivery,
      [
        {
          event_id: `device-reconcile-limit-${delivery.delivery_id}`,
          sequence: eventOffset,
          event_type: "manual_required",
          summary: "Device Host reconciliation limit reached",
          payload: {},
        },
      ],
      eventOffset,
    );
  }

  async #runtimeContext(delivery: DeviceDelivery): Promise<LocalRuntimeContext | undefined> {
    const authorization = delivery.request.authorization as unknown as Record<string, unknown>;
    const context: LocalRuntimeContext = {};
    if (authorization.model_access) {
      context.model_gateway_base_url = this.#modelGatewayBaseUrl();
      context.model_grant_token = await this.#cloud.issueModelGrant(delivery, this.#sessionId);
    }
    if (Array.isArray(authorization.tool_access) && authorization.tool_access.length > 0) {
      context.tool_broker_base_url = this.#cloud.runtimeBaseUrl;
      context.tool_grant_token = await this.#cloud.issueToolGrant(delivery, this.#sessionId);
    }
    return Object.keys(context).length > 0 ? context : undefined;
  }

  #modelGatewayBaseUrl(): string {
    if (!this.#cloud.modelGatewayBaseUrl) {
      throw new Error("Device Host model gateway URL is not configured");
    }
    return this.#cloud.modelGatewayBaseUrl;
  }

  async #append(
    delivery: DeviceDelivery,
    events: OperationProgressEvent[],
    _offset: number,
  ): Promise<void> {
    if (events.length > 0) {
      await this.#cloud.appendEvents(delivery, this.#sessionId, events);
    }
  }

  async #complete(
    delivery: DeviceDelivery,
    response: RemoteCapabilityResponse,
  ): Promise<void> {
    const succeeded = response.status === "succeeded";
    await this.#cloud.complete(delivery, this.#sessionId, {
      invocation_id: delivery.invocation_id,
      status: succeeded ? "succeeded" : "failed",
      summary: response.summary ?? (succeeded ? "Device operation completed" : "Device operation failed"),
      data: succeeded && response.output && typeof response.output === "object"
        ? response.output as Record<string, unknown>
        : {},
      artifacts: response.artifacts ?? [],
      ...(response.error ? {error: response.error} : {}),
    });
  }
}

function terminal(response: RemoteCapabilityResponse): boolean {
  return response.status === "succeeded" || response.status === "failed";
}

function unknownEvent(
  sequence: number,
  response: RemoteCapabilityResponse,
): OperationProgressEvent {
  return {
    event_id: `device-unknown-${sequence}`,
    sequence,
    event_type: "manual_required",
    summary: response.summary ?? "Local Host operation outcome is unknown",
    payload: {error: response.error ?? null},
  };
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function safeMessage(error: unknown): string {
  return (error instanceof Error ? error.message : "local Host check failed")
    .replace(/[\r\n]+/g, " ")
    .slice(0, 500);
}
