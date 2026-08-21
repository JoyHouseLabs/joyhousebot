import assert from "node:assert/strict";
import test from "node:test";

import {AppClient, JoyHouseBotError} from "../dist/index.js";

test("AppClient exchanges Installation credentials and launches only through v2 EntryPoint", async () => {
  const requests = [];
  const fetch = async (url, init = {}) => {
    requests.push({url: String(url), init});
    if (String(url).endsWith("/v2/app-auth/token")) {
      return Response.json({access_token: "token", expires_at: "2099-01-01T00:00:00Z"});
    }
    return Response.json({
      id: "run_1",
      status: "queued",
      progress: {summary: "", completed: 0, total: 0},
    }, {status: 202});
  };
  const client = new AppClient({
    baseUrl: "https://runtime.example",
    clientId: "app_talent",
    clientSecret: "secret",
    installationId: "appinst_1",
    fetch,
  });
  const handle = await client.run("screen", {candidate_id: "candidate_1"}, {idempotencyKey: "key_1"});

  assert.equal(handle.id, "run_1");
  assert.match(requests[0].url, /\/v2\/app-auth\/token$/);
  assert.match(requests[1].url, /\/v2\/entrypoints\/screen\/runs$/);
  assert.doesNotMatch(requests[1].init.body, /installation_id|grant_id/);
});

test("stable error envelope becomes a typed JoyHouseBotError", () => {
  const error = JoyHouseBotError.fromResponse(409, {
    error: {code: "CONFLICT", message: "already used", retryable: false},
  });
  assert.equal(error.code, "CONFLICT");
  assert.equal(error.statusCode, 409);
  assert.equal(error.retryable, false);
});
