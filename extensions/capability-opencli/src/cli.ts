import {readFile} from "node:fs/promises";

import {compileCatalogFiles, loadCompiledCatalog} from "./catalog.js";
import {preflightOpenCli} from "./preflight.js";
import {renderHostExtension} from "./release.js";

async function main(): Promise<void> {
  const [command, ...raw] = process.argv.slice(2);
  const args = parseArgs(raw);
  if (command === "compile-catalog") {
    const catalog = await compileCatalogFiles({
      manifestPath: required(args, "manifest"),
      allowlistPath: required(args, "allowlist"),
      outputPath: required(args, "output"),
      metadata: {
        extensionVersion: required(args, "extension-version"),
        extensionBuildDigest: required(args, "build-digest"),
        extensionLockfileDigest: required(args, "lockfile-digest"),
        nodeVersion: required(args, "node-version"),
        openCliVersion: required(args, "opencli-version"),
        openCliPackageIntegrity: required(args, "opencli-integrity"),
        openCliEntrypointSha256: required(args, "opencli-entrypoint-sha256"),
        expectedManifestSha256: args.get("manifest-sha256"),
      },
    });
    process.stdout.write(`${JSON.stringify({status: "ok", capabilities: catalog.commands.length})}\n`);
    return;
  }
  if (command === "preflight") {
    const catalog = loadCompiledCatalog(JSON.parse(await readFile(required(args, "catalog"), "utf8")));
    const result = await preflightOpenCli({
      entrypoint: required(args, "entrypoint"),
      expectedNodeVersion: catalog.runtime.node_version,
      expectedOpenCliVersion: catalog.runtime.opencli_version,
      profile: args.get("profile"),
    });
    process.stdout.write(`${JSON.stringify(result)}\n`);
    if (!result.ready) process.exitCode = 1;
    return;
  }
  if (command === "render-host-extension") {
    const result = await renderHostExtension({
      bundleRoot: required(args, "bundle-root"),
      stateRoot: required(args, "state-root"),
    });
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
    return;
  }
  throw new Error("usage: cli.js <compile-catalog|preflight|render-host-extension> --key value ...");
}

function parseArgs(values: string[]): Map<string, string> {
  const result = new Map<string, string>();
  for (let index = 0; index < values.length; index += 2) {
    const key = values[index];
    const value = values[index + 1];
    if (!key?.startsWith("--") || value === undefined || value.startsWith("--")) {
      throw new Error("CLI options must use --key value pairs");
    }
    const normalized = key.slice(2);
    if (result.has(normalized)) throw new Error(`duplicate CLI option: ${normalized}`);
    result.set(normalized, value);
  }
  return result;
}

function required(values: Map<string, string>, name: string): string {
  const value = values.get(name);
  if (!value) throw new Error(`--${name} is required`);
  return value;
}

void main().catch((error: unknown) => {
  process.stderr.write(`${error instanceof Error ? error.message : "OpenCLI command failed"}\n`);
  process.exitCode = 1;
});
