import {resolve} from "node:path";

import {RuntimeDeviceTransport} from "./cloud.js";
import {loadDeviceHostConfig} from "./config.js";
import {SignedLocalCapabilityHost} from "./local-host.js";
import {DeviceHostRunner} from "./runner.js";

const config = await loadDeviceHostConfig(
  resolve(process.env.JOYHOUSEBOT_DEVICE_HOST_CONFIG ?? "device-host.config.json"),
);
const cloud = new RuntimeDeviceTransport(config);
const runner = new DeviceHostRunner(cloud, new SignedLocalCapabilityHost(config.localHost));
let stopping = false;

process.on("SIGINT", () => { stopping = true; });
process.on("SIGTERM", () => { stopping = true; });

while (!stopping) {
  try {
    await cloud.heartbeat();
    const claimed = await runner.runOnce();
    if (claimed === 0) await wait(config.pollIntervalMs);
  } catch (error) {
    process.stderr.write(`device-host: ${safeMessage(error)}\n`);
    await wait(config.pollIntervalMs);
  }
}

function wait(milliseconds: number): Promise<void> {
  return new Promise((done) => setTimeout(done, milliseconds));
}

function safeMessage(error: unknown): string {
  return (error instanceof Error ? error.message : "device host error")
    .replace(/[\r\n]+/g, " ")
    .slice(0, 500);
}
