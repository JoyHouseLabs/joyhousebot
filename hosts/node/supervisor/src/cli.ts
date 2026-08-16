import {resolve} from "node:path";

import {canonicalJson, sha256Hex} from "@joyhousebot/extension-sdk";

import {loadSupervisorConfig, hostManifest} from "./manifest.js";
import {createHostServer} from "./server.js";
import {NodeExtensionSupervisor} from "./supervisor.js";

const configPath = resolve(process.env.JOYHOUSE_NODE_HOST_CONFIG ?? "host.config.json");
const keyId = process.env.JOYHOUSE_NODE_HOST_KEY_ID ?? "";
const signingSecret = process.env.JOYHOUSE_NODE_HOST_SIGNING_SECRET ?? "";
if (!keyId) throw new Error("JOYHOUSE_NODE_HOST_KEY_ID is required");

const config = await loadSupervisorConfig(configPath);
const supervisor = new NodeExtensionSupervisor(config);
await supervisor.start();
const server = createHostServer(supervisor, {
  keyId,
  signingSecret,
  modelGatewayBaseUrl: config.model_gateway_base_url,
  toolBrokerBaseUrl: config.tool_broker_base_url,
});
server.listen(config.listen.port, config.listen.host, () => {
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : config.listen.port;
  const digest = `sha256:${sha256Hex(canonicalJson(hostManifest(config)))}`;
  process.stdout.write(`${canonicalJson({event: "ready", port, manifest_digest: digest})}\n`);
});

async function shutdown(): Promise<void> {
  server.close();
  await supervisor.close();
  process.exit(0);
}

process.on("SIGINT", () => void shutdown());
process.on("SIGTERM", () => void shutdown());
