import assert from "node:assert/strict";
import {mkdir, readFile, rm} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";

import {
  buildOpenCliArgv,
  compileCatalog,
  preflightOpenCli,
  runOpenCli,
} from "../dist/index.js";

const FIXTURE = new URL("./fixtures/fake-opencli.mjs", import.meta.url).pathname;
const sha = (character) => `sha256:${character.repeat(64)}`;
const metadata = {
  extensionVersion: "0.1.0",
  extensionBuildDigest: sha("1"),
  extensionLockfileDigest: sha("2"),
  nodeVersion: process.version,
  openCliVersion: "1.8.6",
  openCliPackageIntegrity: "sha512-test",
  openCliEntrypointSha256: "3".repeat(64),
};

function command(overrides = {}) {
  return {
    site: "fixture",
    name: "read",
    description: "Fixture command",
    access: "read",
    domain: "example.com",
    strategy: "cookie",
    browser: true,
    args: [
      {name: "query", type: "string", required: true, positional: true},
      {name: "mode", type: "string", required: false},
      {name: "limit", type: "int", required: false},
      {name: "all", type: "bool", required: false},
    ],
    columns: ["value"],
    ...overrides,
  };
}

function compile(manifest = [command()], allow = [{site: "fixture", name: "read", capability_version: "1.0.0"}]) {
  const bytes = Buffer.from(JSON.stringify(manifest));
  return compileCatalog(bytes, manifest, {schema_version: 1, commands: allow}, metadata);
}

test("compiles exact OpenCLI commands into governed capabilities", () => {
  const catalog = compile();
  assert.equal(catalog.commands.length, 1);
  const compiled = catalog.commands[0];
  assert.equal(compiled.capability.capability_id, "opencli.fixture.read");
  assert.equal(compiled.capability.side_effect, "read");
  assert.equal(compiled.capability.input_schema.additionalProperties, false);
  assert.deepEqual(compiled.capability.input_schema.required, ["query", "browser_profile_ref"]);
  assert.match(compiled.capability.implementation_digest, /^sha256:[0-9a-f]{64}$/);
});

test("rejects path arguments unless the release explicitly governs them", () => {
  const unsafe = command({args: [{name: "output", type: "string", required: false}]});
  assert.throws(() => compile([unsafe]), /path argument output is not explicitly governed/);
  const catalog = compile([unsafe], [{
    site: "fixture", name: "read", capability_version: "1.0.0", allow_path_arguments: ["output"],
  }]);
  assert.deepEqual(catalog.commands[0].allowed_path_arguments, ["output"]);
});

test("builds argv without a shell and rejects flag injection", () => {
  const compiled = compile().commands[0];
  const built = buildOpenCliArgv(compiled, {
    query: "safe query", limit: 3, all: true, browser_profile_ref: "chrome-main",
  }, "/tmp/opencli-operation");
  assert.deepEqual(built.argv, [
    "fixture", "read", "safe query", "--limit=3", "--all", "--format=json",
  ]);
  assert.equal(built.profile, "chrome-main");
  assert.throws(() => buildOpenCliArgv(compiled, {
    query: "--format=table", browser_profile_ref: "chrome-main",
  }, "/tmp/opencli-operation"), /cannot begin/);
  assert.throws(() => buildOpenCliArgv(compiled, {
    query: "safe", arbitrary: "--help", browser_profile_ref: "chrome-main",
  }, "/tmp/opencli-operation"), /unknown fields/);
});

test("maps OpenCLI JSON, empty, auth, bridge, and timeout outcomes", async () => {
  const compiled = compile().commands[0];
  const workspace = join(tmpdir(), `porthouse-opencli-${process.pid}-${Date.now()}`);
  await mkdir(workspace, {recursive: true});
  const execute = (mode, timeoutMs = 2_000) => runOpenCli({
    entrypoint: FIXTURE,
    command: compiled,
    input: {query: "hello", mode, browser_profile_ref: "profile-one"},
    workspace,
    timeoutMs,
    maxStdoutBytes: 16_384,
    maxStderrBytes: 16_384,
  }).result;
  try {
    const success = await execute(undefined);
    assert.equal(success.state, "succeeded");
    assert.equal(success.output.profile, "profile-one");
    assert.ok(success.output.args.includes("--format=json"));
    assert.deepEqual((await execute("empty")).output, []);
    assert.equal((await execute("exit-69")).error.code, "OPENCLI_BRIDGE_UNAVAILABLE");
    assert.equal((await execute("exit-77")).state, "manual_required");
    assert.equal((await execute("slow", 20)).error.code, "OPENCLI_TIMEOUT");
  } finally {
    await rm(workspace, {recursive: true, force: true});
  }
});

test("preflight verifies exact Node, OpenCLI, Bridge, and profile", async () => {
  const result = await preflightOpenCli({
    entrypoint: FIXTURE,
    expectedNodeVersion: process.version,
    expectedOpenCliVersion: "1.8.6",
    profile: "profile-two",
  });
  assert.equal(result.ready, true);
  assert.equal(result.browser_bridge.ready, true);
  assert.equal(result.opencli.actual, "1.8.6");
});

test("runtime lock pins the packaged OpenCLI release", async () => {
  const lock = JSON.parse(await readFile(new URL("../catalog/runtime-lock.json", import.meta.url)));
  assert.equal(lock.node.version, "v24.19.0");
  assert.equal(lock.node.release_status, "lts");
  assert.equal(lock.opencli.version, "1.8.6");
  assert.match(lock.opencli.npm_integrity, /^sha512-/);
});
