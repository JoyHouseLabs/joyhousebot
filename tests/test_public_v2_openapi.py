from __future__ import annotations

import json
from pathlib import Path

from joyhousebot.api.app import create_app
from joyhousebot.api.public_v2_openapi import public_v2_openapi_document


def test_public_v2_openapi_matches_checked_in_contract() -> None:
    expected = json.loads(Path("docs/api/public-v2.openapi.json").read_text(encoding="utf-8"))
    actual = create_app(surface="public").openapi()
    assert actual == expected, (
        "public v2 OpenAPI changed; update implementation/SDKs together and run "
        "scripts/export_public_v2_openapi.py"
    )


def test_public_v2_openapi_contains_only_stable_app_runtime_concepts() -> None:
    document = create_app(surface="public").openapi()
    serialized = json.dumps(document, sort_keys=True)
    for forbidden in (
        "CapabilityRef",
        "GraphTask",
        "Scenario",
        "Worker",
        "grant_id",
        "lease_owner",
    ):
        assert forbidden not in serialized
    assert set(document["paths"]) == {
        "/v2/app-auth/token",
        "/v2/owner-auth/token",
        "/v2/owner-auth/refresh",
        "/v2/owner-auth/revoke",
        "/v2/apps",
        "/v2/apps/{app_id}/install",
        "/v2/entrypoints",
        "/v2/entrypoints/{entrypoint_id}",
        "/v2/entrypoints/{entrypoint_id}/runs",
        "/v2/runs/{run_id}",
        "/v2/runs/{run_id}/cancel",
        "/v2/runs/{run_id}/artifacts",
        "/v2/artifacts/{artifact_id}",
        "/v2/runs/{run_id}/inputs",
            "/v2/runs/{run_id}/approvals",
            "/v2/runs/{run_id}/operations",
            "/v2/approvals/{approval_id}/decisions",
        "/v2/runs/{run_id}/events",
    }


def test_combined_openapi_can_still_export_the_public_contract() -> None:
    expected = json.loads(Path("docs/api/public-v2.openapi.json").read_text(encoding="utf-8"))
    actual = public_v2_openapi_document(create_app(surface="combined").openapi())
    assert actual == expected
