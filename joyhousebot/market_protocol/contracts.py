"""Signed Market control-plane payload contracts."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from joyhousebot.market_protocol.canonical import canonical_json, parse_strict_json
from joyhousebot.market_protocol.dsse import sign_dsse, verify_dsse
from joyhousebot.market_protocol.release import (
    normalize_market_id,
    normalize_publisher_id,
)

DISCOVERY_MEDIA_TYPE = "application/vnd.joyhouse.market.discovery.v1+json"
RESOLUTION_MEDIA_TYPE = "application/vnd.joyhouse.market.resolution.v1+json"
ENTITLEMENT_MEDIA_TYPE = "application/vnd.joyhouse.market.entitlement.v1+json"
ATTESTATION_MEDIA_TYPE = "application/vnd.joyhouse.market.attestation.v1+json"
USAGE_MEDIA_TYPE = "application/vnd.joyhouse.market.usage.v1+json"
SETTLEMENT_MEDIA_TYPE = "application/vnd.joyhouse.market.settlement.v1+json"
GOVERNANCE_MEDIA_TYPE = "application/vnd.joyhouse.market.governance.v1+json"
INSTALLATION_GRANT_MEDIA_TYPE = (
    "application/vnd.joyhouse.market.installation-grant.v1+json"
)
INSTALLATION_RECEIPT_MEDIA_TYPE = (
    "application/vnd.joyhouse.market.installation-receipt.v1+json"
)
PUBLISHER_KEY_ROTATION_MEDIA_TYPE = (
    "application/vnd.joyhouse.publisher-key-rotation.v1+json"
)
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{2,159}$")


def _timestamp(value: Any, *, field: str) -> str:
    result = str(value or "")
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return result


def _identifier(value: Any, *, field: str) -> str:
    result = str(value or "").strip()
    if not _ID.fullmatch(result):
        raise ValueError(f"{field} must be a stable identifier")
    return result


def _digest(value: Any, *, field: str) -> str:
    result = str(value or "")
    if not _DIGEST.fullmatch(result):
        raise ValueError(f"{field} must be sha256")
    return result


def decimal_text(value: Any, *, field: str, allow_negative: bool = False) -> str:
    result = str(value)
    if len(result) > 80:
        raise ValueError(f"{field} is too large")
    try:
        number = Decimal(result)
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be a decimal string") from exc
    if not number.is_finite() or (number < 0 and not allow_negative):
        raise ValueError(f"{field} is outside the allowed decimal range")
    return format(number, "f")


def sign_json_contract(
    value: dict[str, Any], *, payload_type: str, private_key: str
) -> dict[str, Any]:
    return sign_dsse(
        canonical_json(value),
        payload_type=payload_type,
        private_key=private_key,
    )


def verify_json_contract(
    envelope: Mapping[str, Any] | bytes | str,
    *,
    payload_type: str,
    public_keys: Mapping[str, str | bytes],
) -> tuple[dict[str, Any], str]:
    payload, key_id = verify_dsse(
        envelope,
        public_keys=public_keys,
        expected_payload_type=payload_type,
    )
    value = parse_strict_json(payload)
    if not isinstance(value, dict) or canonical_json(value) != payload:
        raise ValueError("signed Market payload is not canonical JSON")
    return value, key_id


def normalize_entitlement(value: dict[str, Any]) -> dict[str, Any]:
    if str(value.get("schema_version") or "") != "1.0":
        raise ValueError("unsupported Entitlement schema_version")
    subject = value.get("subject")
    app = value.get("app")
    limits = value.get("limits") or {}
    if not isinstance(subject, dict) or not isinstance(app, dict) or not isinstance(limits, dict):
        raise ValueError("Entitlement subject, app, and limits must be objects")
    thumbprint = _digest(
        subject.get("installation_key_thumbprint"),
        field="installation_key_thumbprint",
    )
    features = value.get("features") or []
    if not isinstance(features, list) or len(features) > 256:
        raise ValueError("Entitlement features must contain at most 256 entries")
    normalized_limits = {str(key): str(item) for key, item in sorted(limits.items())}
    status = str(value.get("status") or "active")
    if status not in {"active", "expired", "suspended", "refunded", "chargeback", "revoked"}:
        raise ValueError("Entitlement status is invalid")
    normalized = {
        "schema_version": "1.0",
        "entitlement_id": _identifier(value.get("entitlement_id"), field="entitlement_id"),
        "issuer": normalize_market_id(str(value.get("issuer") or "")),
        "subject": {"installation_key_thumbprint": thumbprint},
        "app": {
            "publisher_id": normalize_publisher_id(str(app.get("publisher_id") or "")),
            "app_id": _identifier(app.get("app_id"), field="app_id"),
            "version_constraint": str(app.get("version_constraint") or "*")[:256],
        },
        "offer_id": _identifier(value.get("offer_id"), field="offer_id"),
        "features": sorted({str(item) for item in features}),
        "limits": normalized_limits,
        "not_before": _timestamp(value.get("not_before"), field="not_before"),
        "expires_at": _timestamp(value.get("expires_at"), field="expires_at"),
        "offline_until": _timestamp(value.get("offline_until"), field="offline_until"),
        "terms_digest": _digest(value.get("terms_digest"), field="terms_digest"),
        "status": status,
    }
    not_before = datetime.fromisoformat(normalized["not_before"].replace("Z", "+00:00"))
    expires_at = datetime.fromisoformat(normalized["expires_at"].replace("Z", "+00:00"))
    offline_until = datetime.fromisoformat(
        normalized["offline_until"].replace("Z", "+00:00")
    )
    if not not_before < expires_at <= offline_until:
        raise ValueError("Entitlement time window is invalid")
    return normalized


def normalize_usage_receipt(value: dict[str, Any]) -> dict[str, Any]:
    if str(value.get("schema_version") or "") != "1.0":
        raise ValueError("unsupported Usage Receipt schema_version")
    period = value.get("period")
    if not isinstance(period, dict):
        raise ValueError("Usage Receipt period must be an object")
    sequence = str(value.get("sequence") or "")
    if not sequence.isdigit() or int(sequence) < 1:
        raise ValueError("Usage Receipt sequence must be a positive integer string")
    normalized = {
        "schema_version": "1.0",
        "receipt_id": _identifier(value.get("receipt_id"), field="receipt_id"),
        "entitlement_id": _identifier(value.get("entitlement_id"), field="entitlement_id"),
        "installation_key_thumbprint": _digest(
            value.get("installation_key_thumbprint"), field="installation_key_thumbprint"
        ),
        "meter_id": _identifier(value.get("meter_id"), field="meter_id"),
        "period": {
            "start": _timestamp(period.get("start"), field="period.start"),
            "end": _timestamp(period.get("end"), field="period.end"),
        },
        "quantity": decimal_text(value.get("quantity"), field="quantity"),
        "unit": str(value.get("unit") or "")[:64],
        "sequence": sequence,
        "source_event_digest": _digest(
            value.get("source_event_digest"), field="source_event_digest"
        ),
        "created_at": _timestamp(value.get("created_at"), field="created_at"),
    }
    if not normalized["unit"]:
        raise ValueError("Usage Receipt unit is required")
    period_start = datetime.fromisoformat(
        normalized["period"]["start"].replace("Z", "+00:00")
    )
    period_end = datetime.fromisoformat(
        normalized["period"]["end"].replace("Z", "+00:00")
    )
    if period_end < period_start:
        raise ValueError("Usage Receipt period is invalid")
    return normalized
