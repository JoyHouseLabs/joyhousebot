import assert from "node:assert/strict";
import {execFileSync} from "node:child_process";
import {mkdir, readFile, rm, writeFile} from "node:fs/promises";
import {tmpdir} from "node:os";
import {join} from "node:path";
import test from "node:test";

import {runPi} from "../dist/pi-rpc.js";
import {createWorktree, gitPatch, runTests} from "../dist/workspace.js";

const FAKE_PI = new URL("./fixtures/fake-pi.mjs", import.meta.url).pathname;

test("Pi RPC uses only governed tools and keeps the grant out of model config", async () => {
  const root = join(tmpdir(), `joyhousebot-pi-rpc-${process.pid}-${Date.now()}`);
  const workspace = join(root, "workspace");
  await mkdir(workspace, {recursive: true});
  try {
    const result = await runPi({
      entrypoint: FAKE_PI,
      workspace,
      stateRoot: join(root, "state"),
      instruction: "fix the fixture",
      modelId: "test/model",
      contextWindow: 100_000,
      maxOutputTokens: 4096,
      runtimeContext: {
        model_gateway_base_url: "http://127.0.0.1:18794",
        model_grant_token: `jhm_${"s".repeat(64)}`,
      },
      timeoutMs: 5_000,
      signal: new AbortController().signal,
    });
    assert.equal(result.summary, "fixture summary");
    const capture = JSON.parse(await readFile(join(workspace, "pi-capture.json"), "utf8"));
    assert.equal(capture.apiKey, "$JOYHOUSEBOT_MODEL_GRANT");
    assert.equal(capture.tokenAvailable, true);
    assert.ok(capture.argv.includes("read,edit,write,grep,find,ls"));
    assert.ok(capture.argv.includes("--no-extensions"));
    assert.ok(!capture.argv.includes("bash"));
    const modelBytes = await readFile(join(root, "state", "pi-agent", "models.json"), "utf8");
    assert.ok(!modelBytes.includes("jhm_"));
  } finally {
    await rm(root, {recursive: true, force: true});
  }
});

test("managed worktree returns a reviewable patch and allowlisted test evidence", async () => {
  const root = join(tmpdir(), `joyhousebot-pi-worktree-${process.pid}-${Date.now()}`);
  const repository = join(root, "repository");
  const worktrees = join(root, "worktrees");
  await mkdir(repository, {recursive: true});
  execFileSync("git", ["init"], {cwd: repository});
  execFileSync("git", ["config", "user.name", "Fixture"], {cwd: repository});
  execFileSync("git", ["config", "user.email", "fixture@example.invalid"], {cwd: repository});
  await writeFile(join(repository, "value.txt"), "before\n");
  execFileSync("git", ["add", "value.txt"], {cwd: repository});
  execFileSync("git", ["commit", "-m", "fixture"], {cwd: repository});
  const revision = execFileSync("git", ["rev-parse", "HEAD"], {cwd: repository, encoding: "utf8"}).trim();
  const definition = {
    repository,
    tests: {fixture: {command: process.execPath, args: ["-e", "process.exit(0)"], timeout_ms: 5_000}},
  };
  try {
    const workspace = await createWorktree(definition, revision, worktrees, "operation-one");
    await writeFile(join(workspace, "value.txt"), "after\n");
    const patch = await gitPatch(workspace);
    assert.match(patch, /-before/);
    assert.match(patch, /\+after/);
    const evidence = await runTests(workspace, definition.tests.fixture);
    assert.equal(evidence.passed, true);
  } finally {
    await rm(root, {recursive: true, force: true});
  }
});
