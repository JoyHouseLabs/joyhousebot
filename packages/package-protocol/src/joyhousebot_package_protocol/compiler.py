"""Deterministic compiler for source-first joyhousebot Apps."""

from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Any

SOURCE_DIRECTORIES = (
    "agents",
    "teams",
    "workflows",
    "skills",
    "schemas",
    "prompts",
    "evals",
)
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class CompiledApp:
    root: Path
    manifest: dict[str, Any]
    lock: dict[str, Any]

    def write(self) -> Path:
        destination = self.root / ".joyhousebot" / "app.lock.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(self.lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return destination


def compile_app(root: str | Path) -> CompiledApp:
    root = Path(root).expanduser().resolve()
    manifest_path = root / "joyhousebot.app.toml"
    if not manifest_path.is_file():
        raise ValueError(f"missing App manifest: {manifest_path}")
    with manifest_path.open("rb") as stream:
        source = tomllib.load(stream)
    manifest = _manifest(source)
    files = _source_files(root)
    components = [_component(root, path) for path in files]
    revisions = _revisions(components)
    graph = _dependency_graph(manifest, components)
    projection = {
        "schema_version": 1,
        "app": manifest,
        "components": components,
        "revisions": revisions,
        "dependency_graph": graph,
        "permission_snapshot": sorted(set(manifest["permissions"])),
        "entrypoints": manifest["entrypoints"],
    }
    lock = {
        **projection,
        "manifest_digest": _digest(manifest),
        "lock_digest": _digest(projection),
        "generated": True,
        "editable": False,
    }
    return CompiledApp(root=root, manifest=manifest, lock=lock)


def validate_app(root: str | Path) -> dict[str, Any]:
    compiled = compile_app(root)
    return {
        "valid": True,
        "app_id": compiled.manifest["app_id"],
        "version": compiled.manifest["version"],
        "components": len(compiled.lock["components"]),
        "entrypoints": len(compiled.lock["entrypoints"]),
        "permissions": compiled.lock["permission_snapshot"],
        "lock_digest": compiled.lock["lock_digest"],
    }


def _manifest(source: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "schema_version",
        "app",
        "runtime",
        "permissions",
        "entrypoints",
        "extensions",
        "configuration",
        "metadata",
    }
    unknown = set(source) - allowed
    if unknown:
        raise ValueError(f"unsupported top-level App fields: {sorted(unknown)}")
    app = _object(source.get("app"), "app")
    app_id = _stable(app.get("id"), "app.id")
    if not app_id.startswith("app."):
        raise ValueError("app.id must start with 'app.'")
    version = str(app.get("version") or "").strip()
    name = str(app.get("name") or "").strip()
    if not version or len(version) > 64 or not name or len(name) > 160:
        raise ValueError("app.name and app.version are required")
    permissions = _strings(source.get("permissions"), "permissions")
    entrypoints = _entrypoints(source.get("entrypoints"), permissions)
    runtime = _object(source.get("runtime") or {}, "runtime")
    result = {
        "schema_version": int(source.get("schema_version") or 1),
        "app_id": app_id,
        "version": version,
        "name": name,
        "description": str(app.get("description") or ""),
        "publisher": str(app.get("publisher") or ""),
        "runtime": {
            "min_version": str(runtime.get("min_version") or ""),
            "max_version": str(runtime.get("max_version") or ""),
        },
        "permissions": permissions,
        "entrypoints": entrypoints,
        "extensions": _objects(source.get("extensions"), "extensions"),
        "configuration_schema": _object(source.get("configuration") or {}, "configuration"),
        "metadata": _object(source.get("metadata") or {}, "metadata"),
    }
    if result["schema_version"] != 1:
        raise ValueError("unsupported joyhousebot.app.toml schema_version")
    return result


