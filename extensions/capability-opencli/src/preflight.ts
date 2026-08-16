import {spawn} from "node:child_process";

export interface OpenCliPreflightResult {
  ready: boolean;
  node: {expected: string; actual: string; ready: boolean};
  opencli: {expected: string; actual: string | null; ready: boolean};
  browser_bridge: {ready: boolean; exit_code: number | null; summary: string};
}

export async function preflightOpenCli(options: {
  entrypoint: string;
  expectedNodeVersion: string;
  expectedOpenCliVersion: string;
  profile?: string;
}): Promise<OpenCliPreflightResult> {
  const version = await capture(options.entrypoint, ["--version"], options.profile, 15_000);
  const actualVersion = version.stdout.trim().replace(/^v/, "").split(/\s+/).at(-1) ?? null;
  const doctor = await capture(options.entrypoint, ["doctor"], options.profile, 60_000);
  const nodeReady = process.version === options.expectedNodeVersion;
  const openCliReady = version.code === 0 && actualVersion === options.expectedOpenCliVersion;
  const bridgeReady = doctor.code === 0
    && doctor.stdout.includes("[OK] Extension: connected")
    && doctor.stdout.includes("[OK] Connectivity: connected");
  return {
    ready: nodeReady && openCliReady && bridgeReady,
    node: {expected: options.expectedNodeVersion, actual: process.version, ready: nodeReady},
    opencli: {expected: options.expectedOpenCliVersion, actual: actualVersion, ready: openCliReady},
    browser_bridge: {
      ready: bridgeReady,
      exit_code: doctor.code,
      summary: bridgeReady ? "Browser Bridge is ready" : doctorSummary(doctor.code, doctor.stdout),
    },
  };
}

async function capture(
  entrypoint: string,
  args: string[],
  profile: string | undefined,
  timeoutMs: number,
): Promise<{code: number | null; stdout: string}> {
  return new Promise((resolveResult, reject) => {
    const child = spawn(process.execPath, [entrypoint, ...args], {
      env: {...process.env, ...(profile ? {OPENCLI_PROFILE: profile} : {}), NO_COLOR: "1"},
      shell: false,
      stdio: ["ignore", "pipe", "ignore"],
    });
    const chunks: Buffer[] = [];
    let bytes = 0;
    const timer = setTimeout(() => child.kill("SIGKILL"), timeoutMs);
    timer.unref();
    child.stdout.on("data", (chunk: Buffer) => {
      bytes += chunk.length;
      if (bytes > 1_048_576) child.kill("SIGKILL");
      else chunks.push(chunk);
    });
    child.once("error", reject);
    child.once("exit", (code) => {
      clearTimeout(timer);
      resolveResult({code, stdout: Buffer.concat(chunks).toString("utf8")});
    });
  });
}

function doctorSummary(code: number | null, output: string): string {
  if (output.includes("[MISSING] Extension")) return "Browser Bridge extension is not connected";
  if (output.includes("[FAIL] Connectivity")) return "Browser Bridge connectivity check failed";
  if (code === 69) return "Browser Bridge is not connected";
  if (code === 77) return "The selected browser profile requires login";
  if (code === 78) return "OpenCLI configuration is incomplete";
  if (code === 75) return "OpenCLI doctor timed out";
  return `OpenCLI doctor failed with exit code ${code ?? "unknown"}`;
}
