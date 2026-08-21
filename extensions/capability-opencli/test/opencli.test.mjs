import assert from "node:assert/strict";
import {mkdir, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";

import {
  buildOpenCliArgv,
  captureMarkdownArtifact,
  compileCatalog,
  preflightOpenCli,
  runOpenCli,
  runXiaohongshuAccountSnapshot,
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
  assert.equal(buildOpenCliArgv(compiled, {
    query: "safe query", browser_profile_ref: "auto",
  }, "/tmp/opencli-operation").profile, null);
  assert.throws(() => buildOpenCliArgv(compiled, {
    query: "--format=table", browser_profile_ref: "chrome-main",
  }, "/tmp/opencli-operation"), /cannot begin/);
  assert.throws(() => buildOpenCliArgv(compiled, {
    query: "safe", arbitrary: "--help", browser_profile_ref: "chrome-main",
  }, "/tmp/opencli-operation"), /unknown fields/);
});

test("enforces release-governed URL and count constraints in schema and argv", () => {
  const catalog = compile([command({
    site: "xiaohongshu",
    name: "user",
    args: [
      {name: "id", type: "string", required: true, positional: true},
      {name: "limit", type: "int", default: 15},
    ],
  })], [{
    site: "xiaohongshu",
    name: "user",
    capability_version: "1.0.0",
    argument_constraints: {
      id: {pattern: "^(?:[A-Za-z0-9]+|https://www\\.xiaohongshu\\.com/user/profile/[A-Za-z0-9]+/?)$"},
      limit: {minimum: 1, maximum: 20},
    },
  }]);
  const compiled = catalog.commands[0];
  assert.equal(compiled.capability.input_schema.properties.limit.maximum, 20);
  assert.deepEqual(buildOpenCliArgv(compiled, {
    id: "https://www.xiaohongshu.com/user/profile/abc123",
    limit: 20,
    browser_profile_ref: "chrome-main",
  }, "/tmp/opencli-operation").argv, [
    "xiaohongshu", "user", "https://www.xiaohongshu.com/user/profile/abc123",
    "--limit=20", "--format=json",
  ]);
  assert.throws(() => buildOpenCliArgv(compiled, {
    id: "https://evil.example/user/profile/abc123",
    limit: 20,
    browser_profile_ref: "chrome-main",
  }, "/tmp/opencli-operation"), /outside the governed allowlist/);
  assert.throws(() => buildOpenCliArgv(compiled, {
    id: "abc123",
    limit: 21,
    browser_profile_ref: "chrome-main",
  }, "/tmp/opencli-operation"), /governed maximum/);
});

