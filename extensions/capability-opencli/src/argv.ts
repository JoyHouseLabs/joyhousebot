import {isAbsolute, relative, resolve} from "node:path";

import type {CompiledCommand, OpenCliArgument} from "./types.js";

const URL_ARGUMENT = /(?:^|-)urls?(?:-|$)/;

export function buildOpenCliArgv(
  command: CompiledCommand,
  input: unknown,
  workspaceRoot: string,
): {argv: string[]; profile: string | null} {
  if (!input || typeof input !== "object" || Array.isArray(input)) {
    throw new Error("OpenCLI capability input must be an object");
  }
  const values = input as Record<string, unknown>;
  const allowed = new Set(command.args.map((argument) => argument.name));
  if (command.browser) allowed.add("browser_profile_ref");
  const unknown = Object.keys(values).filter((name) => !allowed.has(name));
  if (unknown.length) throw new Error(`OpenCLI capability input contains unknown fields: ${unknown.join(", ")}`);

  const profile = command.browser ? parseProfile(values.browser_profile_ref) : null;
  const positional: string[] = [];
  const options: string[] = [];
  for (const argument of command.args) {
    const supplied = Object.prototype.hasOwnProperty.call(values, argument.name);
    const value = supplied ? values[argument.name] : argument.default;
    if (value === undefined || value === null) {
      if (argument.required) throw new Error(`OpenCLI argument is required: ${argument.name}`);
      continue;
    }
    const serialized = serializeArgument(argument, value);
    if (["bool", "boolean"].includes(argument.type)) {
      if (serialized === "true") options.push(`--${argument.name}`);
      continue;
    }
    const safeValue = command.allowed_path_arguments.includes(argument.name)
      ? serialized.split(",").map((item) => resolveManagedPath(workspaceRoot, item)).join(",")
      : URL_ARGUMENT.test(argument.name)
        ? validateTargetUrls(serialized, command.domain)
        : serialized;
    if (argument.positional) {
      if (safeValue.startsWith("-")) {
        throw new Error(`OpenCLI positional argument cannot begin with '-': ${argument.name}`);
      }
      positional.push(safeValue);
    } else {
      options.push(`--${argument.name}=${safeValue}`);
    }
  }
  return {argv: [command.site, command.name, ...positional, ...options, "--format=json"], profile};
}

function serializeArgument(argument: OpenCliArgument, value: unknown): string {
  if (["bool", "boolean"].includes(argument.type)) {
    if (typeof value !== "boolean") throw new Error(`OpenCLI argument ${argument.name} must be boolean`);
    return String(value);
  }
  if (argument.type === "int") {
    if (typeof value !== "number" || !Number.isSafeInteger(value)) {
      throw new Error(`OpenCLI argument ${argument.name} must be a safe integer`);
    }
    return String(value);
  }
  if (["float", "number"].includes(argument.type)) {
    if (typeof value !== "number" || !Number.isFinite(value)) {
      throw new Error(`OpenCLI argument ${argument.name} must be a finite number`);
    }
    return String(value);
  }
  if (typeof value !== "string" || Buffer.byteLength(value, "utf8") > 16_384 || value.includes("\0")) {
    throw new Error(`OpenCLI argument ${argument.name} must be a bounded string`);
  }
  return value;
}

function parseProfile(value: unknown): string {
  if (typeof value !== "string" || !/^[A-Za-z0-9_.:-]{1,128}$/.test(value)) {
    throw new Error("browser_profile_ref must explicitly identify one local profile");
  }
  return value;
}

function resolveManagedPath(root: string, value: string): string {
  if (!value || value.includes("\0") || isAbsolute(value)) {
    throw new Error("OpenCLI path arguments must be relative to the operation workspace");
  }
  const target = resolve(root, value);
  const escaped = relative(root, target);
  if (!escaped || escaped.startsWith("..") || isAbsolute(escaped)) {
    throw new Error("OpenCLI path argument escapes the operation workspace");
  }
  return target;
}

function validateTargetUrls(value: string, domain: string | null): string {
  if (!domain) throw new Error("OpenCLI URL argument requires a frozen target domain");
  for (const item of value.split(",")) {
    let parsed: URL;
    try {
      parsed = new URL(item);
    } catch {
      throw new Error("OpenCLI URL argument must contain absolute HTTPS URLs");
    }
    const hostname = parsed.hostname.toLowerCase();
    const expected = domain.toLowerCase();
    if (parsed.protocol !== "https:" || (hostname !== expected && !hostname.endsWith(`.${expected}`))) {
      throw new Error("OpenCLI URL argument is outside the frozen target domain");
    }
    if (parsed.username || parsed.password) throw new Error("OpenCLI URL argument cannot include credentials");
  }
  return value;
}
