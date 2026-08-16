import {createHash} from "node:crypto";
import {readFile, writeFile} from "node:fs/promises";

import {canonicalJson, sha256Hex} from "@porthouse/extension-sdk";

import type {
  AllowedCommand,
  Allowlist,
  CapabilityDefinition,
  CompileCatalogOptions,
  CompiledCatalog,
  CompiledCommand,
  OpenCliArgument,
  OpenCliManifestCommand,
} from "./types.js";

const ID_PART = /^[a-z0-9][a-z0-9-]{0,63}$/;
const VERSION = /^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$/;
const DIGEST = /^sha256:[0-9a-f]{64}$/;
const ARGUMENT_NAME = /^[a-z0-9][a-z0-9-]{0,63}$/;
const RESERVED_ARGUMENTS = new Set(["format", "f", "verbose"]);
const PATH_ARGUMENTS = /(?:^|-)(?:file|files|filename|path|paths|dir|directory|output|resume|image|images|video|videos)(?:-|$)/;
const ARGUMENT_TYPES = new Set(["bool", "boolean", "float", "int", "number", "str", "string"]);

export async function compileCatalogFiles(options: {
  manifestPath: string;
  allowlistPath: string;
  outputPath: string;
  metadata: CompileCatalogOptions;
}): Promise<CompiledCatalog> {
  const manifestBytes = await readFile(options.manifestPath);
  const manifest: unknown = JSON.parse(manifestBytes.toString("utf8"));
  const allowlist: unknown = JSON.parse(await readFile(options.allowlistPath, "utf8"));
  const catalog = compileCatalog(manifestBytes, manifest, allowlist, options.metadata);
  await writeFile(options.outputPath, `${canonicalJson(catalog)}\n`, {mode: 0o644});
  return catalog;
}

export function compileCatalog(
  manifestBytes: Buffer,
  manifestValue: unknown,
  allowlistValue: unknown,
  options: CompileCatalogOptions,
): CompiledCatalog {
  validateMetadata(options);
  const manifestSha256 = createHash("sha256").update(manifestBytes).digest("hex");
  if (options.expectedManifestSha256 && manifestSha256 !== options.expectedManifestSha256) {
    throw new Error("OpenCLI manifest digest does not match the frozen release input");
  }
  if (!Array.isArray(manifestValue) || manifestValue.length > 10_000) {
    throw new Error("OpenCLI manifest must be a bounded command array");
  }
  const allowlist = parseAllowlist(allowlistValue);
  const allowedKeys = new Set(allowlist.commands.map(commandKey));
  const source = new Map<string, unknown>();
  for (const value of manifestValue) {
    if (!value || typeof value !== "object" || Array.isArray(value)) continue;
    const item = value as Record<string, unknown>;
    const key = commandKey({site: String(item.site ?? ""), name: String(item.name ?? "")});
    if (!allowedKeys.has(key)) continue;
    if (source.has(key)) throw new Error("OpenCLI manifest has a duplicate allowed command");
    source.set(key, value);
  }

  const commands = allowlist.commands.map((allowed) => {
    const raw = source.get(commandKey(allowed));
    if (!raw) throw new Error(`allowed OpenCLI command is absent: ${allowed.site} ${allowed.name}`);
    return compileCommand(parseManifestCommand(raw), allowed, options);
  });
  if (new Set(commands.map((item) => item.capability.capability_id)).size !== commands.length) {
    throw new Error("OpenCLI allowlist has duplicate capabilities");
  }
  commands.sort((left, right) => left.capability.capability_id.localeCompare(right.capability.capability_id));
  return {
    schema_version: 1,
    extension: {
      extension_id: "capability-opencli",
      version: options.extensionVersion,
      build_digest: options.extensionBuildDigest,
      lockfile_digest: options.extensionLockfileDigest,
      sdk_version: "1",
    },
    runtime: {
      node_version: options.nodeVersion,
      opencli_version: options.openCliVersion,
      opencli_package_integrity: options.openCliPackageIntegrity,
      opencli_entrypoint_sha256: options.openCliEntrypointSha256,
      upstream_manifest_sha256: manifestSha256,
    },
    commands,
  };
}

