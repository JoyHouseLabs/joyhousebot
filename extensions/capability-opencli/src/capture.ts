import {createHash} from "node:crypto";
import {readdir, readFile, stat} from "node:fs/promises";
import {join, relative, resolve} from "node:path";

const MAX_MARKDOWN_BYTES = 512 * 1024;
const MAX_CAPTURE_FILES = 32;

export interface CapturedArtifact {
  artifact_id: string;
  artifact_type: "source_document";
  operation: "create";
  schema_version: 1;
  media_type: "text/markdown";
  data: {content: string};
  content_sha256: string;
  provenance: Record<string, string>;
  evidence: Record<string, string>;
  metadata: Record<string, string>;
}

/**
 * Materialize a reviewed OpenCLI download into a bounded Runtime Artifact.
 * The Runtime receives bytes, never an ambient host path. The catalog enables
 * this only for commands whose `output` argument is an operation workspace path.
 */
export async function captureMarkdownArtifact(options: {
  workspace: string;
  operationId: string;
  capabilityId: string;
  sourceUrl: string | null;
}): Promise<CapturedArtifact[]> {
  const root = resolve(options.workspace);
  const candidates = (await walk(root)).filter((path) => path.toLowerCase().endsWith(".md"));
  if (candidates.length === 0) throw new Error("OpenCLI download did not create a Markdown document");
  if (candidates.length > 1) throw new Error("OpenCLI download created multiple Markdown documents");
  const path = candidates[0];
  const details = await stat(path);
  if (details.size > MAX_MARKDOWN_BYTES) throw new Error("OpenCLI Markdown document exceeds 512 KiB capture policy");
  const bytes = await readFile(path);
  const content = bytes.toString("utf8");
  const contentSha256 = createHash("sha256").update(bytes).digest("hex");
  const artifactId = `artifact_opencli_${createHash("sha256").update(`${options.operationId}\0${contentSha256}`).digest("hex").slice(0, 32)}`;
  return [{
    artifact_id: artifactId,
    artifact_type: "source_document",
    operation: "create",
    schema_version: 1,
    media_type: "text/markdown",
    data: {content},
    content_sha256: contentSha256,
    provenance: {capability_id: options.capabilityId, operation_id: options.operationId},
    evidence: {relative_path: relative(root, path).split("\\").join("/")},
    metadata: {
      source_system: "opencli.weixin",
      ...(options.sourceUrl ? {source_url: options.sourceUrl} : {}),
    },
  }];
}

async function walk(directory: string): Promise<string[]> {
  const values: string[] = [];
  for (const entry of await readdir(directory, {withFileTypes: true})) {
    const path = join(directory, entry.name);
    if (entry.isDirectory()) values.push(...await walk(path));
    else if (entry.isFile()) values.push(path);
    if (values.length > MAX_CAPTURE_FILES) throw new Error("OpenCLI output exceeds capture file policy");
  }
  return values.sort();
}
