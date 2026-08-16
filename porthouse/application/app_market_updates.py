"""Scheduler-owned remote App update discovery and staging."""

from __future__ import annotations

import asyncio
from typing import Any

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version


class AppMarketUpdateMixin:
    async def process_update_next(self, *, worker_id: str) -> bool:
        subscription = await asyncio.to_thread(
            self.store.claim_app_update_subscription,
            worker_id=worker_id,
            lease_seconds=120,
        )
        if subscription is None:
            return False
        subscription_id = str(subscription["subscription_id"])
        lease_version = int(subscription["lease_version"])
        cursor = str(subscription["cursor"])
        snapshot_version = int(subscription["last_snapshot_version"])
        latest: dict[str, Any] = {}
        event_type = "checked"
        details: dict[str, Any] = {}
        error: dict[str, Any] | None = None
        try:
            registry = await asyncio.to_thread(
                self.store.get_app_market_registry,
                registry_id=str(subscription["registry_id"]),
            )
            if registry is None or registry["status"] != "active":
                raise RuntimeError("active Market Registry not found")
            client = self.client_factory(registry)
            feed = await client.update_feed(
                cursor=cursor,
                discovery=dict(registry["discovery"]),
            )
            cursor = str(feed["cursor"])
            snapshot_version = int(feed["snapshot_version"])
            candidates = self._update_candidates(subscription, feed["releases"])
            if candidates:
                latest = max(candidates, key=lambda item: Version(str(item["version"])))
                event_type = "update_available"
                details = {
                    "version": latest["version"],
                    "bundle_digest": latest.get("bundle_digest"),
                    "policy": subscription["policy"],
                }
                if subscription["policy"] in {"download", "stage"}:
                    acquisition = await self.request_acquisition(
                        registry_id=str(subscription["registry_id"]),
                        publisher_id=str(subscription["publisher_id"]),
                        app_id=str(subscription["app_id"]),
                        version=str(latest["version"]),
                        channel=str(subscription["channel"]),
                        offer_id=str(subscription["entitlement_offer_id"] or "") or None,
                        request_key=(
                            f"update:{subscription_id}:"
                            f"{latest.get('bundle_digest') or latest['version']}"
                        ),
                        user_id=str(subscription["user_id"]),
                        actor_id=f"system:app-update:{subscription_id}",
                        acquisition_policy=str(subscription["policy"]),
                        entitlement=dict(subscription.get("entitlement") or {}),
                    )
                    details["acquisition_id"] = acquisition["acquisition_id"]
            relevant_decisions = [
                item["payload"]
                for item in feed["decisions"]
                if self._decision_affects_subscription(item["payload"], subscription)
            ]
            if relevant_decisions:
                details["governance_decisions"] = relevant_decisions
                if any(
                    item.get("action") in {"suspend", "revoke"}
                    for item in relevant_decisions
                ):
                    event_type = "security_action"
        except Exception as exc:
            event_type = "check_failed"
            error = {"type": type(exc).__name__, "message": str(exc)[:1000]}
            details = dict(error)
        await asyncio.to_thread(
            self.store.finish_app_update_subscription_check,
            subscription_id,
            worker_id=worker_id,
            lease_version=lease_version,
            cursor=cursor,
            snapshot_version=snapshot_version,
            latest_release=latest,
            error=error,
            event_type=event_type,
            details=details,
        )
        return True

    @staticmethod
    def _version_specifier(value: str) -> SpecifierSet:
        text = str(value or "*").strip()
        if text in {"", "*"}:
            return SpecifierSet()
        tokens = text.replace(",", " ").split()
        normalized = [
            token if token[:1] in "<>=!~" else f"=={token}" for token in tokens
        ]
        return SpecifierSet(",".join(normalized))

    @classmethod
    def _update_candidates(
        cls,
        subscription: dict[str, Any],
        releases: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        try:
            current = Version(str(subscription["current_version"]))
            constraint = cls._version_specifier(str(subscription["version_constraint"]))
        except (InvalidVersion, InvalidSpecifier):
            return []
        result: list[dict[str, Any]] = []
        for release in releases:
            if (
                release.get("publisher_id") != subscription["publisher_id"]
                or release.get("app_id") != subscription["app_id"]
                or release.get("status") != "published"
            ):
                continue
            try:
                candidate = Version(str(release.get("version") or ""))
            except InvalidVersion:
                continue
            if candidate <= current or candidate not in constraint:
                continue
            if subscription["channel"] == "stable" and candidate.is_prerelease:
                continue
            result.append(dict(release))
        return result

    @staticmethod
    def _decision_affects_subscription(
        payload: dict[str, Any], subscription: dict[str, Any]
    ) -> bool:
        subject = dict(payload.get("subject") or {})
        if subject.get("type") == "publisher_key":
            return str(subject.get("id") or "").startswith(
                f"{subscription['publisher_id']}:"
            )
        details = dict(payload.get("details") or {})
        if details.get("publisher_id") == subscription["publisher_id"]:
            return True
        return subject.get("type") == "app_release" and subject.get("id") in {
            subscription.get("current_release_id"),
            subscription.get("latest_release", {}).get("release_id"),
        }
