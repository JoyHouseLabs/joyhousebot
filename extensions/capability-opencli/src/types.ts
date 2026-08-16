export type OpenCliAccess = "read" | "write";

export interface OpenCliArgument {
  name: string;
  type: "bool" | "boolean" | "float" | "int" | "number" | "str" | "string";
  required?: boolean;
  positional?: boolean;
  default?: unknown;
  help?: string;
  choices?: Array<string | number | boolean>;
}

export interface OpenCliManifestCommand {
  site: string;
  name: string;
  description?: string;
  access: OpenCliAccess;
  domain?: string;
  strategy?: string;
  browser?: boolean;
  siteSession?: string;
  defaultWindowMode?: string;
  args: OpenCliArgument[];
  columns?: string[];
  type?: string;
  modulePath?: string;
  sourceFile?: string;
}

export interface AllowedCommand {
  site: string;
  name: string;
  capability_version: string;
  timeout_seconds?: number;
  expected_duration_seconds?: number;
  data_classification?: "public" | "internal" | "confidential" | "restricted";
  allow_path_arguments?: string[];
  /** Capture one bounded Markdown document emitted below the managed output workspace. */
  capture_output_markdown?: boolean;
}

export interface Allowlist {
  schema_version: 1;
  commands: AllowedCommand[];
}

export interface CapabilityDefinition {
  capability_id: string;
  version: string;
  implementation_digest: string;
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  permissions: string[];
  tags: string[];
  side_effect: "read" | "external";
  idempotent: true;
  retryable: boolean;
  data_classification: "public" | "internal" | "confidential" | "restricted";
  timeout_seconds: number;
  expected_duration_seconds: number;
  invocation_concurrency: "parallel_safe" | "sequential";
  max_concurrent_invocations: number;
  cost_policy: Record<string, unknown>;
  execution_mode: "durable";
  supports_stream: true;
  provenance: {
    host_protocol_version: "1";
    sdk_version: "1";
    extension_id: "capability-opencli";
    extension_version: string;
    extension_build_digest: string;
    extension_lockfile_digest: string;
  };
}

export interface CompiledCommand {
  site: string;
  name: string;
  access: OpenCliAccess;
  domain: string | null;
  strategy: string;
  browser: boolean;
  site_session: string | null;
  default_window_mode: string | null;
  args: OpenCliArgument[];
  columns: string[];
  allowed_path_arguments: string[];
  capture_output_markdown: boolean;
  capability: CapabilityDefinition;
}

export interface CompiledCatalog {
  schema_version: 1;
  extension: {
    extension_id: "capability-opencli";
    version: string;
    build_digest: string;
    lockfile_digest: string;
    sdk_version: "1";
  };
  runtime: {
    node_version: string;
    opencli_version: string;
    opencli_package_integrity: string;
    opencli_entrypoint_sha256: string;
    upstream_manifest_sha256: string;
  };
  commands: CompiledCommand[];
}

export interface CompileCatalogOptions {
  extensionVersion: string;
  extensionBuildDigest: string;
  extensionLockfileDigest: string;
  nodeVersion: string;
  openCliVersion: string;
  openCliPackageIntegrity: string;
  openCliEntrypointSha256: string;
  expectedManifestSha256?: string;
}
