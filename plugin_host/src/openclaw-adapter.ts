import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import type { PluginRegistrySnapshot } from "./rpc-schema.js";

type GenericRecord = Record<string, unknown>;

type OpenClawPluginRegistry = {
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
  tools: Array<{ pluginId: string; names: string[]; factory: (...args: unknown[]) => unknown }>;
  services: Array<{ pluginId: string; service: { id: string; start: (...args: unknown[]) => unknown; stop?: (...args: unknown[]) => unknown } }>;
  hooks: Array<{ pluginId: string; entry: { hook: { name?: string } } }>;
  channels: Array<{ plugin: { id: string } }>;
  providers: Array<{ provider: { id: string } }>;
  gatewayHandlers: Record<string, (...args: unknown[]) => unknown>;
  diagnostics: Array<{ level: string; message: string; pluginId?: string; source?: string }>;
};

type AdapterState = {
  openclawDir: string;
  workspaceDir: string;
  config: GenericRecord;
  registry: OpenClawPluginRegistry;
  loadedAtMs: number;
  skillsDirs: string[];
  serviceStates: Map<string, boolean>;
};

function normalizeArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((entry) => (typeof entry === "string" ? entry.trim() : ""))
    .filter(Boolean);
}

function ensureRecord(value: unknown): GenericRecord {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as GenericRecord;
  }
  return {};
}

function resolveOpenClawDir(input?: string): string {
  const envDir = (process.env.JOYHOUSEBOT_OPENCLAW_DIR ?? "").trim();
  const candidate = (input ?? envDir).trim();
  if (candidate) {
    return path.resolve(candidate);
  }
  const here = path.dirname(fileURLToPath(import.meta.url));
  return path.resolve(here, "../../../openclaw");
}

function resolveConfigPlugins(config: GenericRecord): GenericRecord {
  const rawPlugins = ensureRecord(config.plugins);
  const rawLoad = ensureRecord(rawPlugins.load);
  const rawSlots = ensureRecord(rawPlugins.slots);
  return {
    enabled: typeof rawPlugins.enabled === "boolean" ? rawPlugins.enabled : true,
    allow: normalizeArray(rawPlugins.allow),
    deny: normalizeArray(rawPlugins.deny),
    load: {
      paths: normalizeArray(rawLoad.paths),
    },
    entries: ensureRecord(rawPlugins.entries),
    slots: {
      memory: typeof rawSlots.memory === "string" ? rawSlots.memory : undefined,
    },
    installs: ensureRecord(rawPlugins.installs),
  };
}

function resolveSkillDirsFromPlugins(plugins: OpenClawPluginRegistry["plugins"]): string[] {
  const seen = new Set<string>();
  const dirs: string[] = [];
  for (const plugin of plugins) {
    const root = path.dirname(plugin.source);
    const manifestPath = path.join(root, "openclaw.plugin.json");
    if (!fs.existsSync(manifestPath)) {
      continue;
    }
    try {
      const parsed = JSON.parse(fs.readFileSync(manifestPath, "utf-8")) as GenericRecord;
      const skills = normalizeArray(parsed.skills);
      for (const rel of skills) {
        const full = path.resolve(root, rel);
        if (!fs.existsSync(full) || seen.has(full)) {
          continue;
        }
        seen.add(full);
        dirs.push(full);
      }
    } catch {
      // Ignore malformed plugin manifests here; loader diagnostics already capture this.
    }
  }
  return dirs;
}

function buildMinimalOpenClawConfig(config: GenericRecord): GenericRecord {
  const plugins = resolveConfigPlugins(config);
  return {
    plugins,
    channels: ensureRecord(config.channels),
    gateway: ensureRecord(config.gateway),
    skills: ensureRecord(config.skills),
  };
}

