import assert from "node:assert/strict";
import {spawn} from "node:child_process";
import {createHash} from "node:crypto";
import {mkdir, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import {createInterface} from "node:readline";
import test from "node:test";

import {canonicalJson} from "@joyhousebot/extension-sdk";
import {compileCatalog} from "../dist/index.js";

const WORKER = new URL("../dist/worker.js", import.meta.url).pathname;
const FIXTURE = new URL("./fixtures/fake-opencli.mjs", import.meta.url).pathname;
const sha = (character) => `sha256:${character.repeat(64)}`;

async function setup() {
  const root = join(tmpdir(), `joyhouse-opencli-worker-${process.pid}-${Date.now()}-${Math.random()}`);
  await mkdir(root, {recursive: true});
  const manifest = [{
    site: "fixture",
    name: "read",
    description: "Fixture read",
    access: "read",
    domain: "example.com",
    strategy: "cookie",
    browser: true,
    args: [
      {name: "query", type: "string", required: true, positional: true},
      {name: "mode", type: "string", required: false},
    ],
    columns: ["value"],
  }];
  const manifestBytes = Buffer.from(JSON.stringify(manifest));
  const catalog = compileCatalog(
    manifestBytes,
    manifest,
    {schema_version: 1, commands: [{site: "fixture", name: "read", capability_version: "1.0.0"}]},
    {
      extensionVersion: "0.1.0",
      extensionBuildDigest: sha("1"),
      extensionLockfileDigest: sha("2"),
      nodeVersion: process.version,
      openCliVersion: "1.8.6",
      openCliPackageIntegrity: "sha512-test",
      openCliEntrypointSha256: createHash("sha256").update(await readFile(FIXTURE)).digest("hex"),
    },
  );
  const catalogPath = join(root, "catalog.json");
  await writeFile(catalogPath, canonicalJson(catalog));
  const child = spawn(process.execPath, [WORKER], {
    env: {
      PATH: process.env.PATH,
      OPENCLI_CATALOG_PATH: catalogPath,
      OPENCLI_STATE_PATH: join(root, "operations.json"),
      OPENCLI_WORKSPACE_ROOT: join(root, "workspaces"),
      OPENCLI_ENTRYPOINT: FIXTURE,
      OPENCLI_PACKAGE_JSON: new URL("./fixtures/package.json", import.meta.url).pathname,
    },
    stdio: ["pipe", "pipe", "pipe"],
  });
  const requests = new Map();
  let stderr = "";
  child.stderr.on("data", (chunk) => { stderr += chunk.toString("utf8"); });
  createInterface({input: child.stdout, crlfDelay: Infinity}).on("line", (line) => {
    const response = JSON.parse(line);
    const pending = requests.get(response.id);
    if (!pending) return;
    requests.delete(response.id);
    response.type === "error"
      ? pending.reject(new Error(response.error?.message ?? "worker error"))
      : pending.resolve(response.payload);
  });
  let counter = 0;
  const request = (type, payload = {}) => new Promise((resolveValue, reject) => {
    const id = `request-${counter++}`;
    requests.set(id, {resolve: resolveValue, reject});
    child.stdin.write(`${JSON.stringify({id, type, payload})}\n`);
  });
  const close = async () => {
    child.kill("SIGTERM");
    await new Promise((resolveValue) => child.once("exit", resolveValue));
    await rm(root, {recursive: true, force: true});
  };
  return {catalog, request, close, stderr: () => stderr};
}

function invocation(capability, mode, key = "idempotency-one") {
  return {
    protocol_version: "1",
    capability: {
      capability_id: capability.capability_id,
      version: capability.version,
      implementation_digest: capability.implementation_digest,
    },
    subject: {user_id: "user-one", agent_id: "agent", session_id: "session"},
    execution: {
      run_id: "run", root_run_id: "run", task_id: "task", request_id: "request",
      action_id: null, idempotency_key: key, request_digest: sha("f"),
    },
    authorization: {permissions: ["opencli.fixture.read"], permission_mode: "default"},
    input: {query: "hello", ...(mode ? {mode} : {}), browser_profile_ref: "profile-one"},
  };
}

async function terminal(request, payload, operation) {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const response = await request("reconcile", {...payload, operation});
    if (response.status !== "pending") return response;
    await new Promise((resolveValue) => setTimeout(resolveValue, 10));
  }
  throw new Error("OpenCLI fixture did not reach a terminal state");
}

test("worker preserves operation identity and returns durable JSON observations", async () => {
  const fixture = await setup();
  try {
    const health = await fixture.request("health");
    assert.equal(health.status, "ok");
    const capability = fixture.catalog.commands[0].capability;
    const payload = invocation(capability);
    const accepted = await fixture.request("invoke", payload);
    assert.equal(accepted.status, "accepted");
    const duplicate = await fixture.request("invoke", payload);
    assert.equal(duplicate.operation.operation_id, accepted.operation.operation_id);
    const result = await terminal(fixture.request, payload, accepted.operation);
    assert.equal(result.status, "succeeded");
    assert.equal(result.output.profile, "profile-one");
    assert.ok(result.events.some((event) => event.event_type === "opencli.succeeded"));
    assert.equal(fixture.stderr(), "");
  } finally {
    await fixture.close();
  }
});

test("worker maps expired login to manual reconciliation without replay", async () => {
  const fixture = await setup();
  try {
    const capability = fixture.catalog.commands[0].capability;
    const payload = invocation(capability, "exit-77", "auth-operation");
    const accepted = await fixture.request("invoke", payload);
    const result = await terminal(fixture.request, payload, accepted.operation);
    assert.equal(result.status, "unknown");
    assert.match(result.summary, /Sign in/);
    const repeated = await fixture.request("reconcile", {...payload, operation: accepted.operation});
    assert.equal(repeated.status, "unknown");
  } finally {
    await fixture.close();
  }
});
