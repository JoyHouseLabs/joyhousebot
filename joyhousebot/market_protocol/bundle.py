"""Safe, content-addressed ``.joyhousebot-app`` bundle creation and verification."""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from joyhousebot.domain.app_packages import normalize_app_manifest
from joyhousebot.market_protocol.canonical import (
    bytes_sha256,
    canonical_json,
    parse_strict_json,
)
from joyhousebot.market_protocol.dsse import sign_dsse, verify_dsse
from joyhousebot.market_protocol.release import (
    APP_MANIFEST_MEDIA_TYPE,
    APP_RELEASE_MEDIA_TYPE,
    normalize_market_id,
    normalize_publisher_id,
    normalize_release_descriptor,
    utc_now_text,
)

_MAX_FILES = 520
_MAX_FILE_SIZE = 64 * 1024 * 1024
_MAX_TOTAL_SIZE = 256 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class AppBundle:
    descriptor: dict[str, Any]
    manifest: dict[str, Any]
    envelope: dict[str, Any]
    signer_key_id: str
    components: dict[str, bytes]


def _component_path(kind: str, logical_id: str, version: str) -> str:
    safe = logical_id.replace("/", "_").replace("\\", "_")
    return f"components/{kind}/{safe}/{version}.json"


def build_app_bundle(
    destination: Path,
    *,
    manifest: dict[str, Any],
    private_key: str,
    market_id: str,
    publisher_id: str,
    components: Mapping[tuple[str, str, str], bytes | dict[str, Any]] | None = None,
    released_at: str | None = None,
) -> AppBundle:
    normalized_manifest = normalize_app_manifest(manifest)
    if int(normalized_manifest["schema_version"]) != 2:
        raise ValueError("Market bundles require App Manifest schema_version 2")
    if normalized_manifest["publisher_id"] != normalize_publisher_id(publisher_id):
        raise ValueError("manifest publisher_id does not match bundle publisher_id")
    manifest_bytes = canonical_json(normalized_manifest)
    component_payloads: dict[str, bytes] = {}
    component_descriptors: list[dict[str, Any]] = []
    for identity, raw in (components or {}).items():
        kind, logical_id, version = identity
        payload = canonical_json(raw) if isinstance(raw, dict) else bytes(raw)
        if len(payload) > _MAX_FILE_SIZE:
            raise ValueError(f"component is too large: {logical_id}")
        path = _component_path(kind, logical_id, version)
        component_payloads[path] = payload
        component_descriptors.append(
            {
                "kind": kind,
                "logical_id": logical_id,
                "version": version,
                "media_type": f"application/vnd.joyhousebot.{kind}.v1+json",
                "digest": bytes_sha256(payload),
                "size": len(payload),
            }
        )
    descriptor = normalize_release_descriptor(
        {
            "schema_version": "1.0",
            "source": {
                "market_id": normalize_market_id(market_id),
                "publisher_id": normalize_publisher_id(publisher_id),
                "app_id": normalized_manifest["app_id"],
            },
            "version": normalized_manifest["version"],
            "released_at": released_at or utc_now_text(),
            "app_manifest": {
                "media_type": APP_MANIFEST_MEDIA_TYPE,
                "digest": bytes_sha256(manifest_bytes),
                "size": len(manifest_bytes),
            },
            "components": component_descriptors,
            "compatibility": {
                "core": dict(normalized_manifest["core"]),
                "platforms": ["any"],
                "architectures": ["any"],
            },
            "licenses": dict(normalized_manifest.get("licenses") or {}),
            "evidence": dict(normalized_manifest.get("evidence") or {}),
        }
    )
    descriptor_bytes = canonical_json(descriptor)
    envelope = sign_dsse(
        descriptor_bytes,
        payload_type=APP_RELEASE_MEDIA_TYPE,
        private_key=private_key,
    )
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("release.dsse.json", canonical_json(envelope))
        archive.writestr("joyhousebot.app.json", manifest_bytes)
        for path, payload in sorted(component_payloads.items()):
            archive.writestr(path, payload)
    key_id = str(envelope["signatures"][0]["keyid"])
    return AppBundle(
        descriptor=descriptor,
        manifest=normalized_manifest,
        envelope=envelope,
        signer_key_id=key_id,
        components=component_payloads,
    )