export function loadCompiledCatalog(value: unknown): CompiledCatalog {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("compiled OpenCLI catalog must be an object");
  }
  const catalog = value as CompiledCatalog;
  if (
    catalog.schema_version !== 1
    || catalog.extension?.extension_id !== "capability-opencli"
    || catalog.extension.sdk_version !== "1"
    || !DIGEST.test(catalog.extension.build_digest)
    || !DIGEST.test(catalog.extension.lockfile_digest)
    || !/^[0-9a-f]{64}$/.test(catalog.runtime?.opencli_entrypoint_sha256 ?? "")
    || !Array.isArray(catalog.commands)
    || catalog.commands.length === 0
    || catalog.commands.length > 2_000
  ) {
    throw new Error("compiled OpenCLI catalog identity is invalid");
  }
  for (const command of catalog.commands) {
    if (
      !ID_PART.test(command.site)
      || !ID_PART.test(command.name)
      || !Array.isArray(command.args)
      || !DIGEST.test(command.capability?.implementation_digest)
      || command.capability.capability_id !== capabilityId(command.site, command.name)
    ) {
      throw new Error("compiled OpenCLI command is invalid");
    }
  }
  return catalog;
}

function parseManifestCommand(value: unknown): OpenCliManifestCommand {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("OpenCLI manifest command must be an object");
  }
  const item = value as Record<string, unknown>;
  const site = String(item.site ?? "");
  const name = String(item.name ?? "");
  const access = String(item.access ?? "");
  if (!ID_PART.test(site) || !ID_PART.test(name) || !["read", "write"].includes(access)) {
    throw new Error(`OpenCLI manifest command identity is invalid: ${site} ${name}`);
  }
  if (!Array.isArray(item.args) || item.args.length > 128) {
    throw new Error(`OpenCLI command ${site} ${name} has invalid args`);
  }
  const args = item.args.map((argument) => parseArgument(argument, site, name));
  if (new Set(args.map((argument) => argument.name)).size !== args.length) {
    throw new Error(`OpenCLI command ${site} ${name} has duplicate args`);
  }
  const columns = item.columns === undefined ? [] : stringArray(item.columns, 256, "columns");
  const domain = item.domain === undefined ? undefined : String(item.domain);
  if (domain && !/^[A-Za-z0-9.-]{1,253}$/.test(domain)) {
    throw new Error(`OpenCLI command ${site} ${name} domain is invalid`);
  }
  return {
    site,
    name,
    description: boundedString(item.description, 2_000),
    access: access as "read" | "write",
    domain,
    strategy: boundedString(item.strategy, 64),
    browser: Boolean(item.browser),
    siteSession: boundedString(item.siteSession, 64),
    defaultWindowMode: boundedString(item.defaultWindowMode, 64),
    args,
    columns,
    type: boundedString(item.type, 64),
    modulePath: boundedString(item.modulePath, 512),
    sourceFile: boundedString(item.sourceFile, 512),
  };
}

function parseArgument(value: unknown, site: string, name: string): OpenCliArgument {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`OpenCLI command ${site} ${name} argument must be an object`);
  }
  const item = value as Record<string, unknown>;
  const argumentName = String(item.name ?? "");
  const type = String(item.type ?? "");
  if (!ARGUMENT_NAME.test(argumentName) || RESERVED_ARGUMENTS.has(argumentName)) {
    throw new Error(`OpenCLI command ${site} ${name} argument name is unsafe`);
  }
  if (!ARGUMENT_TYPES.has(type)) {
    throw new Error(`OpenCLI command ${site} ${name} argument type is unsupported: ${type}`);
  }
  return {
    name: argumentName,
    type: type as OpenCliArgument["type"],
    required: Boolean(item.required),
    positional: Boolean(item.positional),
    ...(item.default === undefined ? {} : {default: item.default}),
    help: boundedString(item.help, 2_000),
    choices: item.choices === undefined ? undefined : scalarArray(item.choices, 256, "choices"),
  };
}

function parseAllowlist(value: unknown): Allowlist {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("OpenCLI allowlist must be an object");
  }
  const item = value as Record<string, unknown>;
  if (item.schema_version !== 1 || !Array.isArray(item.commands) || item.commands.length === 0) {
    throw new Error("OpenCLI allowlist is empty or has an unsupported schema version");
  }
  if (item.commands.length > 2_000) throw new Error("OpenCLI allowlist exceeds policy");
  return {schema_version: 1, commands: item.commands.map(parseAllowedCommand)};
}

