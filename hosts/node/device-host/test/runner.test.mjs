import assert from "node:assert/strict";
import test from "node:test";

import {DeviceHostRunner} from "../dist/index.js";

test("device runner renews accepted work and submits one terminal result", async () => {
  const calls = {renew: 0, complete: [], events: []};
  const delivery = {
    delivery_id: "delivery-1",
    invocation_id: "inv-action-1",
    claim_version: 2,
    request: {
      protocol_version: "1",
      capability: {
        capability_id: "opencli.github.whoami",
        version: "1.0.0",
        implementation_digest: `sha256:${"a".repeat(64)}`,
      },
      subject: {user_id: "user-a", agent_id: "default", session_id: "session-a"},
      execution: {
        run_id: "run-a",
        root_run_id: "run-a",
        task_id: null,
        request_id: "request-a",
        action_id: "action-1",
        idempotency_key: "action:action-1",
        request_digest: `sha256:${"b".repeat(64)}`,
      },
      authorization: {permissions: [], permission_mode: "enforced"},
      input: {},
    },
  };
  const cloud = {
    modelGatewayBaseUrl: "http://127.0.0.1:18794",
    localHostBaseUrl: "http://127.0.0.1:18791",
    heartbeat: async () => {},
    claim: async () => [delivery],
    renew: async () => { calls.renew += 1; },
    issueModelGrant: async () => { throw new Error("unexpected model grant"); },
    appendEvents: async (_delivery, _session, events) => { calls.events.push(...events); },
    complete: async (_delivery, _session, result) => { calls.complete.push(result); },
    claimControls: async () => [],
    completeControl: async () => {},
  };
  let reconciles = 0;
  const local = {
    invoke: async () => ({
      protocol_version: "1",
      status: "accepted",
      operation: {operation_id: "local-operation-1"},
      retry_after_seconds: 0,
    }),
    reconcile: async () => {
      reconciles += 1;
      return {
        protocol_version: "1",
        status: "succeeded",
        summary: "done",
        output: {account: "porthouse"},
        events: [{event_id: "event-1", sequence: 0, event_type: "progress"}],
      };
    },
  };
  const runner = new DeviceHostRunner(cloud, local, 2);

  assert.equal(await runner.runOnce(), 1);
  assert.equal(reconciles, 1);
  assert.equal(calls.renew, 1);
  assert.equal(calls.complete.length, 1);
  assert.equal(calls.complete[0].invocation_id, "inv-action-1");
  assert.deepEqual(calls.complete[0].data, {account: "porthouse"});
  assert.equal(calls.events[0].sequence, 0);
});

test("device runner obtains a transient model grant for a frozen policy", async () => {
  const delivery = {
    delivery_id: "delivery-model",
    invocation_id: "inv-model",
    claim_version: 1,
    request: {
      protocol_version: "1",
      capability: {
        capability_id: "coding.pi.execute",
        version: "1.0.0",
        implementation_digest: `sha256:${"c".repeat(64)}`,
      },
      subject: {user_id: "user-a", agent_id: "default", session_id: "session-a"},
      execution: {
        run_id: "run-model",
        root_run_id: "run-model",
        task_id: "task-model",
        request_id: "request-model",
        action_id: "action-model",
        idempotency_key: "action:action-model",
        request_digest: `sha256:${"d".repeat(64)}`,
      },
      authorization: {
        permissions: [],
        permission_mode: "enforced",
        model_access: {model_id: "test/model"},
      },
      input: {},
    },
  };
  let issued = 0;
  let received;
  const cloud = {
    modelGatewayBaseUrl: "http://127.0.0.1:18794",
    localHostBaseUrl: "http://127.0.0.1:18791",
    heartbeat: async () => {},
    claim: async () => [delivery],
    renew: async () => {},
    issueModelGrant: async () => {
      issued += 1;
      return `jhm_${"x".repeat(64)}`;
    },
    appendEvents: async () => {},
    complete: async () => {},
    claimControls: async () => [],
    completeControl: async () => {},
  };
  const local = {
    invoke: async (_request, context) => {
      received = context;
      return {protocol_version: "1", status: "succeeded", output: {ok: true}};
    },
    reconcile: async () => { throw new Error("unexpected reconcile"); },
  };
  assert.equal(await new DeviceHostRunner(cloud, local).runOnce(), 1);
  assert.equal(issued, 1);
  assert.deepEqual(received, {
    model_gateway_base_url: "http://127.0.0.1:18794",
    model_grant_token: `jhm_${"x".repeat(64)}`,
  });
});

test("device runner exposes only a transient scoped Tool Broker grant", async () => {
  const delivery = {
    delivery_id: "delivery-tools",
    invocation_id: "inv-tools",
    claim_version: 3,
    request: {
      protocol_version: "1",
      capability: {
        capability_id: "coding.pi.execute",
        version: "1.0.0",
        implementation_digest: `sha256:${"e".repeat(64)}`,
      },
      subject: {user_id: "user-a", agent_id: "default", session_id: "session-a"},
      execution: {
        run_id: "run-tools", root_run_id: "run-tools", task_id: null,
        request_id: "request-tools", action_id: "action-tools",
        idempotency_key: "action:action-tools", request_digest: `sha256:${"f".repeat(64)}`,
      },
      authorization: {
        permissions: ["context.read"],
        permission_mode: "enforced",
        tool_access: [{capability_id: "knowledge.retrieve", version: "1.0.0"}],
      },
      input: {},
    },
  };
  let received;
  const cloud = {
    runtimeBaseUrl: "https://runtime.example.test",
    modelGatewayBaseUrl: "http://127.0.0.1:18794",
    localHostBaseUrl: "http://127.0.0.1:18791",
    heartbeat: async () => {},
    claim: async () => [delivery],
    renew: async () => {},
    issueModelGrant: async () => { throw new Error("unexpected model grant"); },
    issueToolGrant: async () => `jht_${"y".repeat(64)}`,
    appendEvents: async () => {},
    complete: async () => {},
    claimControls: async () => [],
    completeControl: async () => {},
  };
  const local = {
    invoke: async (_request, context) => {
      received = context;
      return {protocol_version: "1", status: "succeeded", output: {ok: true}};
    },
    reconcile: async () => { throw new Error("unexpected reconcile"); },
  };
  assert.equal(await new DeviceHostRunner(cloud, local).runOnce(), 1);
  assert.deepEqual(received, {
    tool_broker_base_url: "https://runtime.example.test",
    tool_grant_token: `jht_${"y".repeat(64)}`,
  });
});

test("device runner completes bounded control diagnostics without exposing a shell", async () => {
  const completed = [];
  const cloud = {
    runtimeBaseUrl: "http://127.0.0.1:1",
    modelGatewayBaseUrl: "",
    localHostBaseUrl: "http://127.0.0.1:1",
    heartbeat: async () => {}, claim: async () => [], renew: async () => {},
    issueModelGrant: async () => "", issueToolGrant: async () => "",
    appendEvents: async () => {}, complete: async () => {},
    claimControls: async () => [{request_id: "hostctl-1", action: "preflight", parameters: {}, claim_version: 1}],
    completeControl: async (_request, _session, outcome) => { completed.push(outcome); },
  };
  const local = {invoke: async () => ({}), reconcile: async () => ({})};
  assert.equal(await new DeviceHostRunner(cloud, local).runOnce(), 1);
  assert.equal(completed[0].status, "failed");
  assert.equal(completed[0].error.code, "HOST_PREFLIGHT_FAILED");
});
