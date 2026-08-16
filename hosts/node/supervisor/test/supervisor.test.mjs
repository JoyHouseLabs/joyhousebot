import assert from "node:assert/strict";
import {createHash} from "node:crypto";
import {copyFile, mkdir, readFile, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {dirname, join} from "node:path";
import test from "node:test";

import {
  canonicalJson,
  requestDigest,
  signRequestBody,
  signResponseBody,
} from "@joyhousebot/extension-sdk";
import {
  createHostServer,
  loadSupervisorConfig,
  NodeExtensionSupervisor,
} from "../dist/index.js";

const FIXTURE = new URL("./fixtures/worker.mjs", import.meta.url);
const digest = (value) => createHash("sha256").update(value).digest("hex");
const sha = (character) => `sha256:${character.repeat(64)}`;

async function setup() {
  const root = join(tmpdir(), `joyhouse-node-host-${process.pid}-${Date.now()}-${Math.random()}`);
  await mkdir(root, {recursive: true});
  const worker = await readFile(FIXTURE);
  const extensions = [];
  for (const [index, label] of ["one", "two", "three"].entries()) {
    const bundle = join(root, label);
    await mkdir(bundle);
    await copyFile(FIXTURE, join(bundle, "worker.mjs"));
    extensions.push({
      extension_id: `fixture-${label}`,
      version: "1.0.0",
      build_digest: sha(String(index + 1)),
      lockfile_digest: sha(String(index + 4)),
      sdk_version: "1",
      bundle_root: bundle,
      entrypoint: "worker.mjs",
      entrypoint_sha256: digest(worker),
      runner: "child_process",
      capabilities: [{
        capability_id: `fixture.${label}`,
        version: "1.0.0",
        implementation_digest: sha("7"),
      }],
      environment: {
        FIXTURE_LABEL: label,
        FIXTURE_STATE_PATH: join(bundle, "operations.json"),
      },
      policy: {request_timeout_ms: 250, startup_timeout_ms: 1000},
    });
  }
  const config = {
    protocol_version: "1",
    host: {host_id: "test-host", version: "0.1.0", build_digest: sha("a")},
    listen: {host: "127.0.0.1", port: 0, base_path: "/joyhousebot/v1"},
    operation_registry_path: join(root, "operations.json"),
    extensions,
  };
  const path = join(root, "host.config.json");
  await writeFile(path, JSON.stringify(config));
  return {root, path, config};
}

function request(capabilityId, input, identity = "action:test") {
  return {
    protocol_version: "1",
    capability: {capability_id: capabilityId, version: "1.0.0", implementation_digest: sha("7")},
    subject: {user_id: "user", agent_id: "agent", session_id: "session"},
    execution: {
      run_id: "run", root_run_id: "run", task_id: null, request_id: "request",
      action_id: "action", idempotency_key: identity, request_digest: sha("f"),
    },
    authorization: {permissions: [], permission_mode: "default"},
    input,
  };
}

test("verifies exact installed entrypoint digest", async () => {
  const {path, config} = await setup();
  config.extensions[0].entrypoint_sha256 = "0".repeat(64);
  await writeFile(path, JSON.stringify(config));
  await assert.rejects(loadSupervisorConfig(path), /entrypoint digest mismatch/);
});

test("accepts lifecycle-only Channel and Event Source components", async () => {
  const {path, config} = await setup();
  config.extensions[0].capabilities = [];
  config.extensions[0].channels = [{
    channel_id: "whatsapp",
    version: "1.0.0",
    implementation_digest: sha("8"),
  }];
  config.extensions[0].event_sources = [{
    event_source_id: "github-webhook",
    version: "1.0.0",
    implementation_digest: sha("9"),
  }];
  await writeFile(path, JSON.stringify(config));
  const loaded = await loadSupervisorConfig(path);
  const supervisor = new NodeExtensionSupervisor(loaded);
  assert.equal(supervisor.manifest.channels[0].channel_id, "whatsapp");
  assert.equal(supervisor.manifest.event_sources[0].event_source_id, "github-webhook");
});

test("isolates three extensions and keeps peers alive after a crash", async () => {
  const {path} = await setup();
  const supervisor = new NodeExtensionSupervisor(await loadSupervisorConfig(path));
  await supervisor.start();
  try {
    const values = await Promise.all(["one", "two", "three"].map((label) =>
      supervisor.invoke(request(`fixture.${label}`, {value: label}, `action:${label}`)),
    ));
    assert.deepEqual(values.map((value) => value.output.label), ["one", "two", "three"]);
    await assert.rejects(
      supervisor.invoke(request("fixture.one", {behavior: "crash"}, "action:crash")),
      /exited/,
    );
    const healthy = await supervisor.invoke(
      request("fixture.two", {value: "still-running"}, "action:healthy"),
    );
    assert.equal(healthy.output.value, "still-running");
  } finally {
    await supervisor.close();
  }
});

test("restores durable operation routing after supervisor restart", async () => {
  const {path} = await setup();
  const config = await loadSupervisorConfig(path);
  const first = new NodeExtensionSupervisor(config);
  await first.start();
  const submitted = await first.invoke(
    request("fixture.one", {async: true, delay_ms: 5, value: "restored"}),
  );
  await first.close();
  await new Promise((resolve) => setTimeout(resolve, 10));

  const second = new NodeExtensionSupervisor(await loadSupervisorConfig(path));
  await second.start();
  try {
    const reconciled = await second.reconcile({
      ...request("fixture.one", {}),
      operation: submitted.operation,
    });
    assert.equal(reconciled.status, "succeeded");
    assert.equal(reconciled.output.value, "restored");
  } finally {
    await second.close();
  }
});

test("kills an event-loop-blocked extension without affecting peers", async () => {
  const {path} = await setup();
  const supervisor = new NodeExtensionSupervisor(await loadSupervisorConfig(path));
  await supervisor.start();
  try {
    await assert.rejects(
      supervisor.invoke(request("fixture.one", {behavior: "block"}, "action:block")),
      /timed out/,
    );
    const healthy = await supervisor.invoke(
      request("fixture.three", {value: "available"}, "action:peer"),
    );
    assert.equal(healthy.output.value, "available");
  } finally {
    await supervisor.close();
  }
});

test("serves the signed Remote Capability transport", async () => {
  const {path} = await setup();
  const supervisor = new NodeExtensionSupervisor(await loadSupervisorConfig(path));
  await supervisor.start();
  const secret = "supervisor-test-secret-that-is-long-enough";
  const server = createHostServer(supervisor, {keyId: "test-key", signingSecret: secret});
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const address = server.address();
    assert.equal(typeof address, "object");
    const base = `http://127.0.0.1:${address.port}`;
    const capability = {
      capability_id: "fixture.one",
      version: "1.0.0",
      implementation_digest: sha("7"),
    };
    const subject = {user_id: "user", agent_id: "agent", session_id: "session"};
    const authorization = {permissions: [], permission_mode: "default"};
    const input = {value: "through-http"};
    const payload = {
      protocol_version: "1",
      capability,
      subject,
      execution: {
        run_id: "run", root_run_id: "run", task_id: null, request_id: "request",
        action_id: "action", idempotency_key: "action:http",
        request_digest: requestDigest(capability, subject, authorization, input),
      },
      authorization,
      input,
    };
    const target = "/joyhousebot/v1/capabilities/fixture.one:invoke";
    const body = Buffer.from(canonicalJson(payload));
    const timestamp = String(Math.floor(Date.now() / 1000));
    const nonce = "signed-http-test";
    const response = await fetch(`${base}${target}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Joyhouse-Timestamp": timestamp,
        "X-Joyhouse-Nonce": nonce,
        "X-Joyhouse-Key-ID": "test-key",
        "X-Joyhouse-Capability-Protocol": "1",
        "X-Joyhouse-Action-ID": "action",
        "Idempotency-Key": "action:http",
        "X-Joyhouse-Signature": signRequestBody({
          method: "POST", path: target, timestamp, nonce, body, secret,
        }),
      },
      body,
    });
    const responseBody = Buffer.from(await response.arrayBuffer());
    assert.equal(response.status, 200);
    assert.equal(
      response.headers.get("X-Joyhouse-Response-Signature"),
      signResponseBody({statusCode: 200, nonce, body: responseBody, secret}),
    );
    assert.equal(JSON.parse(responseBody).output.value, "through-http");
  } finally {
    server.close();
    await supervisor.close();
  }
});
