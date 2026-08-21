import type {CapabilityIdentity, ExtensionHostManifest} from "@joyhousebot/extension-sdk";

import {hostManifest} from "./manifest.js";
import {OperationRegistry} from "./operations.js";
import {ExtensionProcess} from "./runner.js";
import type {InstalledExtension, SupervisorConfig} from "./types.js";

export class NodeExtensionSupervisor {
  readonly config: SupervisorConfig;
  readonly manifest: ExtensionHostManifest;
  readonly operations: OperationRegistry;
  #extensions = new Map<string, InstalledExtension>();
  #capabilities = new Map<
    string,
    {extension: InstalledExtension; capability: CapabilityIdentity & Record<string, unknown>}
  >();
  #processes = new Map<string, ExtensionProcess>();

  constructor(config: SupervisorConfig) {
    this.config = config;
    this.manifest = hostManifest(config);
    this.operations = new OperationRegistry(config.operation_registry_path);
    for (const extension of config.extensions) {
      this.#extensions.set(extension.extension_id, extension);
      this.#processes.set(extension.extension_id, new ExtensionProcess(extension));
      for (const capability of extension.capabilities ?? []) {
        this.#capabilities.set(capability.capability_id, {extension, capability});
      }
    }
  }

  async start(): Promise<void> {
    await this.operations.load();
    await Promise.all([...this.#processes.values()].map((process) => process.start()));
  }

  async invoke(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    const capability = payload.capability as Record<string, unknown> | undefined;
    const execution = payload.execution as Record<string, unknown> | undefined;
    const capabilityId = String(capability?.capability_id ?? "");
    const deployed = this.#capabilities.get(capabilityId);
    if (!deployed) throw new Error("capability is not deployed by this host");
    if (
      String(capability?.version ?? "") !== deployed.capability.version
      || String(capability?.implementation_digest ?? "")
        !== deployed.capability.implementation_digest
    ) {
      throw new Error("capability version identity mismatch");
    }
    const extension = deployed.extension;
    const response = await this.#process(extension.extension_id).request("invoke", payload);
    const operation = response.operation as Record<string, unknown> | undefined;
    if (response.status === "accepted" && operation?.operation_id) {
      await this.operations.bind({
        operation_id: String(operation.operation_id),
        extension_id: extension.extension_id,
        capability_id: capabilityId,
        action_id: execution?.action_id ? String(execution.action_id) : null,
        idempotency_key: String(execution?.idempotency_key ?? ""),
        request_digest: String(execution?.request_digest ?? ""),
        updated_at: new Date().toISOString(),
      });
    }
    return response;
  }

  async reconcile(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    const operation = payload.operation as Record<string, unknown> | undefined;
    const binding = this.operations.get(String(operation?.operation_id ?? ""));
    if (!binding) {
      return {protocol_version: "1", status: "unknown", summary: "operation is unknown"};
    }
    this.#validateOperationIdentity(payload, binding);
    return this.#process(binding.extension_id).request("reconcile", payload);
  }

  async command(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.#operationRequest("command", payload);
  }

  async cancel(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.#operationRequest("cancel", payload);
  }

  async close(): Promise<void> {
    await Promise.all([...this.#processes.values()].map((process) => process.close()));
  }

  async #operationRequest(
    type: "command" | "cancel",
    payload: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const operation = payload.operation as Record<string, unknown> | undefined;
    const binding = this.operations.get(String(operation?.operation_id ?? ""));
    if (!binding) throw new Error("operation is unknown");
    this.#validateOperationIdentity(payload, binding);
    return this.#process(binding.extension_id).request(type, payload);
  }

  #process(extensionId: string): ExtensionProcess {
    const process = this.#processes.get(extensionId);
    if (!process) throw new Error("extension process is not configured");
    return process;
  }

  #validateOperationIdentity(
    payload: Record<string, unknown>,
    binding: {
      capability_id: string;
      action_id: string | null;
      idempotency_key: string;
      request_digest: string;
    },
  ): void {
    const capability = payload.capability as Record<string, unknown> | undefined;
    const execution = payload.execution as Record<string, unknown> | undefined;
    if (
      String(capability?.capability_id ?? "") !== binding.capability_id
      || (execution?.action_id ? String(execution.action_id) : null) !== binding.action_id
      || String(execution?.idempotency_key ?? "") !== binding.idempotency_key
      || String(execution?.request_digest ?? "") !== binding.request_digest
    ) {
      throw new Error("operation identity mismatch");
    }
  }
}
