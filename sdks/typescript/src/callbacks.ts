import {createHmac, timingSafeEqual} from "node:crypto";

export interface VerifiedCallback {
  eventId: string;
  eventType: string;
  payload: Record<string, unknown>;
}

export function verifyCallback(options: {
  headers: Headers | Record<string, string>;
  body: Uint8Array;
  secret: string;
  nowSeconds?: number;
  toleranceSeconds?: number;
}): VerifiedCallback {
  const headers = options.headers instanceof Headers
    ? Object.fromEntries(options.headers.entries())
    : Object.fromEntries(Object.entries(options.headers).map(([key, value]) => [key.toLowerCase(), value]));
  const timestamp = Number.parseInt(headers["x-joyhousebot-timestamp"] ?? "", 10);
  const signature = headers["x-joyhousebot-signature"] ?? "";
  const eventId = headers["x-joyhousebot-event-id"] ?? "";
  const eventType = headers["x-joyhousebot-event-type"] ?? "";
  const now = options.nowSeconds ?? Math.floor(Date.now() / 1000);
  if (!Number.isSafeInteger(timestamp) || Math.abs(now - timestamp) > (options.toleranceSeconds ?? 300)) {
    throw new Error("joyhousebot callback timestamp is outside the allowed window");
  }
  const expected = `v1=${createHmac("sha256", options.secret)
    .update(Buffer.concat([Buffer.from(`${timestamp}.`), Buffer.from(options.body)])).digest("hex")}`;
  if (signature.length !== expected.length || !timingSafeEqual(Buffer.from(signature), Buffer.from(expected))) {
    throw new Error("joyhousebot callback signature does not match");
  }
  const payload = JSON.parse(Buffer.from(options.body).toString("utf8")) as Record<string, unknown>;
  if (payload.event_id !== eventId || payload.event_type !== eventType) {
    throw new Error("joyhousebot callback event identity does not match");
  }
  return {eventId, eventType, payload};
}
