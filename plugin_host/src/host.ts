import readline from "node:readline";
import {
  invokeGatewayMethod,
  invokeTool,
  loadOpenClawRegistry,
  startPluginServices,
  stopPluginServices,
  toSnapshot,
  type AdapterState,
} from "./openclaw-adapter.js";
import type { RpcRequest, RpcResponse } from "./rpc-schema.js";
import { rpcError } from "./rpc-schema.js";

let state: AdapterState | null = null;

function respond(res: RpcResponse) {
  process.stdout.write(`${JSON.stringify(res)}\n`);
}

function ensureState(id: string): AdapterState | null {
  if (state) {
    return state;
  }
  respond({
    id,
    ok: false,
    error: rpcError("HOST_NOT_READY", "plugin host not loaded; call plugins.load first"),
  });
  return null;
}

async function handle(req: RpcRequest): Promise<RpcResponse> {
  if (!req || typeof req !== "object") {
    return { id: "unknown", ok: false, error: rpcError("INVALID_REQUEST", "request must be an object") };
  }
  const id = typeof req.id === "string" && req.id ? req.id : "invalid";
  const method = typeof req.method === "string" ? req.method : "";
  const params = req.params && typeof req.params === "object" ? req.params : {};
  if (!method) {
    return { id, ok: false, error: rpcError("INVALID_REQUEST", "method is required") };
  }

  if (method === "host.health") {
    return {
      id,
      ok: true,
      result: {
        ok: true,
        loaded: Boolean(state),
        loadedAtMs: state?.loadedAtMs ?? null,
        workspaceDir: state?.workspaceDir ?? null,
        openclawDir: state?.openclawDir ?? null,
      },
    };
  }

  if (method === "plugins.load" || method === "plugins.reload") {
    const workspaceDir = String(params.workspaceDir ?? "").trim();
    if (!workspaceDir) {
      return {
        id,
        ok: false,
        error: rpcError("INVALID_REQUEST", "plugins.load requires workspaceDir"),
      };
    }
    try {
      state = await loadOpenClawRegistry({
        workspaceDir,
        config: (params.config as Record<string, unknown>) ?? {},
        openclawDir: typeof params.openclawDir === "string" ? params.openclawDir : undefined,
      });
      return { id, ok: true, result: toSnapshot(state) };
    } catch (error) {
      return {
        id,
        ok: false,
        error: rpcError("OPENCLAW_LOAD_ERROR", String(error)),
      };
    }
  }

  if (method === "plugins.status") {
    const ready = ensureState(id);
    if (!ready) {
      return { id, ok: false, error: rpcError("HOST_NOT_READY", "plugin host not loaded") };
    }
    return { id, ok: true, result: toSnapshot(ready) };
  }

  if (method === "plugins.list") {
    const ready = ensureState(id);
    if (!ready) {
      return { id, ok: false, error: rpcError("HOST_NOT_READY", "plugin host not loaded") };
    }
    const snapshot = toSnapshot(ready);
    return { id, ok: true, result: snapshot.plugins };
  }

  if (method === "plugins.info") {
    const ready = ensureState(id);
    if (!ready) {
      return { id, ok: false, error: rpcError("HOST_NOT_READY", "plugin host not loaded") };
    }
    const pluginId = String(params.id ?? "").trim();
    const snapshot = toSnapshot(ready);
    const plugin = snapshot.plugins.find((entry) => entry.id === pluginId || entry.name === pluginId);
    if (!plugin) {
      return { id, ok: false, error: rpcError("PLUGIN_NOT_FOUND", `plugin not found: ${pluginId}`) };
    }
    return { id, ok: true, result: plugin };
  }

  if (method === "plugins.doctor") {
    const ready = ensureState(id);
    if (!ready) {
      return { id, ok: false, error: rpcError("HOST_NOT_READY", "plugin host not loaded") };
    }
    const snapshot = toSnapshot(ready);
    return {
      id,
      ok: true,
      result: {
        diagnostics: snapshot.diagnostics,
        errors: snapshot.plugins.filter((entry) => entry.status === "error"),
      },
    };
  }

  if (method === "plugins.gateway.methods") {
    const ready = ensureState(id);
    if (!ready) {
      return { id, ok: false, error: rpcError("HOST_NOT_READY", "plugin host not loaded") };
    }
    return { id, ok: true, result: Object.keys(ready.registry.gatewayHandlers ?? {}) };
  }

  if (method === "plugins.gateway.invoke") {
    const ready = ensureState(id);
    if (!ready) {
      return { id, ok: false, error: rpcError("HOST_NOT_READY", "plugin host not loaded") };
    }
    const gatewayMethod = String(params.method ?? "").trim();
    if (!gatewayMethod) {
      return { id, ok: false, error: rpcError("INVALID_REQUEST", "plugins.gateway.invoke requires method") };
    }
    const result = await invokeGatewayMethod(
      ready,
      gatewayMethod,
      (params.params as Record<string, unknown>) ?? {},
    );
    return { id, ok: true, result };
  }

  if (method === "plugins.tools.list") {
    const ready = ensureState(id);
    if (!ready) {
      return { id, ok: false, error: rpcError("HOST_NOT_READY", "plugin host not loaded") };
    }
    return { id, ok: true, result: ready.registry.tools.flatMap((entry) => entry.names ?? []) };
  }

  if (method === "plugins.tools.invoke") {
    const ready = ensureState(id);
    if (!ready) {
      return { id, ok: false, error: rpcError("HOST_NOT_READY", "plugin host not loaded") };
    }
    const name = String(params.name ?? "").trim();
    if (!name) {
      return { id, ok: false, error: rpcError("INVALID_REQUEST", "plugins.tools.invoke requires name") };
    }
    const result = await invokeTool(
      ready,
      name,
      params.args,
      (params.context as Record<string, unknown>) ?? {},
    );
    return { id, ok: true, result };
  }

  if (method === "plugins.services.start") {
    const ready = ensureState(id);
    if (!ready) {
      return { id, ok: false, error: rpcError("HOST_NOT_READY", "plugin host not loaded") };
    }
    return { id, ok: true, result: await startPluginServices(ready) };
  }

  if (method === "plugins.services.stop") {
    const ready = ensureState(id);
    if (!ready) {
      return { id, ok: false, error: rpcError("HOST_NOT_READY", "plugin host not loaded") };
    }
    return { id, ok: true, result: await stopPluginServices(ready) };
  }

  if (method === "plugins.skills.dirs") {
    const ready = ensureState(id);
    if (!ready) {
      return { id, ok: false, error: rpcError("HOST_NOT_READY", "plugin host not loaded") };
    }
    return { id, ok: true, result: ready.skillsDirs };
  }

  if (method === "plugins.shutdown") {
    if (state) {
      await stopPluginServices(state);
    }
    state = null;
    return { id, ok: true, result: { ok: true } };
  }

  return { id, ok: false, error: rpcError("METHOD_NOT_FOUND", `unknown method: ${method}`) };
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
rl.on("line", async (line) => {
  if (!line.trim()) {
    return;
  }
  try {
    const req = JSON.parse(line) as RpcRequest;
    const res = await handle(req);
    respond(res);
  } catch (error) {
    respond({
      id: "parse_error",
      ok: false,
      error: rpcError("INVALID_REQUEST", "invalid json request", { detail: String(error) }),
    });
  }
});

rl.on("close", async () => {
  if (state) {
    await stopPluginServices(state);
  }
  process.exit(0);
});

