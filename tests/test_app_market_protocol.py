from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from joyhousebot.domain.app_packages import app_manifest_sha256, normalize_app_manifest
from joyhousebot.market_protocol.bundle import (
    build_app_bundle,
    load_app_bundle,
    verify_app_bundle,
)
from joyhousebot.market_protocol.canonical import canonical_json, parse_strict_json
from joyhousebot.market_protocol.contracts import (
    normalize_entitlement,
    normalize_usage_receipt,
)
from joyhousebot.market_protocol.dsse import (
    generate_ed25519_key_pair,
    sign_dsse,
    verify_dsse,
)
from joyhousebot.market_protocol.release import normalize_app_id, normalize_market_id


def test_market_id_allows_only_https_or_loopback_http() -> None:
    assert normalize_market_id("https://Market.Example:443/") == "https://market.example"
    assert normalize_market_id("http://127.0.0.1:18810/") == "http://127.0.0.1:18810"
    assert normalize_market_id("http://localhost:18810") == "http://localhost:18810"
    assert normalize_market_id("http://[::1]:18810") == "http://[::1]:18810"
    with pytest.raises(ValueError):
        normalize_market_id("http://market.example")


def test_app_id_is_a_canonical_dns_style_identity() -> None:
    assert normalize_app_id(" JoyHouse.ME ") == "joyhouse.me"
    assert normalize_app_id("app.market-radar") == "app.market-radar"
    for value in ("joyhouse", "app_name.example", "-app.example", "app.example-"):
        with pytest.raises(ValueError, match="DNS-style"):
            normalize_app_id(value)


def _manifest() -> dict:
    return {
        "schema_version": 2,
        "app_id": "joyhouse.me",
        "version": "1.0.0",
        "name": "Market Radar",
        "description": "A portable market research application.",
        "publisher": "joyhousebot",
        "publisher_id": "pub_joyhousebot01",
        "core": {"min_version": "2.0.0", "max_version": ""},
        "extensions": [],
        "capabilities": [],
        "assets": {"agents": [], "teams": [], "skills": [], "workflows": [], "scenarios": []},
        "connections": [],
        "permissions": [],
        "secrets": [],
        "triggers": [],
        "evaluations": [],
        "configuration_schema": {},
        "ui": {},
        "metadata": {},
        "licenses": {"code_expression": "Apache-2.0"},
        "evidence": {},
        "data_practices": {
            "telemetry": "none",
            "outbound_domains": [],
            "collects_personal_data": False,
            "retention_days": 0,
        },
        "metering": [],
    }


def test_manifest_v2_uses_cross_language_canonical_digest() -> None:
    normalized = normalize_app_manifest(_manifest())
    assert app_manifest_sha256(normalized).startswith("sha256:")
    assert canonical_json(normalized) == canonical_json(dict(reversed(list(normalized.items()))))


def test_strict_json_rejects_duplicate_properties() -> None:
    with pytest.raises(ValueError, match="duplicate JSON property"):
        parse_strict_json(b'{"app_id":"one","app_id":"two"}')


def test_dsse_binds_payload_type_and_signing_key() -> None:
    signer = generate_ed25519_key_pair()
    other = generate_ed25519_key_pair()
    envelope = sign_dsse(
        b"payload",
        payload_type="application/vnd.joyhousebot.test+json",
        private_key=signer.private_key,
    )
    assert verify_dsse(
        envelope,
        public_keys={signer.key_id: signer.public_key},
        expected_payload_type="application/vnd.joyhousebot.test+json",
    ) == (b"payload", signer.key_id)
    with pytest.raises(ValueError, match="unexpected DSSE payload type"):
        verify_dsse(
            envelope,
            public_keys={signer.key_id: signer.public_key},
            expected_payload_type="application/vnd.joyhousebot.other+json",
        )
    with pytest.raises(ValueError, match="no valid signature"):
        verify_dsse(envelope, public_keys={other.key_id: other.public_key})


def test_dsse_rejects_malformed_base64_as_protocol_error() -> None:
    signer = generate_ed25519_key_pair()
    with pytest.raises(ValueError, match="invalid DSSE base64 value"):
        verify_dsse(
            {
                "payloadType": "application/vnd.joyhousebot.test+json",
                "payload": "%%%",
                "signatures": [{"keyid": signer.key_id, "sig": "%%%"}],
            },
            public_keys={signer.key_id: signer.public_key},
        )


