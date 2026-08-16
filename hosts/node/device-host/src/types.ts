import type {
  InvocationRequest,
  OperationProgressEvent,
  RemoteCapabilityResponse,
} from "@porthouse/extension-sdk";

export interface DeviceHostConfig {
  runtimeBaseUrl: string;
  modelGatewayBaseUrl: string;
  deviceId: string;
  deviceToken: string;
  hostRevision: string;
  hostManifestDigest: string;
  pollIntervalMs: number;
  claimLeaseSeconds: number;
  localHost: {
    baseUrl: string;
    basePath: string;
    keyId: string;
    signingSecret: string;
    requireResponseSignature: boolean;
  };
}

export interface LocalRuntimeContext {
  model_gateway_base_url?: string;
  model_grant_token?: string;
  tool_broker_base_url?: string;
  tool_grant_token?: string;
}

export interface DeviceDelivery {
  delivery_id: string;
  invocation_id: string;
  claim_version: number;
  request: InvocationRequest;
}

export type DeviceHostControlAction =
  | "preflight"
  | "diagnose_opencli"
  | "diagnose_pi"
  | "enable_opencli"
  | "disable_opencli"
  | "enable_pi"
  | "disable_pi"
  | "restart_host";

export interface DeviceHostControlRequest {
  request_id: string;
  action: DeviceHostControlAction;
  parameters: Record<string, string>;
  claim_version: number;
}

export interface CloudDeviceTransport {
  readonly modelGatewayBaseUrl: string;
  readonly runtimeBaseUrl: string;
  readonly localHostBaseUrl: string;
  heartbeat(): Promise<void>;
  claim(sessionId: string): Promise<DeviceDelivery[]>;
  renew(delivery: DeviceDelivery, sessionId: string): Promise<void>;
  issueModelGrant(delivery: DeviceDelivery, sessionId: string): Promise<string>;
  issueToolGrant(delivery: DeviceDelivery, sessionId: string): Promise<string>;
  appendEvents(
    delivery: DeviceDelivery,
    sessionId: string,
    events: OperationProgressEvent[],
  ): Promise<void>;
  complete(
    delivery: DeviceDelivery,
    sessionId: string,
    result: Record<string, unknown>,
  ): Promise<void>;
  claimControls(sessionId: string): Promise<DeviceHostControlRequest[]>;
  completeControl(
    request: DeviceHostControlRequest,
    sessionId: string,
    outcome: {status: "succeeded" | "failed" | "manual_required"; result?: Record<string, unknown>; error?: Record<string, unknown>},
  ): Promise<void>;
}

export interface LocalCapabilityHost {
  invoke(
    request: InvocationRequest,
    runtimeContext?: LocalRuntimeContext,
  ): Promise<RemoteCapabilityResponse>;
  reconcile(
    request: InvocationRequest,
    operationId: string,
    cursor?: string,
    runtimeContext?: LocalRuntimeContext,
  ): Promise<RemoteCapabilityResponse>;
}
