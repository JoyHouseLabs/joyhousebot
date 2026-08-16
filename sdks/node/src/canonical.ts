import {createHash} from "node:crypto";

import canonicalize from "canonicalize";

import type {
  CapabilityIdentity,
  InvocationAuthorization,
  InvocationSubject,
} from "./types.js";

export function canonicalJson(value: unknown): string {
  const result = canonicalize(value);
  if (typeof result !== "string") {
    throw new TypeError("value cannot be represented as canonical JSON");
  }
  return result;
}

export function sha256Hex(value: string | Uint8Array): string {
  return createHash("sha256").update(value).digest("hex");
}

export function requestDigest(
  capability: CapabilityIdentity,
  subject: InvocationSubject,
  authorization: InvocationAuthorization,
  input: unknown,
): string {
  const projection = {authorization, capability, input, subject};
  return `sha256:${sha256Hex(canonicalJson(projection))}`;
}
