import {createHash} from "node:crypto";
import {readdir, readFile, realpath} from "node:fs/promises";
import {join, relative, resolve} from "node:path";

import {loadCompiledCatalog} from "./catalog.js";

export async function renderHostExtension(options: {
  bundleRoot: string;
  stateRoot: string;
}): Promise<Record<string, unknown>> {
  const bundleRoot = await realpath(options.bundleRoot);
  const catalogPath = resolve(bundleRoot, "catalog/catalog.json");
  const lockPath = resolve(bundleRoot, "package-lock.json");
  const workerPath = resolve(bundleRoot, "dist/worker.js");
  const openCliEntrypoint = resolve(bundleRoot, "node_modules/@jackwener/opencli/dist/src/main.js");
  const openCliPackageJson = resolve(bundleRoot, "node_modules/@jackwener/opencli/package.json");
  const catalog = loadCompiledCatalog(JSON.parse(await readFile(catalogPath, "utf8")));
  const buildDigest = `sha256:${await directoryDigest(bundleRoot, "dist")}`;
  const lockfileDigest = `sha256:${digest(await readFile(lockPath))}`;
  if (
    buildDigest !== catalog.extension.build_digest
    || lockfileDigest !== catalog.extension.lockfile_digest
  ) {
    throw new Error("OpenCLI bundle does not match the frozen catalog release digests");
  }
  if (digest(await readFile(openCliEntrypoint)) !== catalog.runtime.opencli_entrypoint_sha256) {
    throw new Error("installed OpenCLI entrypoint does not match the frozen catalog");
  }
  return {
    extension_id: catalog.extension.extension_id,
    version: catalog.extension.version,
    build_digest: buildDigest,
    lockfile_digest: lockfileDigest,
    sdk_version: catalog.extension.sdk_version,
    bundle_root: bundleRoot,
    entrypoint: "dist/worker.js",
    entrypoint_sha256: digest(await readFile(workerPath)),
    runner: "child_process",
    capabilities: [
      ...catalog.commands.filter((command) => command.exposed).map((command) => (
        hostCapability(command.capability, catalog.extension)
      )),
      ...catalog.account_snapshots.map((snapshot) => (
        hostCapability(snapshot.capability, catalog.extension)
      )),
    ],
    environment: {
      OPENCLI_CATALOG_PATH: catalogPath,
      OPENCLI_STATE_PATH: resolve(options.stateRoot, "operations.json"),
      OPENCLI_WORKSPACE_ROOT: resolve(options.stateRoot, "workspaces"),
      OPENCLI_ENTRYPOINT: openCliEntrypoint,
      OPENCLI_PACKAGE_JSON: openCliPackageJson,
    },
    policy: {
      request_timeout_ms: 10_000,
      startup_timeout_ms: 10_000,
      max_frame_bytes: 1_048_576,
      max_stderr_bytes: 65_536,
      max_crashes: 3,
      crash_window_seconds: 60,
    },
  };
}

function hostCapability<T extends object>(
  capability: T,
  extension: {
    extension_id: string;
    version: string;
    build_digest: string;
    lockfile_digest: string;
    sdk_version: string;
  },
): T & {provenance: Record<string, string>} {
  return {
    ...capability,
    provenance: {
      host_extension_id: extension.extension_id,
      host_extension_version: extension.version,
      host_extension_build_digest: extension.build_digest,
      host_extension_lockfile_digest: extension.lockfile_digest,
      host_sdk_version: extension.sdk_version,
    },
  };
}

export async function directoryDigest(root: string, directory: string): Promise<string> {
  const base = resolve(root, directory);
  const files = await walk(base);
  const aggregate = createHash("sha256");
  for (const path of files) {
    const relativePath = relative(root, path).split("\\").join("/");
    aggregate.update(`${digest(await readFile(path))}  ${relativePath}\n`);
  }
  return aggregate.digest("hex");
}

async function walk(directory: string): Promise<string[]> {
  const result: string[] = [];
  for (const entry of await readdir(directory, {withFileTypes: true})) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) result.push(...await walk(path));
    else if (entry.isFile()) result.push(path);
    else throw new Error("OpenCLI release contains an unsupported filesystem entry");
  }
  return result.sort();
}

function digest(value: Buffer): string {
  return createHash("sha256").update(value).digest("hex");
}
