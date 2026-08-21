"""Source-first App lifecycle CLI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import typer

from joyhousebot_package_protocol.compiler import compile_app, validate_app

app = typer.Typer(help="Validate, build, evaluate, publish and install a joyhousebot App")


@app.command()
def validate(root: Path = typer.Argument(Path.cwd(), exists=True, file_okay=False)) -> None:
    """Validate source and all local references without touching a Runtime."""
    typer.echo(json.dumps(validate_app(root), ensure_ascii=False, indent=2))


@app.command()
def build(root: Path = typer.Argument(Path.cwd(), exists=True, file_okay=False)) -> None:
    """Compile deterministic `.joyhousebot/app.lock.json`."""
    compiled = compile_app(root)
    path = compiled.write()
    typer.echo(json.dumps({"lock": str(path), "digest": compiled.lock["lock_digest"]}))


@app.command("eval")
def evaluate(root: Path = typer.Argument(Path.cwd(), exists=True, file_okay=False)) -> None:
    """Run deterministic local eval declaration checks."""
    compiled = compile_app(root)
    evals = [item for item in compiled.lock["components"] if item["kind"] == "eval"]
    invalid = [item["path"] for item in evals if not isinstance(item.get("document"), dict)]
    report = {"passed": not invalid, "suites": len(evals), "invalid": invalid}
    typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
    if invalid:
        raise typer.Exit(1)


@app.command()
def publish(
    root: Path = typer.Argument(Path.cwd(), exists=True, file_okay=False),
    runtime_url: str = typer.Option("", envvar="JOYHOUSEBOT_URL"),
    token: str = typer.Option("", envvar="JOYHOUSEBOT_OPERATOR_TOKEN", hidden=True),
) -> None:
    """Build, save, validate and publish one immutable release."""
    compiled = compile_app(root)
    compiled.write()
    app_id, version = compiled.manifest["app_id"], compiled.manifest["version"]
    if _release_already_published(compiled, runtime_url, token):
        typer.echo(
            json.dumps(
                {
                    "app_id": app_id,
                    "version": version,
                    "status": "published",
                    "unchanged": True,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    _publish_components(compiled, runtime_url, token)
    _request(
        "PUT",
        f"/control/v1/admin/apps/{app_id}/releases/{version}",
        runtime_url,
        token,
        {"manifest": _runtime_manifest(compiled)},
    )
    validation = _request(
        "POST", f"/control/v1/admin/apps/{app_id}/releases/{version}/validate", runtime_url, token
    )
    if not validation.get("valid"):
        raise typer.BadParameter(f"Runtime validation failed: {validation.get('errors')}")
    result = _request(
        "POST", f"/control/v1/admin/apps/{app_id}/releases/{version}/publish", runtime_url, token
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command()
def install(
    root: Path = typer.Argument(Path.cwd(), exists=True, file_okay=False),
    runtime_url: str = typer.Option("", envvar="JOYHOUSEBOT_URL"),
    token: str = typer.Option("", envvar="JOYHOUSEBOT_OPERATOR_TOKEN", hidden=True),
) -> None:
    """Install the compiled release with its exact permission snapshot."""
    compiled = compile_app(root)
    result = _request(
        "POST",
        f"/control/v1/admin/apps/{compiled.manifest['app_id']}/install",
        runtime_url,
        token,
        {
            "version": compiled.manifest["version"],
            "configuration": {},
            "granted_permissions": compiled.lock["permission_snapshot"],
        },
    )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command()
def dev(root: Path = typer.Argument(Path.cwd(), exists=True, file_okay=False)) -> None:
    """Validate and build once for a local Runtime/worker development loop."""
    compiled = compile_app(root)
    path = compiled.write()
    typer.echo(
        json.dumps(
            {"ready": True, "lock": str(path), "hint": "start the Runtime and worker normally"}
        )
    )


def _request(
    method: str, path: str, base_url: str, token: str, body: dict[str, Any] | None = None
) -> dict[str, Any]:
    if not base_url or not token:
        raise typer.BadParameter("JOYHOUSEBOT_URL and JOYHOUSEBOT_OPERATOR_TOKEN are required")
    headers = {"Authorization": f"Bearer {token}"}
    operator_user_id = str(os.getenv("JOYHOUSEBOT_OPERATOR_USER_ID") or "").strip()
    if operator_user_id:
        headers.update(
            {
                "X-Impersonate-User-ID": operator_user_id,
                "X-Impersonation-Reason": "Publish versioned App source package",
            }
        )
    response = httpx.request(
        method,
        f"{base_url.rstrip('/')}{path}",
        headers=headers,
        json=body,
        timeout=30,
    )
    if response.status_code >= 400:
        raise typer.BadParameter(f"Runtime HTTP {response.status_code}: {response.text[:500]}")
    return dict(response.json())


def _release_already_published(compiled: Any, runtime_url: str, token: str) -> bool:
    app_id = str(compiled.manifest["app_id"])
    version = str(compiled.manifest["version"])
    result = _request("GET", f"/control/v1/admin/apps/{app_id}/releases", runtime_url, token)
    existing = next(
        (item for item in result.get("items", []) if str(item.get("version")) == version),
        None,
    )
    if not existing or existing.get("status") != "published":
        return False
    metadata = dict((existing.get("manifest") or {}).get("metadata") or {})
    if metadata.get("source_lock_digest") != compiled.lock["lock_digest"]:
        raise typer.BadParameter(
            f"App {app_id} {version} is already published with different source"
        )
    return True


def _publish_components(compiled: Any, runtime_url: str, token: str) -> None:
    """Publish source-owned revisions before validating the App release."""
    components = {
        item["path"]: dict(item.get("document") or {})
        for item in compiled.lock["components"]
        if item.get("document")
    }
    ordered = ("skill", "agent", "team", "scenario")
    for kind in ordered:
        for revision in compiled.lock["revisions"]:
            if revision["kind"] != kind:
                continue
            document = next(
                value
                for value in components.values()
                if (value.get(f"{kind}_id") == revision["id"] or value.get("id") == revision["id"])
                and str(value.get("revision_id") or value.get("version") or "")
                == revision["revision"]
            )
            result = _publish_component(kind, document, runtime_url, token)
            if kind == "skill":
                revision["content_sha256"] = result["content_sha256"]


def _publish_component(
    kind: str, document: dict[str, Any], runtime_url: str, token: str
) -> dict[str, Any]:
    identity = str(document.get(f"{kind}_id") or document.get("id"))
    revision = str(document.get("revision_id") or document.get("version"))
    if kind == "agent":
        base = f"/control/v1/admin/agents/{identity}/revisions/{revision}"
        _request("PUT", base, runtime_url, token, document)
        _request("POST", f"{base}/publish", runtime_url, token)
        return {}
    if kind == "team":
        base = f"/control/v1/admin/teams/{identity}/revisions/{revision}"
        _request("PUT", base, runtime_url, token, document)
        _request("POST", f"{base}/publish", runtime_url, token)
        return {}
    if kind == "skill":
        base = f"/control/v1/admin/skills/{identity}/versions/{revision}"
        _request("PUT", base, runtime_url, token, document)
        validation = _request("POST", f"{base}/validate", runtime_url, token)
        _request("POST", f"{base}/publish", runtime_url, token)
        return validation
    if kind == "scenario":
        base = f"/control/v1/admin/scenarios/{identity}/versions/{revision}"
        _request("PUT", base, runtime_url, token, document)
        _request("POST", f"{base}/publish", runtime_url, token)
        return {}
    raise ValueError(f"unsupported publishable component kind: {kind}")


def _runtime_manifest(compiled: Any) -> dict[str, Any]:
    value = compiled.manifest
    entrypoints = []
    assets: dict[str, list[dict[str, str]]] = {
        "agents": [],
        "teams": [],
        "workflows": [],
        "scenarios": [],
        "skills": [],
    }
    for item in compiled.lock["revisions"]:
        plural = f"{item['kind']}s"
        if plural not in assets:
            continue
        key = "version" if item["kind"] in {"skill", "scenario"} else "revision_id"
        assets[plural].append(
            {
                f"{item['kind']}_id": item["id"],
                key: item["revision"],
                **(
                    {"content_sha256": item.get("content_sha256") or item["digest"]}
                    if item["kind"] == "skill"
                    else {}
                ),
            }
        )
    for item in value["entrypoints"]:
        kind, ref = item["implementation"]["kind"], item["implementation"]["ref"]
        source = next(
            (row for row in compiled.lock["components"] if row["path"].endswith(ref)), None
        )
        document = dict(source.get("document") or {}) if source else {}
        execution = {"mode": kind, f"{kind}_id": document.get(f"{kind}_id") or document.get("id")}
        execution["version" if kind == "scenario" else "revision_id"] = (
            document.get("version") if kind == "scenario" else document.get("revision_id")
        )
        entrypoints.append(
            {
                "entrypoint_id": item["id"],
                "name": item["name"],
                "description": item["description"],
                "default": item["default"],
                "execution": execution,
                "interaction_mode": item["interaction_mode"],
                "timeout_seconds": 300,
                "input_schema": _schema(compiled.root, item["input_schema"]),
                "output_schema": _schema(compiled.root, item["output_schema"])
                if item["output_schema"]
                else None,
                "verification_policy": {},
            }
        )
    return {
        "schema_version": 1,
        "app_id": value["app_id"],
        "version": value["version"],
        "name": value["name"],
        "description": value["description"],
        "publisher": value["publisher"],
        "core": value["runtime"],
        "extensions": value["extensions"],
        "capabilities": [],
        "assets": assets,
        "connections": [],
        "permissions": value["permissions"],
        "secrets": [],
        "triggers": [],
        "evaluations": [],
        "configuration_schema": value["configuration_schema"],
        "ui": {},
        "metadata": {**value["metadata"], "source_lock_digest": compiled.lock["lock_digest"]},
        "entrypoints": entrypoints,
    }


def _schema(root: Path, reference: str) -> dict[str, Any]:
    relative = reference if reference.startswith("joyhousebot/") else f"joyhousebot/{reference}"
    path = root / relative
    return dict(json.loads(path.read_text(encoding="utf-8")))


__all__ = ["app"]