export async function loadOpenClawRegistry(params: {
  workspaceDir: string;
  config: GenericRecord;
  openclawDir?: string;
}): Promise<AdapterState> {
  const openclawDir = resolveOpenClawDir(params.openclawDir);
  const workspaceDir = path.resolve(params.workspaceDir);
  const loaderModulePath = path.join(openclawDir, "src/plugins/loader.ts");
  const loaderModuleUrl = pathToFileURL(loaderModulePath).href;
  const module = (await import(loaderModuleUrl)) as {
    loadOpenClawPlugins?: (opts: Record<string, unknown>) => OpenClawPluginRegistry;
  };
  if (!module.loadOpenClawPlugins) {
    throw new Error(`loadOpenClawPlugins() not found at ${loaderModulePath}`);
  }
  const openclawConfig = buildMinimalOpenClawConfig(params.config);
  const registry = module.loadOpenClawPlugins({
    config: openclawConfig,
    workspaceDir,
    cache: false,
    mode: "full",
  });
  return {
    openclawDir,
    workspaceDir,
    config: params.config,
    registry,
    loadedAtMs: Date.now(),
    skillsDirs: resolveSkillDirsFromPlugins(registry.plugins),
    serviceStates: new Map<string, boolean>(),
  };
}

export function toSnapshot(state: AdapterState): PluginRegistrySnapshot {
  return {
    loadedAtMs: state.loadedAtMs,
    workspaceDir: state.workspaceDir,
    openclawDir: state.openclawDir,
    plugins: state.registry.plugins.map((p) => ({
      id: p.id,
      name: p.name,
      version: p.version,
      description: p.description,
      source: p.source,
      origin: p.origin,
      status: p.status,
      enabled: p.enabled,
      kind: p.kind,
      toolNames: p.toolNames,
      hookNames: p.hookNames,
      channelIds: p.channelIds,
      providerIds: p.providerIds,
      gatewayMethods: p.gatewayMethods,
      cliCommands: p.cliCommands,
      services: p.services,
      commands: p.commands,
      error: p.error,
    })),
    diagnostics: state.registry.diagnostics.map((d) => ({
      level: d.level,
      message: d.message,
      pluginId: d.pluginId,
      source: d.source,
    })),
    gatewayMethods: Object.keys(state.registry.gatewayHandlers ?? {}),
    toolNames: state.registry.tools.flatMap((entry) => entry.names ?? []),
    serviceIds: state.registry.services.map((entry) => entry.service.id),
    channelIds: state.registry.channels.map((entry) => entry.plugin.id),
    providerIds: state.registry.providers.map((entry) => entry.provider.id),
    hookNames: state.registry.hooks.map((entry) => entry.entry.hook.name ?? ""),
    skillsDirs: state.skillsDirs,
  };
}

function createServiceContext(state: AdapterState): Record<string, unknown> {
  return {
    config: buildMinimalOpenClawConfig(state.config),
    workspaceDir: state.workspaceDir,
    stateDir: path.join(os.homedir(), ".joyhousebot"),
    logger: {
      info: (message: string) => console.error(`[plugin-service] ${message}`),
      warn: (message: string) => console.error(`[plugin-service] ${message}`),
      error: (message: string) => console.error(`[plugin-service] ${message}`),
      debug: (message: string) => console.error(`[plugin-service] ${message}`),
    },
  };
}

export async function startPluginServices(state: AdapterState): Promise<Array<{ id: string; started: boolean; error?: string }>> {
  const out: Array<{ id: string; started: boolean; error?: string }> = [];
  const ctx = createServiceContext(state);
  for (const item of state.registry.services) {
    const id = item.service.id;
    try {
      const result = item.service.start(ctx);
      if (result && typeof (result as Promise<unknown>).then === "function") {
        await result;
      }
      state.serviceStates.set(id, true);
      out.push({ id, started: true });
    } catch (error) {
      state.serviceStates.set(id, false);
      out.push({ id, started: false, error: String(error) });
    }
  }
  return out;
}

