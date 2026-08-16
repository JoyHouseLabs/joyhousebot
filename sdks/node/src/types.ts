export interface CapabilityIdentity {
  capability_id: string;
  version: string;
  implementation_digest: string;
}

export interface InvocationSubject {
  user_id: string;
  agent_id: string | null;
  session_id: string | null;
}

export interface InvocationExecution {
  run_id: string;
  root_run_id: string;
  task_id: string | null;
  request_id: string | null;
  action_id: string | null;
  idempotency_key: string;
  request_digest: string;
}

export interface InvocationAuthorization {
  permissions: string[];
  permission_mode: string;
  model_access?: Record<string, unknown>;
  tool_access?: Array<CapabilityIdentity & Record<string, unknown>>;
}

export interface HostToolRequest<TInput = Record<string, unknown>> {
  host_request_id: string;
  capability_id: string;
  capability_version: string;
  input: TInput;
}

export interface HostToolRequestRecord<TOutput = Record<string, unknown>> {
  request_id: string;
  host_request_id: string;
  delivery_id: string;
  capability_ref: CapabilityIdentity & Record<string, unknown>;
  status:
    | "queued"
    | "running"
    | "waiting_approval"
    | "waiting_external"
    | "succeeded"
    | "failed"
    | "manual_required"
    | "cancelled";
  result?: TOutput | null;
  error?: RemoteError | null;
}

export interface InvocationRequest<TInput = unknown> {
  protocol_version: "1";
  capability: CapabilityIdentity;
  subject: InvocationSubject;
  execution: InvocationExecution;
  authorization: InvocationAuthorization;
  input: TInput;
}

export interface ReconcileRequest {
  protocol_version: "1";
  capability: CapabilityIdentity;
  subject: InvocationSubject;
  execution: InvocationExecution;
  operation: {operation_id: string; cursor?: string};
}

export interface OperationProgressEvent {
  event_id: string;
  sequence: number;
  event_type: string;
  summary?: string;
  payload?: Record<string, unknown>;
  created_at?: string;
}

export interface ExtensionHostManifest {
  host_protocol_version: "1";
  host: {
    host_id: string;
    version: string;
    build_digest: string;
  };
  extensions: Array<{
    extension_id: string;
    version: string;
    build_digest: string;
    lockfile_digest: string;
    sdk_version: string;
  }>;
  capabilities: Array<CapabilityIdentity & Record<string, unknown>>;
  channels?: Array<ChannelDriverIdentity & Record<string, unknown>>;
  event_sources?: Array<EventSourceIdentity & Record<string, unknown>>;
}

export interface ChannelDriverIdentity {
  channel_id: string;
  version: string;
  implementation_digest: string;
}

export interface EventSourceIdentity {
  event_source_id: string;
  version: string;
  implementation_digest: string;
}

export interface ChannelInboundEnvelope {
  protocol_version: "1";
  event_id: string;
  dedupe_key: string;
  channel_id: string;
  account_id: string;
  external_chat_id: string;
  external_sender_id: string;
  occurred_at: string;
  text?: string;
  attachments?: Array<Record<string, unknown>>;
  cursor?: string;
}

export interface EventSourceEnvelope {
  protocol_version: "1";
  event_id: string;
  dedupe_key: string;
  event_source_id: string;
  event_type: string;
  occurred_at: string;
  payload: Record<string, unknown>;
  cursor?: string;
}

export interface HostDescribeRequest {
  protocol_version: "1";
  request: {
    service_id: string;
    expected_manifest_digest: string;
  };
}

export interface HostDescribeResponse {
  protocol_version: "1";
  status: "succeeded";
  manifest: ExtensionHostManifest;
  manifest_digest: string;
  runtime: {language: "node"; version: string};
}

export interface RemoteError {
  code: string;
  message: string;
  retryable: boolean;
}

export interface RemoteCapabilityResponse<TOutput = unknown> {
  protocol_version: "1";
  status: "succeeded" | "accepted" | "pending" | "failed" | "unknown";
  summary?: string;
  output?: TOutput;
  operation?: {operation_id: string};
  artifacts?: unknown[];
  error?: RemoteError;
  retry_after_seconds?: number;
  provider_cursor?: string;
  checkpoint_ref?: string;
  progress_summary?: string;
  progress_percent?: number;
  events?: OperationProgressEvent[];
  cursor_reset?: boolean;
  write_receipt?: {action_id: string; idempotency_key: string};
}

export interface SignedRequestMetadata {
  method: string;
  path: string;
  timestamp: string;
  nonce: string;
  signature: string;
  keyId: string;
  protocolVersion: string;
  idempotencyKey: string;
  actionId?: string;
}
