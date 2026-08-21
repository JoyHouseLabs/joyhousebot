import {createHash} from "node:crypto";
import {readFile, realpath} from "node:fs/promises";
import {isAbsolute, relative, resolve} from "node:path";

import type {ExtensionHostManifest} from "@joyhousebot/extension-sdk";

import type {InstalledExtension, SupervisorConfig} from "./types.js";

const DIGEST = /^sha256:[0-9a-f]{64}$/;
const ID = /^[a-z0-9][a-z0-9-]{0,127}$/;

export async function loadSupervisorConfig(path: string): Promise<SupervisorConfig> {
  const value: unknown = JSON.parse(await readFile(path, "utf8"));
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("supervisor config must be an object");
  }
  const config = value as SupervisorConfig;
  if (config.protocol_version !== "1" || !config.host || !config.listen) {
    throw new Error("supervisor config identity is invalid");
  }
  if (!DIGEST.test(config.host.build_digest) || !Array.isArray(config.extensions)) {
    throw new Error("supervisor host digest or extension list is invalid");
  }
  if (config.model_gateway_base_url) {
    config.model_gateway_base_url = safeModelGatewayUrl(config.model_gateway_base_url);
  }
  if (config.tool_broker_base_url) {
    config.tool_broker_base_url = safeServiceUrl(
      config.tool_broker_base_url,
      "tool_broker_base_url",
    );
  }
  const capabilityIds = new Set<string>();
  const channelIds = new Set<string>();
  const eventSourceIds = new Set<string>();
  const extensionIds = new Set<string>();
  for (const extension of config.extensions) {
    await verifyInstalledExtension(extension);
    if (extensionIds.has(extension.extension_id)) {
      throw new Error(`duplicate extension_id: ${extension.extension_id}`);
    }
    extensionIds.add(extension.extension_id);
    for (const capability of extension.capabilities ?? []) {
      if (capabilityIds.has(capability.capability_id)) {
        throw new Error(`duplicate capability_id: ${capability.capability_id}`);
      }
      capabilityIds.add(capability.capability_id);
    }
    for (const channel of extension.channels ?? []) {
      if (channelIds.has(channel.channel_id)) {
        throw new Error(`duplicate channel_id: ${channel.channel_id}`);
      }
      channelIds.add(channel.channel_id);
    }
    for (const source of extension.event_sources ?? []) {
      if (eventSourceIds.has(source.event_source_id)) {
        throw new Error(`duplicate event_source_id: ${source.event_source_id}`);
      }
      eventSourceIds.add(source.event_source_id);
    }
  }
  return config;
}

function safeModelGatewayUrl(value: string): string {
  return safeServiceUrl(value, "model_gateway_base_url");
}

function safeServiceUrl(value: string, field: string): string {
  const parsed = new URL(String(value).trim());
  const loopback = ["127.0.0.1", "::1", "localhost"].includes(parsed.hostname);
  if (parsed.protocol !== "https:" && !(parsed.protocol === "http:" && loopback)) {
    throw new Error(`${field} requires HTTPS; HTTP is loopback-only`);
  }
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error(`${field} cannot contain credentials, query, or fragment`);
  }
  return parsed.toString().replace(/\/$/, "");
}

export async function verifyInstalledExtension(extension: InstalledExtension): Promise<void> {
  if (!ID.test(extension.extension_id)) {
    throw new Error("extension_id is invalid");
  }
  if (![extension.build_digest, extension.lockfile_digest].every((item) => DIGEST.test(item))) {
    throw new Error(`extension ${extension.extension_id} digest is invalid`);
  }
  if (extension.runner !== "child_process" && extension.runner !== "oci") {
    throw new Error(`extension ${extension.extension_id} runner is invalid`);
  }
  if (!isAbsolute(extension.bundle_root)) {
    throw new Error(`extension ${extension.extension_id} bundle_root must be absolute`);
  }
  const root = await realpath(extension.bundle_root);
  const entrypoint = await realpath(resolve(root, extension.entrypoint));
  const escaped = relative(root, entrypoint);
  if (escaped.startsWith("..") || isAbsolute(escaped)) {
    throw new Error(`extension ${extension.extension_id} entrypoint escapes bundle_root`);
  }
  const actual = createHash("sha256").update(await readFile(entrypoint)).digest("hex");
  if (actual !== extension.entrypoint_sha256) {
    throw new Error(`extension ${extension.extension_id} entrypoint digest mismatch`);
  }
  const components = [
    ...(extension.capabilities ?? []),
    ...(extension.channels ?? []),
    ...(extension.event_sources ?? []),
  ];
  if (components.length === 0) {
    throw new Error(`extension ${extension.extension_id} has no components`);
  }
  for (const component of components) {
    if (!DIGEST.test(component.implementation_digest)) {
      throw new Error(`extension ${extension.extension_id} component digest is invalid`);
    }
  }
}

export function hostManifest(config: SupervisorConfig): ExtensionHostManifest {
  return {
    host_protocol_version: "1",
    host: config.host,
    extensions: config.extensions.map((item) => ({
      extension_id: item.extension_id,
      version: item.version,
      build_digest: item.build_digest,
      lockfile_digest: item.lockfile_digest,
      sdk_version: item.sdk_version,
    })),
    capabilities: config.extensions.flatMap((item) => item.capabilities ?? []),
    channels: config.extensions.flatMap((item) => item.channels ?? []),
    event_sources: config.extensions.flatMap((item) => item.event_sources ?? []),
  };
}
