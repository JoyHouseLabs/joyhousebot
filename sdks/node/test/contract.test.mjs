import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import test from "node:test";

import {
  canonicalJson,
  NonceReplayGuard,
  ProtocolError,
  requestDigest,
  signRequestBody,
  signResponseBody,
  validateInvocationIdentity,
  verifySignedRequest,
} from "../dist/index.js";

const fixtureUrl = new URL(
  "../../../tests/contract/extension-host/signature-vectors.json",
  import.meta.url,
);
const vector = JSON.parse(await readFile(fixtureUrl, "utf8"));

test("Node SDK matches shared request signature and digest vectors", () => {
  const request = vector.request;
  const body = request.body;
  assert.equal(canonicalJson(body), request.canonical_body);
  assert.equal(
    requestDigest(body.capability, body.subject, body.authorization, body.input),
    request.request_digest,
  );
  assert.equal(
    signRequestBody({
      method: request.method,
      path: request.path,
      timestamp: request.timestamp,
      nonce: request.nonce,
      body: Buffer.from(request.canonical_body, "utf8"),
      secret: vector.secret,
    }),
    request.signature,
  );
});

test("Node SDK matches shared response signature vector", () => {
  const response = vector.response;
  assert.equal(canonicalJson(response.body), response.canonical_body);
  assert.equal(
    signResponseBody({
      statusCode: response.status_code,
      nonce: vector.request.nonce,
      body: Buffer.from(response.canonical_body, "utf8"),
      secret: vector.secret,
    }),
    response.signature,
  );
});

test("Node SDK verifies signatures and rejects nonce replay", () => {
  const request = vector.request;
  const rawBody = Buffer.from(request.canonical_body, "utf8");
  const metadata = {
    method: request.method,
    path: request.path,
    timestamp: request.timestamp,
    nonce: request.nonce,
    signature: request.signature,
    keyId: "test-key",
    protocolVersion: "1",
    idempotencyKey: request.body.execution.idempotency_key,
  };
  const nonceGuard = new NonceReplayGuard();
  verifySignedRequest({
    metadata,
    rawBody,
    secret: vector.secret,
    expectedKeyId: "test-key",
    nonceGuard,
    nowSeconds: Number(request.timestamp),
  });
  assert.throws(
    () => verifySignedRequest({
      metadata,
      rawBody,
      secret: vector.secret,
      expectedKeyId: "test-key",
      nonceGuard,
      nowSeconds: Number(request.timestamp),
    }),
    (error) => error instanceof ProtocolError && error.code === "NONCE_REPLAYED",
  );
});

test("Node SDK rejects a digest that does not freeze the input", () => {
  const request = structuredClone(vector.request.body);
  request.input.message = "changed";
  assert.throws(
    () => validateInvocationIdentity(request, {
      method: "POST",
      path: vector.request.path,
      timestamp: vector.request.timestamp,
      nonce: "another-nonce",
      signature: vector.request.signature,
      keyId: "test-key",
      protocolVersion: "1",
      idempotencyKey: request.execution.idempotency_key,
    }),
    (error) => error instanceof ProtocolError && error.code === "REQUEST_DIGEST_MISMATCH",
  );
});
