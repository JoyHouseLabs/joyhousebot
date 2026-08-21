import type {
  CapabilityIdentity,
  ChannelDriverIdentity,
  EventSourceIdentity,
  ExtensionHostManifest,
} from "@joyhousebot/extension-sdk";

export interface ResourcePolicy {
  request_timeout_ms: number;
  startup_timeout_ms: number;
  max_frame_bytes: number;
  max_stderr_bytes: number;
  max_crashes: number;
  crash_window_seconds: number;
}

export interface InstalledExtension {
  extension_id: string;
  version: string;
  build_digest: string;
  lockfile_digest: string;
  sdk_version: string;
  bundle_root: string;
  entrypoint: string;
  entrypoint_sha256: string;
  runner: "child_process" | "oci";
  capabilities?: Array<CapabilityIdentity & Record<string, unknown>>;
  channels?: Array<ChannelDriverIdentity & Record<string, unknown>>;
  event_sources?: Array<EventSourceIdentity & Record<string, unknown>>;
  environment?: Record<string, string>;
  policy?: Partial<ResourcePolicy>;
}

export interface SupervisorConfig {
  protocol_version: "1";
  host: ExtensionHostManifest["host"];
  listen: {host: string; port: number; base_path: string};
  model_gateway_base_url?: string;
  tool_broker_base_url?: string;
  operation_registry_path: string;
  extensions: InstalledExtension[];
}

export interface IpcRequest {
  id: string;
  type:
    | "health"
    | "invoke"
    | "reconcile"
    | "command"
    | "cancel"
    | "channel_start"
    | "channel_stop"
    | "channel_send"
    | "event_source_start"
    | "event_source_stop"
    | "event_ack";
  payload: Record<string, unknown>;
}

export interface IpcResponse {
  id: string;
  type: "result" | "error" | "pong";
  payload?: Record<string, unknown>;
  error?: {code: string; message: string; retryable?: boolean};
}

export interface OperationBinding {
  operation_id: string;
  extension_id: string;
  capability_id: string;
  action_id: string | null;
  idempotency_key: string;
  request_digest: string;
  updated_at: string;
}
