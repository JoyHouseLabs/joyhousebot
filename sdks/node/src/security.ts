import {createHmac, timingSafeEqual} from "node:crypto";

import {requestDigest, sha256Hex} from "./canonical.js";
import type {InvocationRequest, SignedRequestMetadata} from "./types.js";

const PROTOCOL_VERSION = "1";
const DIGEST = /^sha256:[0-9a-f]{64}$/;

export class ProtocolError extends Error {
  readonly code: string;
  readonly httpStatus: number;
  readonly retryable: boolean;

  constructor(code: string, message: string, httpStatus = 400, retryable = false) {
    super(message);
    this.name = "ProtocolError";
    this.code = code;
    this.httpStatus = httpStatus;
    this.retryable = retryable;
  }
}

function hmac(secret: string, value: string): string {
  return createHmac("sha256", secret).update(value, "utf8").digest("hex");
}

function safeEqual(left: string, right: string): boolean {
  const leftBuffer = Buffer.from(left, "utf8");
  const rightBuffer = Buffer.from(right, "utf8");
  return leftBuffer.length === rightBuffer.length && timingSafeEqual(leftBuffer, rightBuffer);
}

export function signRequestBody(options: {
  method: string;
  path: string;
  timestamp: string;
  nonce: string;
  body: Uint8Array;
  secret: string;
}): string {
  const canonical = [
    "JHBCAP-HMAC-SHA256",
    PROTOCOL_VERSION,
    options.method.toUpperCase(),
    options.path,
    options.timestamp,
    options.nonce,
    sha256Hex(options.body),
  ].join("\n");
  return `v1=${hmac(options.secret, canonical)}`;
}

export function signResponseBody(options: {
  statusCode: number;
  nonce: string;
  body: Uint8Array;
  secret: string;
}): string {
  const canonical = [
    "JHBCAP-RESPONSE-HMAC-SHA256",
    PROTOCOL_VERSION,
    String(options.statusCode),
    options.nonce,
    sha256Hex(options.body),
  ].join("\n");
  return `v1=${hmac(options.secret, canonical)}`;
}

export class NonceReplayGuard {
  readonly #expiresAt = new Map<string, number>();
  readonly #ttlSeconds: number;
  readonly #maxEntries: number;

  constructor(ttlSeconds = 300, maxEntries = 10_000) {
    this.#ttlSeconds = ttlSeconds;
    this.#maxEntries = maxEntries;
  }

  accept(nonce: string, nowSeconds: number): boolean {
    for (const [value, expiresAt] of this.#expiresAt) {
      if (expiresAt <= nowSeconds) this.#expiresAt.delete(value);
    }
    if (this.#expiresAt.has(nonce)) return false;
    if (this.#expiresAt.size >= this.#maxEntries) {
      const first = this.#expiresAt.keys().next().value as string | undefined;
      if (first !== undefined) this.#expiresAt.delete(first);
    }
    this.#expiresAt.set(nonce, nowSeconds + this.#ttlSeconds);
    return true;
  }
}

export function verifySignedRequest(options: {
  metadata: SignedRequestMetadata;
  rawBody: Uint8Array;
  secret: string;
  expectedKeyId: string;
  nonceGuard: NonceReplayGuard;
  nowSeconds?: number;
  maxClockSkewSeconds?: number;
}): void {
  const nowSeconds = options.nowSeconds ?? Math.floor(Date.now() / 1000);
  const maxClockSkew = options.maxClockSkewSeconds ?? 300;
  if (options.metadata.protocolVersion !== PROTOCOL_VERSION) {
    throw new ProtocolError("PROTOCOL_MISMATCH", "unsupported capability protocol", 400);
  }
  if (options.metadata.keyId !== options.expectedKeyId) {
    throw new ProtocolError("KEY_ID_INVALID", "capability key id is invalid", 401);
  }
  const timestamp = Number.parseInt(options.metadata.timestamp, 10);
  if (!Number.isSafeInteger(timestamp) || Math.abs(nowSeconds - timestamp) > maxClockSkew) {
    throw new ProtocolError("TIMESTAMP_INVALID", "request timestamp is outside the window", 401);
  }
  const expected = signRequestBody({
    method: options.metadata.method,
    path: options.metadata.path,
    timestamp: options.metadata.timestamp,
    nonce: options.metadata.nonce,
    body: options.rawBody,
    secret: options.secret,
  });
  if (!safeEqual(options.metadata.signature, expected)) {
    throw new ProtocolError("SIGNATURE_INVALID", "request signature is invalid", 401);
  }
  if (!options.nonceGuard.accept(options.metadata.nonce, nowSeconds)) {
    throw new ProtocolError("NONCE_REPLAYED", "request nonce was already used", 409);
  }
}

export function validateInvocationIdentity(request: InvocationRequest, metadata: SignedRequestMetadata): void {
  const execution = request.execution;
  if (!DIGEST.test(execution.request_digest)) {
    throw new ProtocolError("REQUEST_DIGEST_INVALID", "request digest is invalid");
  }
  const expectedDigest = requestDigest(
    request.capability,
    request.subject,
    request.authorization,
    request.input,
  );
  if (!safeEqual(execution.request_digest, expectedDigest)) {
    throw new ProtocolError("REQUEST_DIGEST_MISMATCH", "request digest does not match input", 409);
  }
  if (metadata.idempotencyKey !== execution.idempotency_key) {
    throw new ProtocolError("IDEMPOTENCY_HEADER_MISMATCH", "idempotency header does not match body", 409);
  }
  if ((metadata.actionId ?? null) !== execution.action_id) {
    throw new ProtocolError("ACTION_HEADER_MISMATCH", "action header does not match body", 409);
  }
}