function parseAllowedCommand(value: unknown): AllowedCommand {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("OpenCLI allowlist command must be an object");
  }
  const item = value as Record<string, unknown>;
  const known = new Set([
    "site", "name", "capability_version", "timeout_seconds", "expected_duration_seconds",
    "data_classification", "allow_path_arguments",
  ]);
  const unknown = Object.keys(item).filter((key) => !known.has(key));
  if (unknown.length) throw new Error(`OpenCLI allowlist contains unsupported fields: ${unknown.join(", ")}`);
  const site = String(item.site ?? "");
  const name = String(item.name ?? "");
  const version = String(item.capability_version ?? "");
  if (!ID_PART.test(site) || !ID_PART.test(name) || !VERSION.test(version)) {
    throw new Error(`OpenCLI allowlist command identity is invalid: ${site} ${name}`);
  }
  const classification = String(item.data_classification ?? "confidential");
  if (!["public", "internal", "confidential", "restricted"].includes(classification)) {
    throw new Error(`OpenCLI allowlist command ${site} ${name} classification is invalid`);
  }
  return {
    site,
    name,
    capability_version: version,
    timeout_seconds: boundedInteger(item.timeout_seconds, 1, 3_600, 300),
    expected_duration_seconds: boundedInteger(item.expected_duration_seconds, 0, 3_600, 30),
    data_classification: classification as AllowedCommand["data_classification"],
    allow_path_arguments: item.allow_path_arguments === undefined
      ? []
      : stringArray(item.allow_path_arguments, 32, "allow_path_arguments"),
  };
}

function compileCommand(
  upstream: OpenCliManifestCommand,
  allowed: AllowedCommand,
  options: CompileCatalogOptions,
): CompiledCommand {
  const pathArguments = upstream.args.filter((argument) => PATH_ARGUMENTS.test(argument.name));
  const allowedPaths = new Set(allowed.allow_path_arguments ?? []);
  for (const argument of pathArguments) {
    if (!allowedPaths.has(argument.name)) {
      throw new Error(
        `OpenCLI command ${upstream.site} ${upstream.name} path argument ${argument.name} is not explicitly governed`,
      );
    }
  }
  for (const name of allowedPaths) {
    if (!pathArguments.some((argument) => argument.name === name)) {
      throw new Error(`allowed path argument does not exist: ${upstream.site} ${upstream.name} ${name}`);
    }
  }
  const frozen = {
    opencli_version: options.openCliVersion,
    site: upstream.site,
    name: upstream.name,
    access: upstream.access,
    domain: upstream.domain ?? null,
    strategy: upstream.strategy ?? "public",
    browser: Boolean(upstream.browser),
    site_session: upstream.siteSession ?? null,
    default_window_mode: upstream.defaultWindowMode ?? null,
    args: upstream.args,
    columns: upstream.columns ?? [],
    allowed_path_arguments: [...allowedPaths].sort(),
  };
  const capability = buildCapability(upstream, allowed, options, frozen);
  return {...frozen, capability};
}

function buildCapability(
  upstream: OpenCliManifestCommand,
  allowed: AllowedCommand,
  options: CompileCatalogOptions,
  frozen: Record<string, unknown>,
): CapabilityDefinition {
  const properties: Record<string, unknown> = {};
  const required: string[] = [];
  for (const argument of upstream.args) {
    properties[argument.name] = argumentSchema(argument);
    if (argument.required) required.push(argument.name);
  }
  if (upstream.browser) {
    properties.browser_profile_ref = {
      type: "string",
      minLength: 1,
      maxLength: 128,
      pattern: "^[A-Za-z0-9_.:-]+$",
      description: "Explicit local Browser Bridge profile reference",
    };
    required.push("browser_profile_ref");
  }
  const capability_id = capabilityId(upstream.site, upstream.name);
  const implementation_digest = `sha256:${sha256Hex(canonicalJson({
    extension: "capability-opencli",
    extension_version: options.extensionVersion,
    command: frozen,
  }))}`;
  return {
    capability_id,
    version: allowed.capability_version,
    implementation_digest,
    name: `OpenCLI · ${upstream.site} ${upstream.name}`,
    description: upstream.description ?? `Governed OpenCLI ${upstream.site} ${upstream.name} command`,
    input_schema: {
      type: "object",
      properties,
      required: [...new Set(required)],
      additionalProperties: false,
    },
    output_schema: {
      anyOf: [
        {type: "array", maxItems: 10_000, items: {type: "object", additionalProperties: true}},
        {type: "object", additionalProperties: true},
        {type: "string", maxLength: 1_048_576},
        {type: "null"},
      ],
    },
    permissions: [`opencli.${upstream.site}.${upstream.access}`],
    tags: ["opencli", upstream.site, upstream.access, upstream.strategy ?? "public"],
    side_effect: upstream.access === "read" ? "read" : "external",
    idempotent: true,
    retryable: upstream.access === "read",
    data_classification: allowed.data_classification ?? "confidential",
    timeout_seconds: allowed.timeout_seconds ?? 300,
    expected_duration_seconds: allowed.expected_duration_seconds ?? 30,
    invocation_concurrency: upstream.access === "read" ? "parallel_safe" : "sequential",
    max_concurrent_invocations: upstream.access === "read" ? 2 : 1,
    cost_policy: {},
    execution_mode: "durable",
    supports_stream: true,
    provenance: {
      host_protocol_version: "1",
      sdk_version: "1",
      extension_id: "capability-opencli",
      extension_version: options.extensionVersion,
      extension_build_digest: options.extensionBuildDigest,
      extension_lockfile_digest: options.extensionLockfileDigest,
    },
  };
}

