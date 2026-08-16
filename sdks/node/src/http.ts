import type {IncomingHttpHeaders, IncomingMessage} from "node:http";

import {ProtocolError} from "./security.js";
import type {SignedRequestMetadata} from "./types.js";

function header(headers: IncomingHttpHeaders, name: string, required = true): string {
  const value = headers[name.toLowerCase()];
  const result = Array.isArray(value) ? value[0] : value;
  if (required && !result) {
    throw new ProtocolError("HEADER_REQUIRED", `${name} header is required`, 400);
  }
  return result ?? "";
}

export function requestMetadata(request: IncomingMessage): SignedRequestMetadata {
  const actionId = header(request.headers, "X-Porthouse-Action-ID", false);
  return {
    method: request.method ?? "POST",
    path: request.url ?? "/",
    timestamp: header(request.headers, "X-Porthouse-Timestamp"),
    nonce: header(request.headers, "X-Porthouse-Nonce"),
    signature: header(request.headers, "X-Porthouse-Signature"),
    keyId: header(request.headers, "X-Porthouse-Key-ID"),
    protocolVersion: header(request.headers, "X-Porthouse-Capability-Protocol"),
    idempotencyKey: header(request.headers, "Idempotency-Key"),
    ...(actionId ? {actionId} : {}),
  };
}

export async function readBoundedBody(request: IncomingMessage, maxBytes = 1024 * 1024): Promise<Buffer> {
  const chunks: Buffer[] = [];
  let total = 0;
  for await (const chunk of request) {
    const buffer = Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk);
    total += buffer.length;
    if (total > maxBytes) {
      throw new ProtocolError("REQUEST_TOO_LARGE", "request body exceeds configured limit", 413);
    }
    chunks.push(buffer);
  }
  return Buffer.concat(chunks);
}