export async function stopPluginServices(state: AdapterState): Promise<Array<{ id: string; stopped: boolean; error?: string }>> {
  const out: Array<{ id: string; stopped: boolean; error?: string }> = [];
  const ctx = createServiceContext(state);
  for (const item of state.registry.services) {
    const id = item.service.id;
    try {
      if (item.service.stop) {
        const result = item.service.stop(ctx);
        if (result && typeof (result as Promise<unknown>).then === "function") {
          await result;
        }
      }
      state.serviceStates.set(id, false);
      out.push({ id, stopped: true });
    } catch (error) {
      out.push({ id, stopped: false, error: String(error) });
    }
  }
  return out;
}

export async function invokeGatewayMethod(
  state: AdapterState,
  method: string,
  params: Record<string, unknown>,
): Promise<{ ok: boolean; payload?: unknown; error?: unknown }> {
  const handler = state.registry.gatewayHandlers[method];
  if (!handler) {
    return { ok: false, error: { code: "METHOD_NOT_FOUND", message: `unknown method: ${method}` } };
  }
  let response: { ok: boolean; payload?: unknown; error?: unknown } = { ok: false };
  await handler({
    req: { id: "plugin-host", method, type: "req", params },
    params,
    client: null,
    isWebchatConnect: () => false,
    respond: (ok: boolean, payload?: unknown, error?: unknown) => {
      response = { ok, payload, error };
    },
    context: {
      // Keep context minimal. Complex plugins can still fail gracefully.
      deps: {},
      cron: {},
      cronStorePath: "",
      loadGatewayModelCatalog: async () => [],
      getHealthCache: () => null,
      refreshHealthSnapshot: async () => ({}),
      logHealth: { error: () => undefined },
      logGateway: {
        info: () => undefined,
        warn: () => undefined,
        error: () => undefined,
        debug: () => undefined,
      },
      incrementPresenceVersion: () => 1,
      getHealthVersion: () => 1,
      broadcast: () => undefined,
      broadcastToConnIds: () => undefined,
      nodeSendToSession: () => undefined,
      nodeSendToAllSubscribed: () => undefined,
      nodeSubscribe: () => undefined,
      nodeUnsubscribe: () => undefined,
      nodeUnsubscribeAll: () => undefined,
      hasConnectedMobileNode: () => false,
      nodeRegistry: {},
      agentRunSeq: new Map(),
      chatAbortControllers: new Map(),
      chatAbortedRuns: new Map(),
      chatRunBuffers: new Map(),
      chatDeltaSentAt: new Map(),
      addChatRun: () => undefined,
      removeChatRun: () => undefined,
      registerToolEventRecipient: () => undefined,
      dedupe: new Map(),
      wizardSessions: new Map(),
      findRunningWizard: () => null,
      purgeWizardSession: () => undefined,
      getRuntimeSnapshot: () => ({}),
      startChannel: async () => undefined,
      stopChannel: async () => undefined,
      markChannelLoggedOut: () => undefined,
      wizardRunner: async () => undefined,
      broadcastVoiceWakeChanged: () => undefined,
    },
  });
  return response;
}

export async function invokeTool(
  state: AdapterState,
  name: string,
  args: unknown,
  context: Record<string, unknown>,
): Promise<{ ok: boolean; result?: unknown; error?: string }> {
  for (const item of state.registry.tools) {
    if (!item.names.includes(name)) {
      continue;
    }
    try {
      const produced = item.factory(context as never);
      const tool = Array.isArray(produced)
        ? produced.find((entry) => typeof entry?.name === "string" && entry.name === name)
        : produced;
      if (!tool || typeof tool !== "object") {
        continue;
      }
      const runFn =
        (tool as Record<string, unknown>).execute ??
        (tool as Record<string, unknown>).run ??
        (tool as Record<string, unknown>).handler;
      if (typeof runFn !== "function") {
        return { ok: false, error: `tool ${name} is not invokable` };
      }
      const maybe = (runFn as (value: unknown) => unknown)(args);
      const result = maybe && typeof (maybe as Promise<unknown>).then === "function" ? await maybe : maybe;
      return { ok: true, result };
    } catch (error) {
      return { ok: false, error: String(error) };
    }
  }
  return { ok: false, error: `tool not found: ${name}` };
}

export type { AdapterState };