function argumentSchema(argument: OpenCliArgument): Record<string, unknown> {
  const description = argument.help ?? argument.name;
  const enumValue = argument.choices?.length ? {enum: argument.choices} : {};
  if (["bool", "boolean"].includes(argument.type)) {
    return {type: "boolean", description, ...enumValue, ...(argument.default === undefined ? {} : {default: argument.default})};
  }
  if (["int"].includes(argument.type)) {
    return {type: "integer", description, ...enumValue, ...(argument.default === undefined ? {} : {default: argument.default})};
  }
  if (["float", "number"].includes(argument.type)) {
    return {type: "number", description, ...enumValue, ...(argument.default === undefined ? {} : {default: argument.default})};
  }
  return {
    type: "string",
    maxLength: 16_384,
    description,
    ...enumValue,
    ...(argument.default === undefined ? {} : {default: argument.default}),
  };
}

function validateMetadata(options: CompileCatalogOptions): void {
  if (!VERSION.test(options.extensionVersion) || !VERSION.test(options.openCliVersion)) {
    throw new Error("catalog compiler requires exact extension and OpenCLI versions");
  }
  if (!DIGEST.test(options.extensionBuildDigest) || !DIGEST.test(options.extensionLockfileDigest)) {
    throw new Error("catalog compiler requires exact extension digests");
  }
  if (!/^v[0-9]+\.[0-9]+\.[0-9]+$/.test(options.nodeVersion)) {
    throw new Error("catalog compiler requires an exact Node version");
  }
  if (
    !options.openCliPackageIntegrity.startsWith("sha512-")
    || !/^[0-9a-f]{64}$/.test(options.openCliEntrypointSha256)
  ) {
    throw new Error("catalog compiler requires OpenCLI package and entrypoint integrity");
  }
  if (options.expectedManifestSha256 && !/^[0-9a-f]{64}$/.test(options.expectedManifestSha256)) {
    throw new Error("expected OpenCLI manifest digest is invalid");
  }
}

function capabilityId(site: string, name: string): string {
  return `opencli.${site}.${name}`;
}

function commandKey(value: {site: string; name: string}): string {
  return `${value.site}\u0000${value.name}`;
}

function boundedString(value: unknown, limit: number): string | undefined {
  if (value === undefined || value === null || value === "") return undefined;
  const result = String(value);
  if (result.length > limit) throw new Error("OpenCLI manifest string exceeds policy");
  return result;
}

function boundedInteger(value: unknown, minimum: number, maximum: number, fallback: number): number {
  if (value === undefined) return fallback;
  const result = Number(value);
  if (!Number.isInteger(result) || result < minimum || result > maximum) {
    throw new Error("OpenCLI allowlist integer exceeds policy");
  }
  return result;
}

function stringArray(value: unknown, limit: number, name: string): string[] {
  if (!Array.isArray(value) || value.length > limit) throw new Error(`OpenCLI ${name} is invalid`);
  return value.map((item) => {
    const result = String(item);
    if (!/^[A-Za-z0-9_.:-]{1,128}$/.test(result)) {
      throw new Error(`OpenCLI ${name} contains an invalid item`);
    }
    return result;
  });
}

function scalarArray(value: unknown, limit: number, name: string): Array<string | number | boolean> {
  if (!Array.isArray(value) || value.length > limit) throw new Error(`OpenCLI ${name} is invalid`);
  return value.map((item) => {
    if (!["string", "number", "boolean"].includes(typeof item)) {
      throw new Error(`OpenCLI ${name} contains a non-scalar item`);
    }
    return item as string | number | boolean;
  });
}
