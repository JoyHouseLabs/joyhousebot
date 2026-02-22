export type RpcRequest = {
  id: string;
  method: string;
  params?: Record<string, unknown>;
};

export type RpcError = {
  code:
    | "INVALID_REQUEST"
    | "METHOD_NOT_FOUND"
    | "INTERNAL_ERROR"
    | "HOST_NOT_READY"
    | "OPENCLAW_LOAD_ERROR"
    | "PLUGIN_NOT_FOUND";
  message: string;
  data?: Record<string, unknown>;
};

export type RpcResponse = {
  id: string;
  ok: boolean;
  result?: unknown;
  error?: RpcError;
};

export type PluginRegistrySnapshot = {
  loadedAtMs: number;
  workspaceDir: string;
  openclawDir: string;
  plugins: Array<{
    id: string;
    name: string;
    version?: string;
    description?: string;
    source: string;
    origin: string;
    status: string;
    enabled: boolean;
    kind?: string;
    toolNames: string[];
    hookNames: string[];
    channelIds: string[];
    providerIds: string[];
    gatewayMethods: string[];
    cliCommands: string[];
    services: string[];
    commands: string[];
    error?: string;
  }>;
  diagnostics: Array<{
    level: string;
    message: string;
    pluginId?: string;
    source?: string;
  }>;
  gatewayMethods: string[];
  toolNames: string[];
  serviceIds: string[];
  channelIds: string[];
  providerIds: string[];
  hookNames: string[];
  skillsDirs: string[];
};

export function rpcError(
  code: RpcError["code"],
  message: string,
  data?: Record<string, unknown>,
): RpcError {
  return { code, message, ...(data ? { data } : {}) };
}

