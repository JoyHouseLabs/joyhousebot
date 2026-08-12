"""Fail-closed Remote Market protocol client."""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urljoin

import httpx
from tuf.api.metadata import Metadata, Root, Snapshot, Targets, Timestamp

from joyhousebot.market.local_keys import resolve_market_secret
from joyhousebot.market_protocol.bundle import AppBundle, verify_app_bundle
from joyhousebot.market_protocol.canonical import bytes_sha256
from joyhousebot.market_protocol.contracts import (
    ATTESTATION_MEDIA_TYPE,
    ENTITLEMENT_MEDIA_TYPE,
    GOVERNANCE_MEDIA_TYPE,
    RESOLUTION_MEDIA_TYPE,
    normalize_entitlement,
    verify_json_contract,
)
from joyhousebot.market_protocol.release import normalize_market_id
from joyhousebot.utils.ssrf import (
    ResponseTooLargeError,
    SsrfBlockedError,
    SsrfProtectedTransport,
    validate_url,
)

_REDIRECTS = {301, 302, 303, 307, 308}
_MAX_METADATA = 4 * 1024 * 1024
_MAX_BUNDLE = 256 * 1024 * 1024


class MarketClient:
    def __init__(
        self,
        base_url: str,
        *,
        auth_token_ref: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = normalize_market_id(base_url)
        self.auth_token_ref = str(auth_token_ref or "")
        self._injected_client = client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_value: dict[str, Any] | None = None,
        maximum: int = _MAX_METADATA,
        authenticated: bool = False,
    ) -> tuple[httpx.Response, bytes]:
        current = urljoin(f"{self.base_url}/", path.lstrip("/"))
        headers: dict[str, str] = {"Accept": "application/json"}
        if authenticated:
            token = resolve_market_secret(self.auth_token_ref)
            headers["Authorization"] = f"Bearer {token}"
        owns = self._injected_client is None
        client = self._injected_client or httpx.AsyncClient(
            transport=SsrfProtectedTransport(), timeout=httpx.Timeout(30.0), follow_redirects=False
        )
        try:
            for _ in range(6):
                ok, error = validate_url(current)
                if not ok:
                    raise SsrfBlockedError(error)
                async with client.stream(
                    method,
                    current,
                    headers=headers,
                    json=json_value,
                ) as response:
                    if response.status_code in _REDIRECTS and response.headers.get("location"):
                        current = urljoin(current, response.headers["location"])
                        continue
                    response.raise_for_status()
                    length = response.headers.get("content-length", "")
                    if length.isdigit() and int(length) > maximum:
                        raise ResponseTooLargeError("Market response exceeds the size limit")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > maximum:
                            raise ResponseTooLargeError("Market response exceeds the size limit")
                        chunks.append(chunk)
                    return response, b"".join(chunks)
            raise ValueError("Market response exceeded the redirect limit")
        finally:
            if owns:
                await client.aclose()

    async def discovery(self) -> dict[str, Any]:
        _, body = await self._request("GET", "/.well-known/joyhouse-market")
        value = httpx.Response(200, content=body).json()
        if not isinstance(value, dict):
            raise ValueError("Market discovery response must be an object")
        if normalize_market_id(str(value.get("market_id") or "")) != self.base_url:
            raise ValueError("Market discovery identity does not match the configured origin")
        if "1.0" not in list(value.get("protocol_versions") or []):
            raise ValueError("Market does not support protocol version 1.0")
        return value

    async def metadata(self, name: str) -> bytes:
        if name not in {
            "root.json",
            "targets.json",
            "snapshot.json",
            "timestamp.json",
        } and not (name.removesuffix(".root.json").isdigit() and name.endswith(".root.json")):
            raise ValueError("unsupported TUF metadata name")
        _, body = await self._request("GET", f"/tuf/{name}")
        return body

    async def resolve(
        self,
        *,
        publisher_id: str,
        app_id: str,
        version: str | None,
        channel: str,
        offer_id: str | None,
    ) -> dict[str, Any]:
        _, body = await self._request(
            "POST",
            "/api/market/v1/resolutions",
            json_value={
                "publisher_id": publisher_id,
                "app_id": app_id,
                "version": version,
                "channel": channel,
                "offer_id": offer_id,
                "runtime": {},
            },
        )
        value = httpx.Response(200, content=body).json()
        if not isinstance(value, dict):
            raise ValueError("Market Resolution must be an object")
        return value

    async def target(self, path: str) -> bytes:
        _, body = await self._request("GET", f"/targets/{path.lstrip('/')}", maximum=_MAX_BUNDLE)
        return body

    async def entitlement(
        self, *, publisher_id: str, app_id: str, installation_key_thumbprint: str
    ) -> dict[str, Any]:
        _, body = await self._request(
            "POST",
            "/api/market/v1/entitlements/resolve",
            json_value={
                "publisher_id": publisher_id,
                "app_id": app_id,
                "installation_key_thumbprint": installation_key_thumbprint,
            },
            authenticated=True,
        )
        value = httpx.Response(200, content=body).json()
        if not isinstance(value, dict):
            raise ValueError("Market Entitlement must be an object")
        return value

    async def update_feed(
        self,
        *,
        cursor: str,
        discovery: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_cursor = str(cursor or "0")
        if not normalized_cursor.isdigit():
            raise ValueError("Market update cursor must be numeric")
        _, body = await self._request(
            "GET",
            f"/api/market/v1/update-feeds/global?cursor={normalized_cursor}&limit=200",
        )
        value = httpx.Response(200, content=body).json()
        if not isinstance(value, dict) or not str(value.get("cursor") or "").isdigit():
            raise ValueError("Market Update Feed must contain a numeric cursor")
        governance_key = dict(
            dict(discovery.get("contract_keys") or {}).get("governance") or {}
        )
        verified_decisions: list[dict[str, Any]] = []
        for item in value.get("decisions") or []:
            if not isinstance(item, dict):
                raise ValueError("Market Update Feed decision must be an object")
            payload, _ = verify_json_contract(
                dict(item.get("envelope") or {}),
                payload_type=GOVERNANCE_MEDIA_TYPE,
                public_keys={
                    str(governance_key.get("key_id") or ""): str(
                        governance_key.get("public_key") or ""
                    )
                },
            )
            if payload != dict(item.get("payload") or {}):
                raise ValueError("Market Update Feed decision payload differs from its signature")
            verified_decisions.append({"payload": payload, "envelope": item["envelope"]})
        releases = value.get("releases") or []
        if not isinstance(releases, list) or not all(isinstance(item, dict) for item in releases):
            raise ValueError("Market Update Feed releases must be objects")
        return {
            "cursor": str(value["cursor"]),
            "snapshot_version": int(value.get("snapshot_version") or 0),
            "releases": [dict(item) for item in releases],
            "decisions": verified_decisions,
        }

    async def verify_acquisition(
        self,
        *,
        trusted_root: dict[str, Any],
        minimum_versions: dict[str, int],
        discovery: dict[str, Any],
        resolution: dict[str, Any],
    ) -> tuple[AppBundle, bytes, dict[str, Any]]:
        root = Metadata.from_dict(dict(trusted_root))
        if not isinstance(root.signed, Root):
            raise ValueError("pinned Market root is not TUF root metadata")
        root = await self._rotate_root(root)
        timestamp_raw = await self.metadata("timestamp.json")
        snapshot_raw = await self.metadata("snapshot.json")
        targets_raw = await self.metadata("targets.json")
        timestamp = Metadata.from_bytes(timestamp_raw)
        snapshot = Metadata.from_bytes(snapshot_raw)
        targets = Metadata.from_bytes(targets_raw)
        if not isinstance(timestamp.signed, Timestamp):
            raise ValueError("invalid TUF timestamp metadata")
        if not isinstance(snapshot.signed, Snapshot):
            raise ValueError("invalid TUF snapshot metadata")
        if not isinstance(targets.signed, Targets):
            raise ValueError("invalid TUF targets metadata")
        root.verify_delegate("timestamp", timestamp)
        root.verify_delegate("snapshot", snapshot)
        root.verify_delegate("targets", targets)
        for name, metadata in (
            ("root", root),
            ("timestamp", timestamp),
            ("snapshot", snapshot),
            ("targets", targets),
        ):
            if metadata.signed.is_expired(datetime.now(timezone.utc)):
                raise ValueError(f"TUF {name} metadata is expired")
            minimum = int(minimum_versions.get(name) or 0)
            if metadata.signed.version < minimum:
                raise ValueError(f"TUF {name} metadata rollback detected")
        timestamp.signed.snapshot_meta.verify_length_and_hashes(snapshot_raw)
        target_meta = snapshot.signed.meta.get("targets.json")
        if target_meta is None:
            raise ValueError("TUF snapshot does not bind targets metadata")
        target_meta.verify_length_and_hashes(targets_raw)

        contract_keys = dict(discovery.get("contract_keys") or {})
        resolution_key = dict(contract_keys.get("resolution") or {})
        resolution_payload, _ = verify_json_contract(
            dict(resolution.get("envelope") or {}),
            payload_type=RESOLUTION_MEDIA_TYPE,
            public_keys={
                str(resolution_key.get("key_id") or ""): str(
                    resolution_key.get("public_key") or ""
                )
            },
        )
        if resolution_payload != dict(resolution.get("payload") or {}):
            raise ValueError("Resolution envelope and payload differ")
        release = dict(resolution_payload.get("release") or {})
        target_path = str(release.get("target_path") or "")
        target_parts = PurePosixPath(target_path)
        if target_parts.is_absolute() or ".." in target_parts.parts:
            raise ValueError("resolved TUF target path is unsafe")
        target = targets.signed.targets.get(target_path)
        if target is None:
            raise ValueError("resolved App release is absent from TUF targets")
        target_custom = dict(target.unrecognized_fields.get("custom") or {})
        if (
            target_custom.get("status") != "published"
            or target_custom.get("bundle_digest") != release.get("bundle_digest")
        ):
            raise ValueError("TUF target does not authorize the resolved App release")
        bundle = await self.target(target_path)
        target.verify_length_and_hashes(bundle)
        if bytes_sha256(bundle) != str(release.get("bundle_digest") or ""):
            raise ValueError("resolved App bundle digest does not match")
        publisher_keys = {
            str(item.get("key_id") or ""): str(item.get("public_key") or "")
            for item in resolution_payload.get("publisher_keys") or []
            if isinstance(item, dict)
        }
        with tempfile.NamedTemporaryFile(suffix=".joyhouse-app") as stream:
            stream.write(bundle)
            stream.flush()
            verified = verify_app_bundle(
                Path(stream.name),
                public_keys=publisher_keys,
                expected_market_id=self.base_url,
                expected_publisher_id=str(release.get("publisher_id") or ""),
            )
        attestation = dict(resolution_payload.get("market_attestation") or {})
        attestation_key = dict(contract_keys.get("attestation") or {})
        attestation_payload, _ = verify_json_contract(
            dict(attestation.get("envelope") or {}),
            payload_type=ATTESTATION_MEDIA_TYPE,
            public_keys={
                str(attestation_key.get("key_id") or ""): str(
                    attestation_key.get("public_key") or ""
                )
            },
        )
        if attestation_payload != dict(attestation.get("payload") or {}):
            raise ValueError("Market Attestation envelope and payload differ")
        if (
            dict(attestation_payload.get("subject") or {}).get("bundle_digest")
            != release.get("bundle_digest")
            or attestation_payload.get("decision") != "approved"
        ):
            raise ValueError("Market Attestation does not approve the resolved bundle")
        versions = {
            "root": root.signed.version,
            "timestamp": timestamp.signed.version,
            "snapshot": snapshot.signed.version,
            "targets": targets.signed.version,
        }
        return verified, bundle, {"tuf_versions": versions, "target_path": target_path}

    async def verify_entitlement(
        self,
        value: dict[str, Any],
        *,
        discovery: dict[str, Any],
        expected_thumbprint: str,
        publisher_id: str,
        app_id: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        key = dict(dict(discovery.get("contract_keys") or {}).get("entitlement") or {})
        payload, _ = verify_json_contract(
            dict(value.get("envelope") or {}),
            payload_type=ENTITLEMENT_MEDIA_TYPE,
            public_keys={str(key.get("key_id") or ""): str(key.get("public_key") or "")},
        )
        normalized = normalize_entitlement(payload)
        if normalized != dict(value.get("payload") or {}):
            raise ValueError("Entitlement envelope and payload differ")
        if normalized["issuer"] != self.base_url:
            raise ValueError("Entitlement issuer does not match Market")
        if normalized["subject"]["installation_key_thumbprint"] != expected_thumbprint:
            raise ValueError("Entitlement is bound to another installation key")
        if (
            normalized["app"]["publisher_id"] != publisher_id
            or normalized["app"]["app_id"] != app_id
        ):
            raise ValueError("Entitlement is for another App")
        now = datetime.now(timezone.utc)
        not_before = datetime.fromisoformat(normalized["not_before"].replace("Z", "+00:00"))
        expires_at = datetime.fromisoformat(normalized["expires_at"].replace("Z", "+00:00"))
        if normalized["status"] != "active" or not (not_before <= now < expires_at):
            raise ValueError("Entitlement is not currently active")
        return normalized, dict(value["envelope"])

    async def _rotate_root(self, trusted: Metadata[Root]) -> Metadata[Root]:
        current = trusted
        for next_version in range(current.signed.version + 1, current.signed.version + 33):
            try:
                raw = await self.metadata(f"{next_version}.root.json")
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    break
                raise
            candidate = Metadata.from_bytes(raw)
            if not isinstance(candidate.signed, Root) or candidate.signed.version != next_version:
                raise ValueError("TUF root rotation is not sequential")
            current.verify_delegate("root", candidate)
            candidate.verify_delegate("root", candidate)
            current = candidate
        latest = Metadata.from_bytes(await self.metadata("root.json"))
        if not isinstance(latest.signed, Root):
            raise ValueError("Market root metadata is invalid")
        if latest.signed.version != current.signed.version:
            raise ValueError("Market root metadata skipped a trusted rotation")
        if latest.to_dict() != current.to_dict():
            raise ValueError("Market root metadata changed without a version rotation")
        return current
