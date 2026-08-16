import {spawn} from "node:child_process";
import {realpath, rm} from "node:fs/promises";
import {isAbsolute, join, relative, resolve} from "node:path";

import type {WorkspaceDefinition, WorkspaceTestProfile} from "./types.js";

export async function createWorktree(
  definition: WorkspaceDefinition,
  revision: string,
  root: string,
  operationId: string,
): Promise<string> {
  if (!/^[0-9a-f]{7,64}$/.test(revision)) {
    throw new Error("Pi pilot requires an exact hexadecimal Git revision");
  }
  const repository = await realpath(definition.repository);
  if (!isAbsolute(repository)) throw new Error("workspace repository must be absolute");
  const target = resolve(root, operationId);
  const escaped = relative(resolve(root), target);
  if (!escaped || escaped.startsWith("..") || isAbsolute(escaped)) {
    throw new Error("operation workspace escapes the managed root");
  }
  await rm(target, {recursive: true, force: true});
  await run("git", ["-C", repository, "worktree", "add", "--detach", target, revision], repository, 60_000);
  return target;
}

export async function removeWorktree(
  definition: WorkspaceDefinition,
  workspace: string,
): Promise<void> {
  await run(
    "git",
    ["-C", definition.repository, "worktree", "remove", "--force", workspace],
    definition.repository,
    60_000,
  ).catch(() => undefined);
}

export async function gitPatch(workspace: string, maximumBytes = 2 * 1024 * 1024): Promise<string> {
  const result = await run(
    "git",
    ["diff", "--binary", "--no-ext-diff", "--"],
    workspace,
    60_000,
    maximumBytes,
  );
  return result.stdout;
}

export async function runTests(
  workspace: string,
  profile: WorkspaceTestProfile,
): Promise<Record<string, unknown>> {
  const result = await run(
    profile.command,
    profile.args,
    workspace,
    Math.min(30 * 60_000, Math.max(1_000, profile.timeout_ms)),
    1024 * 1024,
    false,
  );
  return {
    command: [profile.command, ...profile.args],
    exit_code: result.exitCode,
    stdout: result.stdout,
    stderr: result.stderr,
    passed: result.exitCode === 0,
  };
}

async function run(
  command: string,
  args: string[],
  cwd: string,
  timeoutMs: number,
  maximumBytes = 1024 * 1024,
  requireSuccess = true,
): Promise<{stdout: string; stderr: string; exitCode: number}> {
  return new Promise((resolveValue, reject) => {
    const child = spawn(command, args, {
      cwd,
      shell: false,
      env: {PATH: process.env.PATH ?? ""},
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = Buffer.alloc(0);
    let stderr = Buffer.alloc(0);
    let bounded = true;
    const append = (current: Buffer, chunk: Buffer) => {
      const next = Buffer.concat([current, chunk]);
      if (next.length > maximumBytes) {
        bounded = false;
        child.kill("SIGKILL");
      }
      return next.subarray(0, maximumBytes);
    };
    child.stdout.on("data", (chunk: Buffer) => { stdout = append(stdout, chunk); });
    child.stderr.on("data", (chunk: Buffer) => { stderr = append(stderr, chunk); });
    const timer = setTimeout(() => child.kill("SIGKILL"), timeoutMs);
    child.once("error", reject);
    child.once("exit", (code) => {
      clearTimeout(timer);
      const result = {
        stdout: stdout.toString("utf8"),
        stderr: stderr.toString("utf8"),
        exitCode: code ?? 1,
      };
      if (!bounded) reject(new Error("managed command output exceeded its byte limit"));
      else if (requireSuccess && result.exitCode !== 0) {
        reject(new Error(`${command} failed with exit code ${result.exitCode}`));
      } else resolveValue(result);
    });
  });
}

export function workspaceRoot(root: string, operationId: string): string {
  return join(resolve(root), operationId);
}
