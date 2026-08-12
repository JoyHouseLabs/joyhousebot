"""Trusted Market registration, durable acquisition, and local import use cases."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx
from tuf.api.metadata import Metadata, Root

from joyhousebot.application.app_market_updates import AppMarketUpdateMixin
from joyhousebot.application.app_packs import AppPackService
from joyhousebot.application.errors import ConflictError, NotFoundError, ValidationError
from joyhousebot.domain.app_packs import normalize_app_id, normalize_app_version
from joyhousebot.market.client import MarketClient
from joyhousebot.market.local_keys import (
    decrypt_installation_private_key,
    encrypt_installation_private_key,
    installation_key_thumbprint,
    market_encryption_key,
)
from joyhousebot.market_protocol.canonical import canonical_sha256
from joyhousebot.market_protocol.contracts import (
    INSTALLATION_GRANT_MEDIA_TYPE,
    INSTALLATION_RECEIPT_MEDIA_TYPE,
    sign_json_contract,
    verify_json_contract,
)
from joyhousebot.market_protocol.dsse import (
    generate_ed25519_key_pair,
    public_key_bytes,
)
from joyhousebot.market_protocol.release import (
    normalize_market_id,
    normalize_publisher_id,
)

MarketClientFactory = Callable[[dict[str, Any]], MarketClient]


class AppMarketService(AppMarketUpdateMixin):
    def __init__(
        self,
        store: Any,
        *,
        client_factory: MarketClientFactory | None = None,
        encryption_key: bytes | None = None,
    ) -> None:
        self.store = store
        self.app_packs = AppPackService(store)
        self.client_factory = client_factory or self._client
        self._encryption_key = encryption_key

    @staticmethod
    def _client(registry: dict[str, Any]) -> MarketClient:
        return MarketClient(
            str(registry["base_url"]),
            auth_token_ref=str(registry.get("auth_token_ref") or ""),
        )

    async def register(
        self,
        *,
        base_url: str,
        trusted_root: dict[str, Any],
        discovery: dict[str, Any],
        auth_token_ref: str,
        policy: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any]:
        market_id = normalize_market_id(base_url)
        try:
            root = Metadata.from_dict(dict(trusted_root))
            if not isinstance(root.signed, Root):
                raise ValueError("trusted metadata is not a TUF root")
            root.verify_delegate("root", root)
            market_extension = dict(
                root.signed.unrecognized_fields.get("x-joyhouse-market") or {}
            )
            if normalize_market_id(str(market_extension.get("market_id") or "")) != market_id:
                raise ValueError("TUF root belongs to another Market")
            normalized_discovery = dict(discovery)
            if normalize_market_id(str(normalized_discovery.get("market_id") or "")) != market_id:
                raise ValueError("Market discovery identity does not match the configured origin")
            if "1.0" not in list(normalized_discovery.get("protocol_versions") or []):
                raise ValueError("Market discovery does not support protocol version 1.0")
            # Contract keys are trusted only through the pinned TUF root, never
            # through the unsigned discovery document by itself.
            normalized_discovery["contract_keys"] = dict(
                market_extension.get("contract_keys") or {}
            )
            if auth_token_ref and not str(auth_token_ref).startswith("env://"):
                raise ValueError("Market access token must use env://VARIABLE")
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return await asyncio.to_thread(
            self.store.save_app_market_registry,
            registry_id=f"marketreg_{uuid4().hex}",
            market_id=market_id,
            base_url=market_id,
            trusted_root=root.to_dict(),
            discovery=normalized_discovery,
            auth_token_ref=str(auth_token_ref or ""),
            policy={
                "allow_prerelease": bool(policy.get("allow_prerelease", False)),
                "allow_executable_extensions": bool(
                    policy.get("allow_executable_extensions", False)
                ),
                "last_tuf_versions": dict(policy.get("last_tuf_versions") or {}),
            },
            actor_id=actor_id,
        )

    async def list_registries(self) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.store.list_app_market_registries)

    async def ensure_installation_key(
        self, registry_id: str, *, user_id: str
    ) -> dict[str, Any]:
        found = await asyncio.to_thread(
            self.store.get_app_market_installation_key,
            registry_id,
            user_id=user_id,
        )
        if found is not None:
            return {key: value for key, value in found.items() if key != "private_ciphertext"}
        master_key = self._encryption_key or market_encryption_key()
        key = generate_ed25519_key_pair()
        public_key = f"base64url:{key.public_key}"
        saved = await asyncio.to_thread(
            self.store.save_app_market_installation_key,
            registry_id=registry_id,
            user_id=user_id,
            key_id=key.key_id,
            public_key=public_key,
            key_thumbprint=installation_key_thumbprint(public_key_bytes(public_key)),
            private_ciphertext=encrypt_installation_private_key(
                key.private_key,
                master_key=master_key,
                registry_id=registry_id,
                user_id=user_id,
            ),
        )
        return {name: value for name, value in saved.items() if name != "private_ciphertext"}

    async def sign_installation_receipt(
        self,
        registry_id: str,
        *,
        user_id: str,
        actor_id: str,
        value: dict[str, Any],
    ) -> dict[str, Any]:
        registry = await asyncio.to_thread(
            self.store.get_app_market_registry, registry_id=registry_id
        )
        if registry is None or registry["status"] != "active":
            raise NotFoundError("active Market Registry not found")
        key = await asyncio.to_thread(
            self.store.get_app_market_installation_key,
            registry_id,
            user_id=user_id,
        )
        if key is None or key["status"] != "active":
            raise ValidationError("active Market installation key not found")
        actual_state = str(value["actual_state"])
        local_id = str(value.get("local_installation_id") or "")
        actual_version = ""
        bundle_digest = ""
        installation_fingerprint = ""
        accepted_scopes: list[str] = []
        if actual_state != "failed":
            if not local_id:
                raise ValidationError("terminal installation receipts require local_installation_id")
            installation = await asyncio.to_thread(
                self.store.get_app_installation,
                local_id,
                expected_user_id=user_id,
            )
            if installation is None:
                raise NotFoundError("local App installation not found")
            status = str(installation["status"])
            allowed_states = {
                "installed": {"installed", "active"},
                "disabled": {"disabled"},
                "uninstalled": {"uninstalled"},
            }
            if status not in allowed_states[actual_state]:
                raise ConflictError("local App installation state does not match the receipt")
            actual_version = str(installation["version"])
            bundle_digest = str(installation.get("bundle_digest") or "")
            if actual_state == "installed" and not bundle_digest:
                raise ValidationError("Market installation receipt requires a verified bundle digest")
            accepted_scopes = sorted(
                {str(item) for item in installation.get("granted_permissions") or []}
            )
            installation_fingerprint = canonical_sha256(
                {
                    "local_installation_id": local_id,
                    "app_id": installation["app_id"],
                    "version": actual_version,
                    "manifest_sha256": installation["manifest_sha256"],
                    "bundle_digest": bundle_digest,
                    "granted_permissions": accepted_scopes,
                    "status": status,
                }
            )
        elif not str(value.get("error_code") or ""):
            raise ValidationError("failed installation receipts require error_code")
        payload = {
            "schema_version": "1.0",
            "receipt_id": value["receipt_id"],
            "device_id": value["device_id"],
            "installation_id": value["installation_id"],
            "intent_revision": int(value["intent_revision"]),
            "actual_state": actual_state,
            "actual_version": actual_version,
            "runtime_instance_id": value["runtime_instance_id"],
            "bundle_digest": bundle_digest,
            "installation_fingerprint": installation_fingerprint,
            "accepted_scopes": accepted_scopes,
            "error_code": str(value.get("error_code") or ""),
            "error_message": str(value.get("error_message") or ""),
        }
        request_hash = canonical_sha256(payload)
        prior = await asyncio.to_thread(
            self.store.get_app_market_receipt_signature,
            registry_id=registry_id,
            user_id=user_id,
            receipt_id=str(value["receipt_id"]),
        )
        if prior is not None:
            if prior["request_hash"] != request_hash:
                raise ConflictError("installation receipt signing idempotency conflict")
            return {"payload": prior["payload"], "envelope": prior["envelope"]}
        private_key = decrypt_installation_private_key(
            str(key["private_ciphertext"]),
            master_key=self._encryption_key or market_encryption_key(),
            registry_id=registry_id,
            user_id=user_id,
        )
        envelope = sign_json_contract(
            payload,
            payload_type=INSTALLATION_RECEIPT_MEDIA_TYPE,
            private_key=private_key,
        )
        try:
            saved = await asyncio.to_thread(
                self.store.save_app_market_receipt_signature,
                registry_id=registry_id,
                user_id=user_id,
                receipt_id=str(value["receipt_id"]),
                request_hash=request_hash,
                payload=payload,
                envelope=envelope,
                actor_id=actor_id,
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc
        return {"payload": saved["payload"], "envelope": saved["envelope"]}

    async def install_acquisition(
        self,
        acquisition_id: str,
        *,
        user_id: str,
        actor_id: str,
        installation_grant: dict[str, Any],
        configuration: dict[str, Any],
        granted_permissions: list[str],
    ) -> dict[str, Any]:
        acquisition = await self.get_acquisition(acquisition_id, user_id=user_id)
        if acquisition["status"] != "imported":
            raise ConflictError("Market acquisition must be accepted before installation")
        registry_id = str(acquisition["registry_id"])
        registry = await asyncio.to_thread(
            self.store.get_app_market_registry, registry_id=registry_id
        )
        if registry is None or registry["status"] != "active":
            raise NotFoundError("active Market Registry not found")
        contract = dict(
            dict(registry.get("discovery") or {}).get("contract_keys") or {}
        ).get("installation")
        if not isinstance(contract, dict):
            raise ValidationError("trusted Market installation contract key is missing")
        key_id = str(contract.get("key_id") or "")
        public_key = str(contract.get("public_key") or "")
        try:
            payload, signer = verify_json_contract(
                installation_grant.get("envelope") or {},
                payload_type=INSTALLATION_GRANT_MEDIA_TYPE,
                public_keys={key_id: public_key},
            )
        except ValueError as exc:
            raise ValidationError(f"invalid Installation Grant: {exc}") from exc
        if signer != key_id:
            raise ValidationError("Installation Grant signer does not match trusted Registry")
        supplied_payload = installation_grant.get("payload")
        if supplied_payload is not None and supplied_payload != payload:
            raise ValidationError("Installation Grant payload does not match its signed envelope")
        try:
            expires_at = datetime.fromisoformat(
                str(payload["expires_at"]).replace("Z", "+00:00")
            )
        except (KeyError, ValueError) as exc:
            raise ValidationError("Installation Grant expires_at is invalid") from exc
        if expires_at.tzinfo is None or expires_at <= datetime.now(timezone.utc):
            raise ConflictError("Installation Grant has expired")
        release = dict(payload.get("release") or {})
        expected_permissions = sorted({str(item) for item in granted_permissions})
        expected = {
            "market_id": str(registry["market_id"]),
            "publisher_id": str(acquisition["publisher_id"]),
            "app_id": str(acquisition["app_id"]),
            "version": str(acquisition.get("resolved_version") or ""),
            "bundle_digest": str(acquisition.get("bundle_digest") or ""),
            "entitlement_id": str(acquisition.get("entitlement_id") or ""),
            "permission_digest": canonical_sha256(expected_permissions),
        }
        actual = {
            "market_id": str(payload.get("market_id") or ""),
            "publisher_id": str(release.get("publisher_id") or ""),
            "app_id": str(release.get("app_id") or ""),
            "version": str(release.get("version") or ""),
            "bundle_digest": str(release.get("bundle_digest") or ""),
            "entitlement_id": str(payload.get("entitlement_id") or ""),
            "permission_digest": str(payload.get("permission_digest") or ""),
        }
        if actual != expected:
            raise ConflictError("Installation Grant does not match the verified acquisition")
        if str(payload.get("desired_state") or "") not in {"installed", "updated"}:
            raise ConflictError("Installation Grant does not authorize installation")
        grant_id = str(payload.get("grant_id") or "")
        if not grant_id:
            raise ValidationError("Installation Grant is missing grant_id")
        request_hash = canonical_sha256(
            {
                "acquisition_id": acquisition_id,
                "grant": payload,
                "configuration": configuration,
                "granted_permissions": expected_permissions,
            }
        )
        prior = await asyncio.to_thread(
            self.store.get_app_market_grant_consumption,
            registry_id=registry_id,
            user_id=user_id,
            grant_id=grant_id,
        )
        if prior is not None:
            if prior["request_hash"] != request_hash:
                raise ConflictError("Installation Grant idempotency conflict")
            return dict(prior["result"])
        app_id = expected["app_id"]
        version = expected["version"]
        local_release = await asyncio.to_thread(self.store.get_app_release, app_id, version)
        if local_release is None:
            raise NotFoundError("imported App release not found")
        if local_release["status"] == "draft":
            await self.app_packs.publish(
                app_id, version, actor_id=actor_id, user_id=user_id
            )
        elif local_release["status"] != "published":
            raise ConflictError("imported App release is not installable")
        installation = await self.app_packs.install(
            app_id,
            version,
            user_id=user_id,
            actor_id=actor_id,
            configuration=configuration,
            granted_permissions=expected_permissions,
        )
        result = {
            "acquisition_id": acquisition_id,
            "registry_id": registry_id,
            "cloud_installation_id": str(payload.get("installation_id") or ""),
            "intent_revision": int(payload.get("intent_revision") or 0),
            "grant_id": grant_id,
            "installation": installation,
        }
        try:
            saved = await asyncio.to_thread(
                self.store.save_app_market_grant_consumption,
                registry_id=registry_id,
                user_id=user_id,
                grant_id=grant_id,
                acquisition_id=acquisition_id,
                request_hash=request_hash,
                local_installation_id=str(installation["installation_id"]),
                result=result,
                actor_id=actor_id,
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc
        return dict(saved["result"])

    async def request_acquisition(
        self,
        *,
        registry_id: str,
        publisher_id: str,
        app_id: str,
        version: str | None,
        channel: str,
        offer_id: str | None,
        request_key: str,
        user_id: str,
        actor_id: str,
        acquisition_policy: str = "manual",
        entitlement: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        registry = await asyncio.to_thread(
            self.store.get_app_market_registry, registry_id=registry_id
        )
        if registry is None or registry["status"] != "active":
            raise NotFoundError("active Market Registry not found")
        if channel not in {"stable", "beta", "security"}:
            raise ValidationError("invalid Market update channel")
        if not str(request_key or "").strip():
            raise ValidationError("Idempotency-Key is required for App acquisition")
        if acquisition_policy not in {"manual", "download", "stage"}:
            raise ValidationError("invalid App acquisition policy")
        try:
            normalized_publisher = normalize_publisher_id(publisher_id)
            normalized_app = normalize_app_id(app_id)
            normalized_version = normalize_app_version(version) if version else None
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        return await asyncio.to_thread(
            self.store.create_app_acquisition,
            acquisition_id=f"acq_{uuid4().hex}",
            user_id=user_id,
            registry_id=registry_id,
            publisher_id=normalized_publisher,
            app_id=normalized_app,
            requested_version=normalized_version,
            channel=channel,
            offer_id=str(offer_id or "") or None,
            provided_entitlement=dict(entitlement or {}),
            acquisition_policy=acquisition_policy,
            request_key=str(request_key)[:200],
            actor_id=actor_id,
        )

    async def list_acquisitions(self, *, user_id: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self.store.list_app_acquisitions, user_id=user_id)

    async def save_update_subscription(
        self,
        *,
        user_id: str,
        installation_id: str,
        registry_id: str,
        publisher_id: str,
        app_id: str,
        channel: str,
        version_constraint: str,
        policy: str,
        allow_security_patch_download: bool,
        allow_auto_stage: bool,
        allow_auto_activate: bool,
    ) -> dict[str, Any]:
        if channel not in {"stable", "beta", "security"}:
            raise ValidationError("invalid update channel")
        if policy not in {"notify", "download", "stage", "activate_safe"}:
            raise ValidationError("invalid update policy")
        if policy == "activate_safe" or allow_auto_activate:
            raise ValidationError(
                "activate_safe requires Eval and rollout ACK gates and is not enabled"
            )
        try:
            self._version_specifier(version_constraint)
        except ValueError as exc:
            raise ValidationError("invalid update version constraint") from exc
        try:
            return await asyncio.to_thread(
                self.store.save_app_update_subscription,
                subscription_id=f"appsub_{uuid4().hex}",
                user_id=user_id,
                installation_id=installation_id,
                registry_id=registry_id,
                publisher_id=normalize_publisher_id(publisher_id),
                app_id=normalize_app_id(app_id),
                channel=channel,
                version_constraint=str(version_constraint or "*")[:256],
                policy=policy,
                allow_security_patch_download=allow_security_patch_download,
                allow_auto_stage=allow_auto_stage,
                allow_auto_activate=allow_auto_activate,
            )
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc

    async def list_update_subscriptions(self, *, user_id: str) -> list[dict[str, Any]]:
        return await asyncio.to_thread(
            self.store.list_app_update_subscriptions, user_id=user_id
        )

    async def get_acquisition(
        self, acquisition_id: str, *, user_id: str
    ) -> dict[str, Any]:
        value = await asyncio.to_thread(
            self.store.get_app_acquisition, acquisition_id, user_id=user_id
        )
        if value is None:
            raise NotFoundError("App acquisition not found")
        return value

    async def process_next(self, *, worker_id: str) -> bool:
        acquisition = await asyncio.to_thread(
            self.store.claim_app_acquisition, worker_id=worker_id, lease_seconds=180
        )
        if acquisition is None:
            return False
        acquisition_id = str(acquisition["acquisition_id"])
        lease_version = int(acquisition["lease_version"])
        try:
            registry = await asyncio.to_thread(
                self.store.get_app_market_registry,
                registry_id=str(acquisition["registry_id"]),
            )
            if registry is None or registry["status"] != "active":
                raise RuntimeError("active Market Registry not found")
            client = self.client_factory(registry)
            completed_status = (
                "staged" if acquisition["acquisition_policy"] == "download"
                else "awaiting_acceptance"
            )
            await self._advance(
                acquisition_id,
                worker_id=worker_id,
                lease_version=lease_version,
                status="resolving",
            )
            remote_discovery = await client.discovery()
            discovery = dict(registry["discovery"])
            remote_discovery["contract_keys"] = dict(discovery.get("contract_keys") or {})
            resolution = await client.resolve(
                publisher_id=str(acquisition["publisher_id"]),
                app_id=str(acquisition["app_id"]),
                version=str(acquisition["requested_version"] or "") or None,
                channel=str(acquisition["channel"]),
                offer_id=str(acquisition["offer_id"] or "") or None,
            )
            resolved_release = dict(dict(resolution.get("payload") or {}).get("release") or {})
            if (
                resolved_release.get("publisher_id") != acquisition["publisher_id"]
                or resolved_release.get("app_id") != acquisition["app_id"]
            ):
                raise ValueError("Market Resolution returned another App identity")
            if acquisition["requested_version"] and (
                resolved_release.get("version") != acquisition["requested_version"]
            ):
                raise ValueError("Market Resolution returned another App version")
            await self._advance(
                acquisition_id,
                worker_id=worker_id,
                lease_version=lease_version,
                status="fetching",
                values={"resolution": resolution},
            )
            policy = dict(registry.get("policy") or {})
            verified, bundle, report = await client.verify_acquisition(
                trusted_root=dict(registry["trusted_root"]),
                minimum_versions=dict(policy.get("last_tuf_versions") or {}),
                discovery=remote_discovery,
                resolution=resolution,
            )
            await self._advance(
                acquisition_id,
                worker_id=worker_id,
                lease_version=lease_version,
                status="verifying",
            )
            entitlement_id: str | None = None
            entitlement_rule = dict(dict(resolution["payload"]).get("entitlement") or {})
            if bool(entitlement_rule.get("required")):
                installation_key = await self.ensure_installation_key(
                    str(acquisition["registry_id"]), user_id=str(acquisition["user_id"])
                )
                entitlement_value = dict(acquisition.get("provided_entitlement") or {})
                if not entitlement_value:
                    entitlement_value = await client.entitlement(
                        publisher_id=str(acquisition["publisher_id"]),
                        app_id=str(acquisition["app_id"]),
                        installation_key_thumbprint=str(installation_key["key_thumbprint"]),
                    )
                payload, envelope = await client.verify_entitlement(
                    entitlement_value,
                    discovery=remote_discovery,
                    expected_thumbprint=str(installation_key["key_thumbprint"]),
                    publisher_id=str(acquisition["publisher_id"]),
                    app_id=str(acquisition["app_id"]),
                )
                if acquisition["offer_id"] and payload["offer_id"] != acquisition["offer_id"]:
                    raise ValueError("Entitlement is for another Offer")
                await asyncio.to_thread(
                    self.store.save_app_market_entitlement,
                    user_id=str(acquisition["user_id"]),
                    registry_id=str(acquisition["registry_id"]),
                    publisher_id=str(acquisition["publisher_id"]),
                    app_id=str(acquisition["app_id"]),
                    payload=payload,
                    envelope=envelope,
                )
                entitlement_id = str(payload["entitlement_id"])
            permission_diff = await asyncio.to_thread(
                self._permission_diff,
                verified.manifest,
                user_id=str(acquisition["user_id"]),
            )
            report.update(
                {
                    "author_signature": "verified",
                    "market_attestation": "verified",
                    "bundle_digest": str(dict(resolution["payload"])["release"]["bundle_digest"]),
                    "component_count": len(verified.components),
                }
            )
            policy["last_tuf_versions"] = dict(report["tuf_versions"])
            await asyncio.to_thread(
                self.store.record_app_market_refresh,
                str(acquisition["registry_id"]),
                discovery=remote_discovery,
                policy=policy,
            )
            await self._advance(
                acquisition_id,
                worker_id=worker_id,
                lease_version=lease_version,
                status=completed_status,
                values={
                    "resolved_version": verified.manifest["version"],
                    "release_descriptor": verified.descriptor,
                    "app_manifest": verified.manifest,
                    "verification_report": report,
                    "permission_diff": permission_diff,
                    "bundle_digest": report["bundle_digest"],
                    "bundle": bundle,
                    "entitlement_id": entitlement_id,
                },
            )
        except httpx.HTTPError as exc:
            await self._fail(
                acquisition_id,
                worker_id=worker_id,
                lease_version=lease_version,
                status="failed",
                exc=exc,
            )
        except (ValueError, KeyError) as exc:
            await self._fail(
                acquisition_id,
                worker_id=worker_id,
                lease_version=lease_version,
                status="quarantined",
                exc=exc,
            )
        except Exception as exc:
            await self._fail(
                acquisition_id,
                worker_id=worker_id,
                lease_version=lease_version,
                status="failed",
                exc=exc,
            )
        return True

    async def accept(
        self, acquisition_id: str, *, user_id: str, actor_id: str
    ) -> dict[str, Any]:
        acquisition = await self.get_acquisition(acquisition_id, user_id=user_id)
        if acquisition["status"] not in {"staged", "awaiting_acceptance"}:
            raise ConflictError("App acquisition is not awaiting acceptance")
        manifest = dict(acquisition["app_manifest"])
        existing = await asyncio.to_thread(
            self.store.get_app_release, manifest["app_id"], manifest["version"]
        )
        if existing is not None:
            origin = dict(existing.get("origin_ref") or {})
            if origin.get("bundle_digest") != acquisition["bundle_digest"]:
                raise ConflictError(
                    "a different local App release already uses this identity"
                )
        else:
            await self.app_packs.save_draft(manifest, actor_id=actor_id)
            await asyncio.to_thread(
                self.store.set_app_release_origin,
                manifest["app_id"],
                manifest["version"],
                origin_ref={
                    "registry_id": acquisition["registry_id"],
                    "publisher_id": acquisition["publisher_id"],
                    "app_id": acquisition["app_id"],
                    "version": acquisition["resolved_version"],
                    "release_id": dict(
                        dict(acquisition.get("resolution") or {}).get("payload") or {}
                    ).get("release", {}).get("release_id"),
                    "bundle_digest": acquisition["bundle_digest"],
                },
                bundle_digest=acquisition["bundle_digest"],
            )
        try:
            return await asyncio.to_thread(
                self.store.finish_app_acquisition_action,
                acquisition_id,
                user_id=user_id,
                status="imported",
                actor_id=actor_id,
                details={"local_release": f"{manifest['app_id']}@{manifest['version']}"},
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc

    async def reject(
        self, acquisition_id: str, *, user_id: str, actor_id: str
    ) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(
                self.store.finish_app_acquisition_action,
                acquisition_id,
                user_id=user_id,
                status="rejected",
                actor_id=actor_id,
                details={},
            )
        except ValueError as exc:
            raise ConflictError(str(exc)) from exc

    async def _advance(self, acquisition_id: str, **values: Any) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.store.advance_app_acquisition, acquisition_id, **values
        )

    async def _fail(
        self,
        acquisition_id: str,
        *,
        worker_id: str,
        lease_version: int,
        status: str,
        exc: Exception,
    ) -> None:
        await self._advance(
            acquisition_id,
            worker_id=worker_id,
            lease_version=lease_version,
            status=status,
            values={
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc)[:1000],
                }
            },
        )

    def _permission_diff(
        self, manifest: dict[str, Any], *, user_id: str
    ) -> dict[str, Any]:
        current = next(
            (
                item
                for item in self.store.list_app_installations(user_id=user_id)
                if item["app_id"] == manifest["app_id"]
                and item["status"] != "uninstalled"
            ),
            None,
        )
        previous = dict(current.get("manifest") or {}) if current else {}

        def additions(field: str) -> list[Any]:
            before = {str(item) for item in previous.get(field) or []}
            return [item for item in manifest.get(field) or [] if str(item) not in before]

        old_data = dict(previous.get("data_practices") or {})
        new_data = dict(manifest.get("data_practices") or {})
        old_domains = set(old_data.get("outbound_domains") or [])
        return {
            "new_install": current is None,
            "permissions_added": additions("permissions"),
            "integrations_added": additions("integrations"),
            "extensions_added": [
                item
                for item in manifest.get("extensions") or []
                if item not in list(previous.get("extensions") or [])
            ],
            "secrets_added": [
                item
                for item in manifest.get("secrets") or []
                if item not in list(previous.get("secrets") or [])
            ],
            "outbound_domains_added": sorted(
                set(new_data.get("outbound_domains") or []) - old_domains
            ),
            "meters_added": [
                item
                for item in manifest.get("metering") or []
                if item not in list(previous.get("metering") or [])
            ],
        }
