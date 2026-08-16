import {spawn, type ChildProcess} from "node:child_process";

import {buildOpenCliArgv} from "./argv.js";
import type {CompiledCommand} from "./types.js";

export interface OpenCliExecutionResult {
  state: "succeeded" | "retryable" | "manual_required" | "failed" | "cancelled";
  output?: unknown;
  error?: {code: string; message: string; retryable: boolean; exit_code: number | null};
  empty?: boolean;
}

export interface RunningOpenCli {
  child: ChildProcess;
  result: Promise<OpenCliExecutionResult>;
}

export interface OpenCliRunnerOptions {
  entrypoint: string;
  command: CompiledCommand;
  input: unknown;
  workspace: string;
  timeoutMs: number;
  maxStdoutBytes: number;
  maxStderrBytes: number;
}

export function runOpenCli(options: OpenCliRunnerOptions): RunningOpenCli {
  const {argv, profile} = buildOpenCliArgv(options.command, options.input, options.workspace);
  const child = spawn(process.execPath, [options.entrypoint, ...argv], {
    cwd: options.workspace,
    env: {
      ...process.env,
      ...(profile ? {OPENCLI_PROFILE: profile} : {}),
      NO_COLOR: "1",
    },
    shell: false,
    stdio: ["ignore", "pipe", "pipe"],
  });
  const result = new Promise<OpenCliExecutionResult>((resolveResult) => {
    const stdout: Buffer[] = [];
    const stderr: Buffer[] = [];
    let stdoutBytes = 0;
    let stderrBytes = 0;
    let limitError: string | null = null;
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill("SIGTERM");
      setTimeout(() => child.kill("SIGKILL"), 2_000).unref();
    }, options.timeoutMs);
    timer.unref();
    child.stdout.on("data", (chunk: Buffer) => {
      stdoutBytes += chunk.length;
      if (stdoutBytes > options.maxStdoutBytes) {
        limitError = "stdout";
        child.kill("SIGKILL");
        return;
      }
      stdout.push(chunk);
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderrBytes += chunk.length;
      if (stderrBytes > options.maxStderrBytes) {
        limitError = "stderr";
        child.kill("SIGKILL");
        return;
      }
      stderr.push(chunk);
    });
    child.once("error", () => {
      clearTimeout(timer);
      resolveResult(failure("OPENCLI_PROCESS_START_FAILED", "OpenCLI process could not start", true, null));
    });
    child.once("exit", (code, signal) => {
      clearTimeout(timer);
      if (limitError) {
        resolveResult(failure(
          `OPENCLI_${limitError.toUpperCase()}_LIMIT`,
          `OpenCLI ${limitError} exceeded the configured limit`,
          false,
          code,
        ));
        return;
      }
      if (timedOut) {
        resolveResult(failure("OPENCLI_TIMEOUT", "OpenCLI command exceeded its deadline", true, 75));
        return;
      }
      if (signal === "SIGINT" || signal === "SIGTERM" || signal === "SIGKILL" || code === 130) {
        resolveResult(failure("OPENCLI_CANCELLED", "OpenCLI command was cancelled", false, 130, "cancelled"));
        return;
      }
      if (code === 0 || code === 66) {
        if (code === 66 || stdoutBytes === 0) {
          resolveResult({state: "succeeded", output: [], empty: true});
          return;
        }
        try {
          const output: unknown = JSON.parse(Buffer.concat(stdout).toString("utf8"));
          resolveResult({state: "succeeded", output});
        } catch {
          resolveResult(failure("OPENCLI_OUTPUT_INVALID", "OpenCLI returned invalid JSON", false, code));
        }
        return;
      }
      resolveResult(mapExitCode(code, options.command.access));
    });
  });
  return {child, result};
}

function mapExitCode(code: number | null, access: "read" | "write"): OpenCliExecutionResult {
  if (code === 69) {
    return failure("OPENCLI_BRIDGE_UNAVAILABLE", "Browser Bridge is not connected", true, code, "retryable");
  }
  if (code === 75) {
    return failure("OPENCLI_TEMPORARY_FAILURE", "OpenCLI reported a temporary failure", true, code, "retryable");
  }
  if (code === 77) {
    return failure("OPENCLI_AUTH_REQUIRED", "Sign in to the selected browser profile, then retry", false, code, "manual_required");
  }
  if (code === 78) {
    return failure("OPENCLI_CONFIG_INVALID", "OpenCLI configuration is incomplete", false, code);
  }
  if (code === 2) {
    return failure("OPENCLI_USAGE_ERROR", "Frozen OpenCLI arguments are incompatible with this build", false, code);
  }
  return failure(
    access === "write" ? "OPENCLI_WRITE_OUTCOME_UNKNOWN" : "OPENCLI_COMMAND_FAILED",
    access === "write"
      ? "OpenCLI write outcome is uncertain and requires review"
      : "OpenCLI command failed",
    false,
    code,
    access === "write" ? "manual_required" : "failed",
  );
}

function failure(
  code: string,
  message: string,
  retryable: boolean,
  exit_code: number | null,
  state: OpenCliExecutionResult["state"] = retryable ? "retryable" : "failed",
): OpenCliExecutionResult {
  return {state, error: {code, message, retryable, exit_code}};
}
