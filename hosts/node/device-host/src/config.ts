import {readFile} from "node:fs/promises";
import {resolve} from "node:path";

import type {DeviceHostConfig} from "./types.js";

interface RawConfig {
  runtime_base_url?: unknown;
  model_gateway_base_url?: unknown;
  device_id?: unknown;
  device_token_ref?: unknown;
  host_revision?: unknown;
  host_manifest_digest?: unknown;
  poll_interval_ms?: unknown;
  claim_lease_seconds?: unknown;
  local_host?: Record<string, unknown>;
}

export async function loadDeviceHostConfig(path: string): Promise<DeviceHostConfig> {
  const raw = JSON.parse(await readFile(resolve(path), "utf8")) as RawConfig;
  const runtimeBaseUrl = safeUrl(raw.runtime_base_url, "runtime_base_url", false);
  const deviceId = identifier(raw.device_id, "device_id");
  const deviceToken = environmentSecret(raw.device_token_ref, "device_token_ref", 40);
  const local = raw.local_host;
  if (!local || typeof local !== "object" || Array.isArray(local)) {
    throw new Error("local_host must be an object");
  }
  return {
    runtimeBaseUrl,
    modelGatewayBaseUrl: safeUrl(
      raw.model_gateway_base_url ?? "http://127.0.0.1:18794",
      "model_gateway_base_url",
      true,
    ),
    deviceId,
    deviceToken,
    hostRevision: boundedString(raw.host_revision, "host_revision", 256),
    hostManifestDigest: digest(raw.host_manifest_digest, "host_manifest_digest"),
    pollIntervalMs: integer(raw.poll_interval_ms ?? 2_000, 250, 60_000, "poll_interval_ms"),
    claimLeaseSeconds: integer(
      raw.claim_lease_seconds ?? 60,
      10,
      300,
      "claim_lease_seconds",
    ),
    localHost: {
      baseUrl: safeUrl(local.base_url, "local_host.base_url", true),
      basePath: basePath(local.base_path),
      keyId: identifier(local.key_id, "local_host.key_id"),
      signingSecret: environmentSecret(
        local.signing_secret_ref,
        "local_host.signing_secret_ref",
        32,
      ),
      requireResponseSignature: local.require_response_signature !== false,
    },
  };
}

function boundedString(value: unknown, field: string, maximum: number): string {
  const normalized = String(value ?? "").trim();
  if (!normalized || normalized.length > maximum) throw new Error(`${field} is invalid`);
  return normalized;
}

function digest(value: unknown, field: string): string {
  const normalized = String(value ?? "").trim();
  if (!/^sha256:[0-9a-f]{64}$/.test(normalized)) {
    throw new Error(`${field} must be sha256:<64 lowercase hex>`);
  }
  return normalized;
}

function environmentSecret(value: unknown, field: string, minimum: number): string {
  const reference = String(value ?? "").trim();
  const match = /^env:\/\/([A-Za-z_][A-Za-z0-9_]*)$/.exec(reference);
  if (!match) throw new Error(`${field} must use env://VARIABLE`);
  const secret = process.env[match[1]] ?? "";
  if (Buffer.byteLength(secret, "utf8") < minimum) {
    throw new Error(`${field} environment value is missing or too short`);
  }
  return secret;
}

function safeUrl(value: unknown, field: string, localOnly: boolean): string {
  const parsed = new URL(String(value ?? "").trim());
  const loopback = ["127.0.0.1", "::1", "localhost"].includes(parsed.hostname);
  if (parsed.protocol !== "https:" && !(parsed.protocol === "http:" && loopback)) {
    throw new Error(`${field} requires HTTPS; HTTP is loopback-only`);
  }
  if (localOnly && !loopback) throw new Error(`${field} must point to a loopback Host`);
  if (parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error(`${field} cannot contain credentials, query, or fragment`);
  }
  return parsed.toString().replace(/\/$/, "");
}

function identifier(value: unknown, field: string): string {
  const normalized = String(value ?? "").trim();
  if (!/^[A-Za-z0-9_.:-]{1,128}$/.test(normalized)) {
    throw new Error(`${field} is invalid`);
  }
  return normalized;
}

function integer(value: unknown, minimum: number, maximum: number, field: string): number {
  const parsed = Number(value);
  if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error(`${field} must be an integer between ${minimum} and ${maximum}`);
  }
  return parsed;
}

function basePath(value: unknown): string {
  const normalized = String(value ?? "/joyhousebot/v1").trim().replace(/\/$/, "");
  if (!normalized.startsWith("/") || normalized.includes("..")) {
    throw new Error("local_host.base_path is invalid");
  }
  return normalized;
}
