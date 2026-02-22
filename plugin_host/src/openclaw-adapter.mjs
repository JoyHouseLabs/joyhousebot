import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

function normalizeArray(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((entry) => (typeof entry === "string" ? entry.trim() : ""))
    .filter(Boolean);
}

function ensureRecord(value) {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value;
  }
  return {};
}

function resolveOpenClawDir(input) {
  const envDir = (process.env.JOYHOUSEBOT_OPENCLAW_DIR ?? "").trim();
  const candidate = (input ?? envDir).trim();
  if (candidate) {
    return path.resolve(candidate);
  }
  const here = path.dirname(fileURLToPath(import.meta.url));
  return path.resolve(here, "../../../openclaw");
}

function resolveConfigPlugins(config) {
  const rawPlugins = ensureRecord(config.plugins);
  const rawLoad = ensureRecord(rawPlugins.load);
  const rawSlots = ensureRecord(rawPlugins.slots);
  return {
    enabled: typeof rawPlugins.enabled === "boolean" ? rawPlugins.enabled : true,
    allow: normalizeArray(rawPlugins.allow),
    deny: normalizeArray(rawPlugins.deny),
    load: { paths: normalizeArray(rawLoad.paths) },
    entries: ensureRecord(rawPlugins.entries),
    slots: { memory: typeof rawSlots.memory === "string" ? rawSlots.memory : undefined },
    installs: ensureRecord(rawPlugins.installs),
  };
}

function resolveSkillDirsFromPlugins(plugins) {
  const seen = new Set();
  const dirs = [];
  for (const plugin of plugins) {
    const root = path.dirname(plugin.source);
    const manifestPath = path.join(root, "openclaw.plugin.json");
    if (!fs.existsSync(manifestPath)) {
      continue;
    }
    try {
      const parsed = JSON.parse(fs.readFileSync(manifestPath, "utf-8"));
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
      // Loader diagnostics already cover malformed manifests.
    }
  }
  return dirs;
}

function buildMinimalOpenClawConfig(config) {
  const plugins = resolveConfigPlugins(config);
  return {
    plugins,
    channels: ensureRecord(config.channels),
    gateway: ensureRecord(config.gateway),
    skills: ensureRecord(config.skills),
  };
}

export async function loadOpenClawRegistry(params) {
  const openclawDir = resolveOpenClawDir(params.openclawDir);
  const workspaceDir = path.resolve(params.workspaceDir);
  const distLoaderPath = path.join(openclawDir, "dist/plugins/loader.js");
  const loaderModulePath = fs.existsSync(distLoaderPath)
    ? distLoaderPath
    : path.join(openclawDir, "src/plugins/loader.ts");
  if (!fs.existsSync(loaderModulePath)) {
    throw new Error(
      `OpenClaw loader not found. Expected ${distLoaderPath} (build openclaw first with pnpm build).`,
    );
  }
  if (loaderModulePath.endsWith(".ts")) {
    throw new Error(
      "OpenClaw dist build is missing (dist/plugins/loader.js). Please run `pnpm build` in openclaw first.",
    );
  }
  const loaderModuleUrl = pathToFileURL(loaderModulePath).href;
  const module = await import(loaderModuleUrl);
  if (!module.loadOpenClawPlugins) {
    throw new Error(`loadOpenClawPlugins() not found at ${loaderModulePath}`);
  }
  const openclawConfig = buildMinimalOpenClawConfig(params.config ?? {});
  const registry = module.loadOpenClawPlugins({
    config: openclawConfig,
    workspaceDir,
    cache: false,
    mode: "full",
  });
  return {
    openclawDir,
    workspaceDir,
    config: params.config ?? {},
    registry,
    loadedAtMs: Date.now(),
    skillsDirs: resolveSkillDirsFromPlugins(registry.plugins ?? []),
    serviceStates: new Map(),
  };
}

export function toSnapshot(state) {
  return {
    loadedAtMs: state.loadedAtMs,
    workspaceDir: state.workspaceDir,
    openclawDir: state.openclawDir,
    plugins: (state.registry.plugins ?? []).map((p) => ({
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
    diagnostics: (state.registry.diagnostics ?? []).map((d) => ({
      level: d.level,
      message: d.message,
      pluginId: d.pluginId,
      source: d.source,
    })),
    gatewayMethods: Object.keys(state.registry.gatewayHandlers ?? {}),
    toolNames: (state.registry.tools ?? []).flatMap((entry) => entry.names ?? []),
    serviceIds: (state.registry.services ?? []).map((entry) => entry.service.id),
    channelIds: (state.registry.channels ?? []).map((entry) => entry.plugin.id),
    providerIds: (state.registry.providers ?? []).map((entry) => entry.provider.id),
    hookNames: (state.registry.hooks ?? []).map((entry) => entry.entry?.hook?.name ?? ""),
    skillsDirs: state.skillsDirs,
  };
}

function createServiceContext(state) {
  return {
    config: buildMinimalOpenClawConfig(state.config),
    workspaceDir: state.workspaceDir,
    stateDir: path.join(os.homedir(), ".joyhousebot"),
    logger: {
      info: (message) => console.error(`[plugin-service] ${message}`),
      warn: (message) => console.error(`[plugin-service] ${message}`),
      error: (message) => console.error(`[plugin-service] ${message}`),
      debug: (message) => console.error(`[plugin-service] ${message}`),
    },
  };
}

export async function startPluginServices(state) {
  const out = [];
  const ctx = createServiceContext(state);
  for (const item of state.registry.services ?? []) {
    const id = item.service.id;
    try {
      const result = item.service.start(ctx);
      if (result && typeof result.then === "function") {
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

export async function stopPluginServices(state) {
  const out = [];
  const ctx = createServiceContext(state);
  for (const item of state.registry.services ?? []) {
    const id = item.service.id;
    try {
      if (item.service.stop) {
        const result = item.service.stop(ctx);
        if (result && typeof result.then === "function") {
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

export async function invokeGatewayMethod(state, method, params) {
  const handler = state.registry.gatewayHandlers?.[method];
  if (!handler) {
    return { ok: false, error: { code: "METHOD_NOT_FOUND", message: `unknown method: ${method}` } };
  }
  let response = { ok: false };
  await handler({
    req: { id: "plugin-host", method, type: "req", params },
    params,
    client: null,
    isWebchatConnect: () => false,
    respond: (ok, payload, error) => {
      response = { ok, payload, error };
    },
    context: {
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

export async function invokeTool(state, name, args, context) {
  for (const item of state.registry.tools ?? []) {
    if (!(item.names ?? []).includes(name)) {
      continue;
    }
    try {
      const produced = item.factory(context ?? {});
      const tool = Array.isArray(produced)
        ? produced.find((entry) => typeof entry?.name === "string" && entry.name === name)
        : produced;
      if (!tool || typeof tool !== "object") {
        continue;
      }
      const runFn = tool.execute ?? tool.run ?? tool.handler;
      if (typeof runFn !== "function") {
        return { ok: false, error: `tool ${name} is not invokable` };
      }
      const maybe = runFn(args);
      const result = maybe && typeof maybe.then === "function" ? await maybe : maybe;
      return { ok: true, result };
    } catch (error) {
      return { ok: false, error: String(error) };
    }
  }
  return { ok: false, error: `tool not found: ${name}` };
}

