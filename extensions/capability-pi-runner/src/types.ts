export interface WorkspaceTestProfile {
  command: string;
  args: string[];
  timeout_ms: number;
}

export interface WorkspaceDefinition {
  repository: string;
  tests: Record<string, WorkspaceTestProfile>;
}

export interface PiRuntimeContext {
  model_gateway_base_url: string;
  model_grant_token: string;
  tool_broker_base_url?: string;
  tool_grant_token?: string;
}

export interface PiOperation {
  operation_id: string;
  identity_key: string;
  request_digest: string;
  capability_id: string;
  user_id: string;
  input: {
    workspace_ref: string;
    revision: string;
    instruction: string;
    test_profile?: string;
  };
  status: "running" | "succeeded" | "failed" | "manual_required" | "cancelled";
  workspace_path: string;
  events: Array<Record<string, unknown>>;
  output?: Record<string, unknown>;
  error?: {code: string; message: string; retryable: boolean};
  created_at: string;
  updated_at: string;
}
