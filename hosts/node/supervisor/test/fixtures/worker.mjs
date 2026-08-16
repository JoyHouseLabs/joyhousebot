import {createHash, randomUUID} from "node:crypto";
import {existsSync, readFileSync, renameSync, writeFileSync} from "node:fs";
import {createInterface} from "node:readline";

const statePath = process.env.FIXTURE_STATE_PATH;
const label = process.env.FIXTURE_LABEL ?? "fixture";
const operations = new Map();
if (statePath && existsSync(statePath)) {
  for (const item of JSON.parse(readFileSync(statePath, "utf8"))) {
    operations.set(item.operation_id, item);
  }
}

function persist() {
  if (!statePath) return;
  const temporary = `${statePath}.tmp`;
  writeFileSync(temporary, JSON.stringify([...operations.values()]));
  renameSync(temporary, statePath);
}

function reply(value) {
  process.stdout.write(`${JSON.stringify(value)}\n`);
}

createInterface({input: process.stdin, crlfDelay: Infinity}).on("line", (line) => {
  const request = JSON.parse(line);
  if (request.type === "health") {
    reply({id: request.id, type: "pong", payload: {status: "ok"}});
    return;
  }
  const payload = request.payload ?? {};
  const input = payload.input ?? {};
  if (input.behavior === "crash") process.exit(23);
  if (input.behavior === "block") {
    const until = Date.now() + 10_000;
    while (Date.now() < until) {} // fixture for timeout isolation
  }
  if (request.type === "invoke") {
    if (input.async === true) {
      const identity = String(payload.execution?.idempotency_key ?? randomUUID());
      const operation_id = `op_${createHash("sha256").update(identity).digest("hex").slice(0, 16)}`;
      const operation = {
        operation_id,
        ready_at: Date.now() + Number(input.delay_ms ?? 20),
        output: {label, value: input.value},
      };
      operations.set(operation_id, operation);
      persist();
      reply({id: request.id, type: "result", payload: {
        protocol_version: "1", status: "accepted", summary: "accepted",
        operation: {operation_id},
      }});
      return;
    }
    reply({id: request.id, type: "result", payload: {
      protocol_version: "1", status: "succeeded", summary: "completed",
      output: {label, value: input.value}, artifacts: [],
    }});
    return;
  }
  const operationId = payload.operation?.operation_id;
  const operation = operations.get(operationId);
  if (!operation) {
    reply({id: request.id, type: "result", payload: {
      protocol_version: "1", status: "unknown", summary: "unknown",
    }});
    return;
  }
  if (request.type === "cancel") {
    operations.delete(operationId);
    persist();
    reply({id: request.id, type: "result", payload: {
      protocol_version: "1", status: "failed", summary: "cancelled",
      error: {code: "CANCELLED", message: "cancelled", retryable: false},
    }});
    return;
  }
  const completed = Date.now() >= operation.ready_at;
  reply({id: request.id, type: "result", payload: {
    protocol_version: "1",
    status: completed ? "succeeded" : "pending",
    summary: completed ? "completed" : "pending",
    operation: {operation_id: operationId},
    ...(completed ? {output: operation.output, artifacts: []} : {retry_after_seconds: 1}),
  }});
});