test("builds a semantic account snapshot from internal list and detail commands", async () => {
  const manifest = [
    command({
      site: "xiaohongshu", name: "user",
      args: [
        {name: "id", type: "string", required: true, positional: true},
        {name: "limit", type: "int", default: 15},
      ],
    }),
    command({
      site: "xiaohongshu", name: "note",
      args: [{name: "note-id", type: "string", required: true, positional: true}],
    }),
  ];
  const bytes = Buffer.from(JSON.stringify(manifest));
  const catalog = compileCatalog(bytes, manifest, {
    schema_version: 1,
    commands: [
      {site: "xiaohongshu", name: "user", capability_version: "1.0.0", exposed: false},
      {site: "xiaohongshu", name: "note", capability_version: "1.0.0", exposed: false},
    ],
    account_snapshots: [{platform: "xiaohongshu", capability_version: "1.0.0"}],
  }, metadata);
  assert.equal(catalog.account_snapshots[0].capability.capability_id, "social.xiaohongshu.account-snapshot");
  assert.ok(catalog.commands.every((item) => item.exposed === false));

  const workspace = join(tmpdir(), `joyhousebot-xhs-snapshot-${process.pid}-${Date.now()}`);
  await mkdir(workspace, {recursive: true});
  const progress = [];
  try {
    const running = runXiaohongshuAccountSnapshot({
      entrypoint: FIXTURE,
      listCommand: catalog.commands.find((item) => item.name === "user"),
      detailCommand: catalog.commands.find((item) => item.name === "note"),
      input: {
        profile_url: "https://www.xiaohongshu.com/user/profile/user123",
        limit: 1,
        page_delay_seconds: 2,
        browser_profile_ref: "chrome-main",
      },
      workspace,
      timeoutMs: 10_000,
      maxStdoutBytes: 16_384,
      maxStderrBytes: 16_384,
      onProgress: async (event) => { progress.push(event); },
    });
    const result = await running.result;
    assert.equal(result.state, "succeeded");
    assert.equal(result.output.complete_text_count, 1);
    assert.equal(result.output.notes[0].content, "这是完整正文。");
    assert.equal(result.output.notes[0].url, "https://www.xiaohongshu.com/user/profile/user123/note123");
    assert.doesNotMatch(JSON.stringify(result.output), /secret_token/);
    assert.deepEqual(progress.map((event) => event.status), ["listed", "complete"]);

    process.env.FAKE_XHS_NOTE_EXIT = "77";
    const resumedProgress = [];
    const resumed = runXiaohongshuAccountSnapshot({
      entrypoint: FIXTURE,
      listCommand: catalog.commands.find((item) => item.name === "user"),
      detailCommand: catalog.commands.find((item) => item.name === "note"),
      input: {
        profile_url: "https://www.xiaohongshu.com/user/profile/user123",
        limit: 1,
        page_delay_seconds: 2,
        browser_profile_ref: "chrome-main",
      },
      checkpoint: result.output,
      workspace,
      timeoutMs: 10_000,
      maxStdoutBytes: 16_384,
      maxStderrBytes: 16_384,
      onProgress: async (event) => { resumedProgress.push(event); },
    });
    const resumedResult = await resumed.result;
    assert.equal(resumedResult.state, "succeeded");
    assert.equal(resumedResult.output.complete_text_count, 1);
    assert.deepEqual(resumedProgress.map((event) => event.status), ["listed"]);
  } finally {
    delete process.env.FAKE_XHS_NOTE_EXIT;
    await rm(workspace, {recursive: true, force: true});
  }
});

test("uses an explicit negative flag for reviewed true-default booleans", () => {
  const compiled = compile([command({
    args: [{name: "download-images", type: "boolean", default: true}],
  })]).commands[0];
  const built = buildOpenCliArgv(compiled, {
    "download-images": false,
    browser_profile_ref: "chrome-main",
  }, "/tmp/opencli-operation");
  assert.deepEqual(built.argv, ["fixture", "read", "--no-download-images", "--format=json"]);
});

test("Markdown capture forbids image download side effects", () => {
  const compiled = compile([command({
    args: [
      {name: "output", type: "string", default: "./output"},
      {name: "download-images", type: "boolean", default: true},
    ],
  })], [{
    site: "fixture", name: "read", capability_version: "1.0.0",
    capture_output_markdown: true, allow_path_arguments: ["output"],
  }]).commands[0];
  assert.throws(() => buildOpenCliArgv(compiled, {
    "download-images": true,
    browser_profile_ref: "chrome-main",
  }, "/tmp/opencli-operation"), /forbids image downloads/);
  assert.equal(
    compiled.capability.input_schema.properties["download-images"].default,
    false,
  );
});

test("captures one bounded Markdown output as an Artifact instead of a host path", async () => {
  const workspace = join(tmpdir(), `joyhousebot-opencli-capture-${process.pid}-${Date.now()}`);
  await mkdir(join(workspace, "article"), {recursive: true});
  await writeFile(join(workspace, "article", "post.md"), "# Captured");
  try {
    const artifacts = await captureMarkdownArtifact({
      workspace,
      operationId: "opencli-one",
      capabilityId: "opencli.weixin.download",
      sourceUrl: "https://mp.weixin.qq.com/s/example",
    });
    assert.equal(artifacts.length, 1);
    assert.equal(artifacts[0].data.content, "# Captured");
    assert.equal(artifacts[0].metadata.source_url, "https://mp.weixin.qq.com/s/example");
    assert.equal(artifacts[0].evidence.relative_path, "article/post.md");
  } finally {
    await rm(workspace, {recursive: true, force: true});
  }
});

test("maps OpenCLI JSON, empty, auth, bridge, and timeout outcomes", async () => {
  const compiled = compile().commands[0];
  const workspace = join(tmpdir(), `joyhousebot-opencli-${process.pid}-${Date.now()}`);
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