def test_signed_app_bundle_round_trip_and_tamper_detection(tmp_path) -> None:
    signer = generate_ed25519_key_pair()
    bundle_path = tmp_path / "market-radar.joyhousebot-app"
    created = build_app_bundle(
        bundle_path,
        manifest=_manifest(),
        private_key=signer.private_key,
        market_id="https://market.example",
        publisher_id="pub_joyhousebot01",
        components={
            ("skill", "skill.market-analysis", "1.0.0"): {
                "schema_version": 1,
                "skill_id": "skill.market-analysis",
                "version": "1.0.0",
            }
        },
        released_at="2026-08-10T00:00:00Z",
    )
    verified = verify_app_bundle(
        bundle_path,
        public_keys={signer.key_id: signer.public_key},
        expected_market_id="https://market.example",
        expected_publisher_id="pub_joyhousebot01",
    )
    assert verified.descriptor == created.descriptor
    assert verified.manifest["schema_version"] == 2
    assert len(verified.components) == 1

    files, envelope = load_app_bundle(bundle_path)
    tampered = tmp_path / "tampered.joyhousebot-app"
    manifest = json.loads(files["joyhousebot.app.json"])
    manifest["name"] = "Tampered"
    with zipfile.ZipFile(tampered, "w") as archive:
        archive.writestr("release.dsse.json", canonical_json(envelope))
        archive.writestr("joyhousebot.app.json", canonical_json(manifest))
        for path, payload in verified.components.items():
            archive.writestr(path, payload)
    with pytest.raises(ValueError, match="does not match"):
        verify_app_bundle(tampered, public_keys={signer.key_id: signer.public_key})


def test_bundle_rejects_path_traversal(tmp_path) -> None:
    path = tmp_path / "unsafe.joyhousebot-app"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("../release.dsse.json", "{}")
    with pytest.raises(ValueError, match="unsafe path"):
        load_app_bundle(path)


def test_public_json_schema_matches_protocol_normalizers(tmp_path) -> None:
    schema = json.loads(
        Path("docs/protocol/app-market-v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)

    def validate(name: str, value: dict) -> None:
        Draft202012Validator(
            {"$ref": f"#/$defs/{name}", "$defs": schema["$defs"]}
        ).validate(value)

    signer = generate_ed25519_key_pair()
    bundle = build_app_bundle(
        tmp_path / "schema.joyhousebot-app",
        manifest=_manifest(),
        private_key=signer.private_key,
        market_id="https://market.example",
        publisher_id="pub_joyhousebot01",
        released_at="2026-08-10T00:00:00Z",
    )
    validate("release", bundle.descriptor)
    entitlement = normalize_entitlement(
        {
            "schema_version": "1.0",
            "entitlement_id": "ent_market_001",
            "issuer": "https://market.example",
            "subject": {"installation_key_thumbprint": "sha256:" + "1" * 64},
            "app": {
                "publisher_id": "pub_joyhousebot01",
                "app_id": "joyhouse.me",
                "version_constraint": "*",
            },
            "offer_id": "offer_market_001",
            "features": [],
            "limits": {},
            "not_before": "2026-08-10T00:00:00Z",
            "expires_at": "2026-09-10T00:00:00Z",
            "offline_until": "2026-09-17T00:00:00Z",
            "terms_digest": "sha256:" + "2" * 64,
            "status": "active",
        }
    )
    validate("entitlement", entitlement)
    usage = normalize_usage_receipt(
        {
            "schema_version": "1.0",
            "receipt_id": "usage_market_001",
            "entitlement_id": "ent_market_001",
            "installation_key_thumbprint": "sha256:" + "1" * 64,
            "meter_id": "market_report.generated",
            "period": {
                "start": "2026-08-10T00:00:00Z",
                "end": "2026-08-11T00:00:00Z",
            },
            "quantity": "3",
            "unit": "report",
            "sequence": "1",
            "source_event_digest": "sha256:" + "3" * 64,
            "created_at": "2026-08-11T00:00:01Z",
        }
    )
    validate("usageReceipt", usage)
