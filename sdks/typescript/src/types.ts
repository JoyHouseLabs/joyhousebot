export type RunStatus =
  | "queued" | "running" | "waiting_for_input" | "waiting_for_approval"
  | "succeeded" | "failed" | "cancelled";

export interface Run {
  id: string;
  status: RunStatus;
  progress: {phase?: string | null; summary: string; completed: number; total: number};
  pending_action?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
}

export interface RunEvent {
  sequence: number;
  event: string;
  run_id: string;
  timestamp?: string | null;
  data: Record<string, unknown>;
}

export interface EntryPoint {
  id: string;
  key: string;
  app_id: string;
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema?: Record<string, unknown> | null;
  interaction_mode: "auto" | "interactive" | "background";
  permission_summary: string[];
  risk_summary: string[];
}

export interface Page<T> {items: T[]; next_cursor?: string | null}

export interface ClientOptions {
  baseUrl: string;
  timeoutMs?: number;
  fetch?: typeof globalThis.fetch;
}

export interface RunOptions {
  idempotencyKey: string;
  appId?: string;
  sessionId?: string;
  clientContext?: Record<string, unknown>;
}
