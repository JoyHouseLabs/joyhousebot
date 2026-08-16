#!/usr/bin/env python3
"""Validate the exact Node LTS distribution selected for bundled Hosts."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "hosts" / "node" / "runtime-lock.json"
OPENCLI_RUNTIME_LOCK = (
    ROOT / "extensions" / "capability-opencli" / "catalog" / "runtime-lock.json"
)
OPENCLI_PACKAGE_LOCK = ROOT / "extensions" / "capability-opencli" / "package-lock.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def load_lock(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1 or value.get("distribution") != "node":
        raise ValueError("Node runtime lock schema is unsupported")
    version = str(value.get("version") or "")
    release_line = int(value.get("release_line") or 0)
    if version != "v24.19.0" or release_line != 24:
        raise ValueError("bundled Node runtime must use the reviewed v24.19.0 LTS release")
    if value.get("release_status") != "lts":
        raise ValueError("bundled Node runtime must be an LTS release")
    artifacts = value.get("artifacts")
    required = {"darwin-arm64", "darwin-x64", "linux-arm64", "linux-x64", "win32-x64"}
    if not isinstance(artifacts, dict) or set(artifacts) != required:
        raise ValueError("Node runtime lock must cover every supported platform")
    for platform, artifact in artifacts.items():
        if not isinstance(artifact, dict):
            raise ValueError(f"Node runtime artifact {platform} is invalid")
        filename = str(artifact.get("filename") or "")
        digest = str(artifact.get("sha256") or "")
        if version.removeprefix("v") not in filename or not SHA256.fullmatch(digest):
            raise ValueError(f"Node runtime artifact {platform} is not exactly pinned")
    return value


def verify_archive(lock: dict, platform: str, archive: Path) -> None:
    artifact = lock["artifacts"].get(platform)
    if artifact is None:
        raise ValueError(f"unsupported Node runtime platform: {platform}")
    if archive.name != artifact["filename"]:
        raise ValueError(f"Node runtime archive must be named {artifact['filename']}")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    if digest != artifact["sha256"]:
        raise ValueError("Node runtime archive SHA-256 does not match runtime-lock.json")


def verify_opencli_lock(node_lock: dict) -> None:
    value = json.loads(OPENCLI_RUNTIME_LOCK.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1:
        raise ValueError("OpenCLI runtime lock schema is unsupported")
    node = value.get("node")
    package = value.get("opencli")
    if not isinstance(node, dict) or node.get("version") != node_lock["version"]:
        raise ValueError("OpenCLI Extension must use the bundled exact Node runtime")
    if not isinstance(package, dict) or package.get("version") != "1.8.6":
        raise ValueError("OpenCLI Extension must use the reviewed 1.8.6 release")
    integrity = str(package.get("npm_integrity") or "")
    entrypoint_digest = str(package.get("entrypoint_sha256") or "")
    manifest_digest = str(package.get("upstream_manifest_sha256") or "")
    if (
        not integrity.startswith("sha512-")
        or not SHA256.fullmatch(entrypoint_digest)
        or not SHA256.fullmatch(manifest_digest)
    ):
        raise ValueError("OpenCLI package or manifest integrity is not pinned")
    package_lock = json.loads(OPENCLI_PACKAGE_LOCK.read_text(encoding="utf-8"))
    installed = package_lock.get("packages", {}).get("node_modules/@jackwener/opencli", {})
    root = package_lock.get("packages", {}).get("", {})
    if (
        installed.get("version") != package["version"]
        or installed.get("integrity") != integrity
        or root.get("dependencies", {}).get("@jackwener/opencli") != package["version"]
    ):
        raise ValueError("OpenCLI package-lock does not match runtime-lock.json")
    catalog = json.loads(
        (OPENCLI_RUNTIME_LOCK.parent / "catalog.json").read_text(encoding="utf-8")
    )
    runtime = catalog.get("runtime", {})
    if (
        runtime.get("node_version") != node_lock["version"]
        or runtime.get("opencli_version") != package["version"]
        or runtime.get("opencli_package_integrity") != integrity
        or runtime.get("opencli_entrypoint_sha256") != entrypoint_digest
        or runtime.get("upstream_manifest_sha256") != manifest_digest
    ):
        raise ValueError("compiled OpenCLI catalog does not match runtime-lock.json")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument(
        "--platform",
        choices=(
            "darwin-arm64",
            "darwin-x64",
            "linux-arm64",
            "linux-x64",
            "win32-x64",
        ),
    )
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    lock = load_lock(args.lock)
    if args.lock.resolve() == DEFAULT_LOCK.resolve():
        verify_opencli_lock(lock)
    if bool(args.platform) != bool(args.archive):
        parser.error("--platform and --archive must be supplied together")
    if args.archive:
        verify_archive(lock, args.platform, args.archive)
    suffix = "; OpenCLI 1.8.6 verified" if args.lock.resolve() == DEFAULT_LOCK.resolve() else ""
    print(f"Node runtime lock verified: {lock['version']} ({lock['release_status']}){suffix}")


if __name__ == "__main__":
    main()
