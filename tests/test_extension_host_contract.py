from __future__ import annotations

import json
from pathlib import Path

from joyhousebot_connector_http_capability import (
    connector,
    extension_host_manifest_digest,
    request_digest,
    sign_request_body,
    sign_response_body,
)
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "contract" / "extension-host"
SCHEMA = ROOT / "docs" / "protocol" / "extension-host-v1.schema.json"


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_extension_host_schema_accepts_contract_fixtures() -> None:
    schema = _json(SCHEMA)
    validator = Draft202012Validator(schema)
    vector = _json(FIXTURES / "signature-vectors.json")
    describe = _json(FIXTURES / "host-describe.json")

    validator.validate(vector["request"]["body"])
    validator.validate(describe)
    assert extension_host_manifest_digest(describe["manifest"]) == describe["manifest_digest"]

    reconcile = {
        key: value
        for key, value in vector["request"]["body"].items()
        if key != "input" and key != "authorization"
    }
    reconcile["operation"] = {"operation_id": "operation-1"}
    validator.validate(reconcile)


def test_extension_host_schema_accepts_frozen_host_access_and_lifecycle_components() -> None:
    validator = Draft202012Validator(_json(SCHEMA))
    invocation = _json(FIXTURES / "signature-vectors.json")["request"]["body"]
    invocation["authorization"]["model_access"] = {
        "provider_id": "provider-local",
        "provider_revision_id": "revision-1",
        "model_id": "model-1",
        "token_budget": 10_000,
        "cost_budget_micros": 0,
        "max_concurrent": 1,
        "expires_in_seconds": 600,
        "context_window": 128_000,
    }
    invocation["authorization"]["tool_access"] = [
        {
            "capability_id": "browser.navigate",
            "version": "1.0.0",
            "implementation_digest": f"sha256:{'1' * 64}",
            "extension_id": "browser-tools",
        }
    ]
    validator.validate(invocation)

    describe = _json(FIXTURES / "host-describe.json")
    describe["manifest"]["channels"] = [
        {
            "channel_id": "whatsapp",
            "version": "1.0.0",
            "implementation_digest": f"sha256:{'2' * 64}",
        }
    ]
    describe["manifest"]["event_sources"] = [
        {
            "event_source_id": "github-webhook",
            "version": "1.0.0",
            "implementation_digest": f"sha256:{'3' * 64}",
        }
    ]
    validator.validate(describe)


def test_python_matches_extension_host_signature_vectors() -> None:
    vector = _json(FIXTURES / "signature-vectors.json")
    request = vector["request"]
    body = connector._canonical_json(request["body"])
    assert body.decode("utf-8") == request["canonical_body"]
    assert sign_request_body(
        method=request["method"],
        path=request["path"],
        timestamp=request["timestamp"],
        nonce=request["nonce"],
        body=body,
        secret=vector["secret"],
    ) == request["signature"]

    response = vector["response"]
    response_body = connector._canonical_json(response["body"])
    assert response_body.decode("utf-8") == response["canonical_body"]
    assert sign_response_body(
        status_code=response["status_code"],
        nonce=request["nonce"],
        body=response_body,
        secret=vector["secret"],
    ) == response["signature"]


def test_python_matches_extension_host_request_digest_vector() -> None:
    vector = _json(FIXTURES / "signature-vectors.json")["request"]
    body = vector["body"]
    assert request_digest(
        capability=body["capability"],
        subject=body["subject"],
        authorization=body["authorization"],
        input_value=body["input"],
    ) == vector["request_digest"]