def _entrypoints(value: Any, permissions: list[str]) -> list[dict[str, Any]]:
    rows = _objects(value, "entrypoints")
    if not rows:
        raise ValueError("an App must declare at least one EntryPoint")
    if "runs.submit" not in permissions:
        raise ValueError("EntryPoints require the runs.submit permission")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        entrypoint_id = _stable(row.get("id"), "entrypoints.id")
        if entrypoint_id in seen:
            raise ValueError(f"duplicate EntryPoint: {entrypoint_id}")
        seen.add(entrypoint_id)
        implementation = _object(row.get("implementation"), "entrypoints.implementation")
        kind = str(implementation.get("kind") or "")
        ref = str(implementation.get("ref") or "")
        if kind not in {"agent", "team", "workflow", "scenario"} or not ref:
            raise ValueError(f"EntryPoint {entrypoint_id} has an invalid implementation")
        result.append(
            {
                "id": entrypoint_id,
                "name": str(row.get("name") or entrypoint_id),
                "description": str(row.get("description") or ""),
                "default": bool(row.get("default", len(rows) == 1)),
                "interaction_mode": str(row.get("interaction_mode") or "auto"),
                "implementation": {"kind": kind, "ref": ref},
                "input_schema": str(row.get("input_schema") or ""),
                "output_schema": str(row.get("output_schema") or ""),
            }
        )
    if sum(bool(item["default"]) for item in result) != 1:
        raise ValueError("exactly one EntryPoint must be default")
    return sorted(result, key=lambda item: item["id"])


def _source_files(root: Path) -> list[Path]:
    source_root = root / "joyhousebot"
    paths: list[Path] = []
    for directory in SOURCE_DIRECTORIES:
        base = source_root / directory
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"App source cannot contain symlinks: {path}")
            if path.is_file() and path.suffix in {".json", ".md", ".txt", ".toml"}:
                paths.append(path)
    return sorted(paths, key=lambda item: item.relative_to(root).as_posix())


def _component(root: Path, path: Path) -> dict[str, Any]:
    relative = PurePosixPath(path.relative_to(root).as_posix())
    data = path.read_bytes()
    kind = relative.parts[1]
    component: dict[str, Any] = {
        "kind": kind[:-1] if kind.endswith("s") else kind,
        "path": relative.as_posix(),
        "digest": f"sha256:{sha256(data).hexdigest()}",
        "size": len(data),
    }
    if path.suffix in {".json", ".toml"}:
        value = json.loads(data) if path.suffix == ".json" else tomllib.loads(data.decode())
        if not isinstance(value, dict):
            raise ValueError(f"structured component must be an object: {relative}")
        component["document"] = value
    return component


def _revisions(components: list[dict[str, Any]]) -> list[dict[str, str]]:
    result = []
    for item in components:
        document = item.get("document") or {}
        logical_id = str(document.get("id") or document.get(f"{item['kind']}_id") or "")
        revision = str(document.get("revision_id") or document.get("version") or "")
        if logical_id:
            result.append(
                {
                    "kind": item["kind"],
                    "id": logical_id,
                    "revision": revision,
                    "digest": item["digest"],
                }
            )
    return sorted(result, key=lambda item: (item["kind"], item["id"], item["revision"]))


def _dependency_graph(
    manifest: dict[str, Any], components: list[dict[str, Any]]
) -> dict[str, list[str]]:
    known = {item["path"] for item in components}
    graph: dict[str, list[str]] = {}
    for entrypoint in manifest["entrypoints"]:
        dependencies = [entrypoint["implementation"]["ref"]]
        dependencies.extend(
            path for path in (entrypoint["input_schema"], entrypoint["output_schema"]) if path
        )
        missing = [
            path
            for path in dependencies
            if f"joyhousebot/{path}" not in known and path not in known
        ]
        if missing:
            raise ValueError(f"EntryPoint {entrypoint['id']} references missing sources: {missing}")
        graph[f"entrypoint:{entrypoint['id']}"] = sorted(dependencies)
    return graph


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{sha256(payload).hexdigest()}"


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a table")
    return dict(value)


def _objects(value: Any, field: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"{field} must be an array of tables")
    return [dict(item) for item in value]


def _strings(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{field} must be a non-empty string array")
    return sorted(set(value))


def _stable(value: Any, field: str) -> str:
    result = str(value or "").strip()
    if not _ID.fullmatch(result):
        raise ValueError(f"{field} must be a stable identifier")
    return result


__all__ = ["CompiledApp", "compile_app", "validate_app"]
