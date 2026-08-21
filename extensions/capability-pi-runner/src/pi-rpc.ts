import {spawn} from "node:child_process";
import {mkdir, writeFile} from "node:fs/promises";
import {join} from "node:path";

import type {PiRuntimeContext} from "./types.js";

export async function runPi(options: {
  entrypoint: string;
  workspace: string;
  stateRoot: string;
  instruction: string;
  modelId: string;
  contextWindow: number;
  maxOutputTokens: number;
  runtimeContext: PiRuntimeContext;
  timeoutMs: number;
  signal: AbortSignal;
}): Promise<{summary: string; event_count: number}> {
  const agentDir = join(options.stateRoot, "pi-agent");
  await mkdir(agentDir, {recursive: true, mode: 0o700});
  await writeFile(
    join(agentDir, "models.json"),
    JSON.stringify({
      providers: {
        joyhousebot: {
          baseUrl: `${options.runtimeContext.model_gateway_base_url}/v1`,
          api: "openai-completions",
          apiKey: "$JOYHOUSEBOT_MODEL_GRANT",
          authHeader: true,
          compat: {supportsDeveloperRole: false, supportsReasoningEffort: false},
          models: [{
            id: options.modelId,
            name: "joyhousebot governed model",
            reasoning: false,
            input: ["text"],
            contextWindow: options.contextWindow,
            maxTokens: options.maxOutputTokens,
            cost: {input: 0, output: 0, cacheRead: 0, cacheWrite: 0},
          }],
        },
      },
    }),
    {mode: 0o600},
  );
  const argv = [
    options.entrypoint,
    "--mode", "rpc",
    "--provider", "joyhousebot",
    "--model", options.modelId,
    "--no-session",
    "--no-extensions",
    "--no-skills",
    "--no-prompt-templates",
    "--no-themes",
    "--no-context-files",
    "--tools", "read,edit,write,grep,find,ls",
    "--no-approve",
  ];
  return new Promise((resolveValue, reject) => {
    const child = spawn(process.execPath, argv, {
      cwd: options.workspace,
      shell: false,
      env: {
        PATH: process.env.PATH ?? "",
        PI_CODING_AGENT_DIR: agentDir,
        JOYHOUSEBOT_MODEL_GRANT: options.runtimeContext.model_grant_token,
      },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = Buffer.alloc(0);
    let stderr = Buffer.alloc(0);
    let buffer = "";
    let eventCount = 0;
    let lastAssistant = "";
    let completed = false;
    const finish = (error?: Error) => {
      if (completed) return;
      completed = true;
      clearTimeout(timer);
      child.kill("SIGTERM");
      error ? reject(error) : resolveValue({summary: lastAssistant.slice(0, 16_384), event_count: eventCount});
    };
    options.signal.addEventListener(
      "abort",
      () => finish(new Error("Pi RPC was cancelled")),
      {once: true},
    );
    child.stdout.on("data", (chunk: Buffer) => {
      stdout = Buffer.concat([stdout, chunk]);
      if (stdout.length > 4 * 1024 * 1024) return finish(new Error("Pi RPC output exceeded 4 MiB"));
      buffer += chunk.toString("utf8");
      for (;;) {
        const newline = buffer.indexOf("\n");
        if (newline < 0) break;
        const line = buffer.slice(0, newline).replace(/\r$/, "");
        buffer = buffer.slice(newline + 1);
        if (!line) continue;
        let event: Record<string, unknown>;
        try { event = JSON.parse(line) as Record<string, unknown>; }
        catch { return finish(new Error("Pi RPC emitted invalid JSONL")); }
        eventCount += 1;
        if (event.type === "message_end") {
          const message = event.message as Record<string, unknown> | undefined;
          if (message?.role === "assistant") lastAssistant = assistantText(message.content);
        }
        if (event.type === "agent_end") finish();
      }
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr = Buffer.concat([stderr, chunk]).subarray(0, 64 * 1024);
    });
    child.once("error", finish);
    child.once("exit", (code) => {
      if (!completed) finish(new Error(`Pi RPC exited before agent_end (${code ?? "signal"})`));
    });
    const timer = setTimeout(() => finish(new Error("Pi RPC timed out")), options.timeoutMs);
    child.stdin.write(`${JSON.stringify({id: "prompt-1", type: "prompt", message: options.instruction})}\n`);
  });
}

function assistantText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((item): item is Record<string, unknown> => !!item && typeof item === "object")
    .filter((item) => item.type === "text")
    .map((item) => String(item.text ?? ""))
    .join("\n");
}