def _safe_members(archive: zipfile.ZipFile) -> dict[str, bytes]:
    infos = archive.infolist()
    if len(infos) > _MAX_FILES:
        raise ValueError("App bundle contains too many files")
    total = 0
    result: dict[str, bytes] = {}
    for info in infos:
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts or info.is_dir():
            raise ValueError("App bundle contains an unsafe path")
        if info.file_size < 0 or info.file_size > _MAX_FILE_SIZE:
            raise ValueError("App bundle member exceeds the size limit")
        total += info.file_size
        if total > _MAX_TOTAL_SIZE:
            raise ValueError("App bundle exceeds the total size limit")
        if info.filename in result:
            raise ValueError("App bundle contains a duplicate path")
        result[info.filename] = archive.read(info)
    return result


def load_app_bundle(path: Path) -> tuple[dict[str, bytes], dict[str, Any]]:
    try:
        with zipfile.ZipFile(Path(path), "r") as archive:
            files = _safe_members(archive)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("invalid App bundle archive") from exc
    if "release.dsse.json" not in files or "joyhousebot.app.json" not in files:
        raise ValueError("App bundle is missing its release envelope or manifest")
    envelope = parse_strict_json(files["release.dsse.json"])
    if not isinstance(envelope, dict):
        raise ValueError("App bundle release envelope must be an object")
    return files, envelope


def verify_app_bundle(
    path: Path,
    *,
    public_keys: Mapping[str, str | bytes],
    expected_market_id: str | None = None,
    expected_publisher_id: str | None = None,
) -> AppBundle:
    files, envelope = load_app_bundle(path)
    descriptor_bytes, signer_key_id = verify_dsse(
        envelope,
        public_keys=public_keys,
        expected_payload_type=APP_RELEASE_MEDIA_TYPE,
    )
    descriptor_raw = parse_strict_json(descriptor_bytes)
    if not isinstance(descriptor_raw, dict):
        raise ValueError("App release descriptor must be an object")
    descriptor = normalize_release_descriptor(descriptor_raw)
    source = descriptor["source"]
    if expected_market_id and source["market_id"] != normalize_market_id(expected_market_id):
        raise ValueError("App bundle belongs to a different Market")
    if expected_publisher_id and source["publisher_id"] != normalize_publisher_id(
        expected_publisher_id
    ):
        raise ValueError("App bundle belongs to a different publisher")
    manifest_bytes = files["joyhousebot.app.json"]
    manifest_reference = descriptor["app_manifest"]
    if len(manifest_bytes) != manifest_reference["size"]:
        raise ValueError("App manifest size does not match the release descriptor")
    if bytes_sha256(manifest_bytes) != manifest_reference["digest"]:
        raise ValueError("App manifest digest does not match the release descriptor")
    manifest_raw = parse_strict_json(manifest_bytes)
    if not isinstance(manifest_raw, dict):
        raise ValueError("App manifest must be an object")
    manifest = normalize_app_manifest(manifest_raw)
    if canonical_json(manifest) != manifest_bytes:
        raise ValueError("App manifest bytes are not RFC 8785 canonical")
    if manifest["app_id"] != source["app_id"] or manifest["version"] != descriptor["version"]:
        raise ValueError("App manifest identity does not match its release descriptor")
    if manifest["publisher_id"] != source["publisher_id"]:
        raise ValueError("App manifest publisher does not match its release descriptor")
    component_payloads: dict[str, bytes] = {}
    for component in descriptor["components"]:
        path_name = _component_path(
            component["kind"], component["logical_id"], component["version"]
        )
        payload = files.get(path_name)
        if payload is None:
            raise ValueError(f"App bundle component is missing: {path_name}")
        if len(payload) != component["size"] or bytes_sha256(payload) != component["digest"]:
            raise ValueError(f"App bundle component digest mismatch: {path_name}")
        component_value = parse_strict_json(payload)
        if not isinstance(component_value, dict) or canonical_json(component_value) != payload:
            raise ValueError(
                f"App bundle component is not a canonical JSON object: {path_name}"
            )
        component_payloads[path_name] = payload
    allowed = {"release.dsse.json", "joyhousebot.app.json", *component_payloads}
    extra = set(files) - allowed
    if extra:
        raise ValueError(f"App bundle contains undeclared files: {sorted(extra)}")
    return AppBundle(
        descriptor=descriptor,
        manifest=manifest,
        envelope=dict(envelope),
        signer_key_id=signer_key_id,
        components=component_payloads,
    )
