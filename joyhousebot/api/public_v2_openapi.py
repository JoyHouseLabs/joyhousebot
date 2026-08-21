"""Extract the self-contained public v2 contract from the combined OpenAPI document."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def public_v2_openapi_document(document: dict[str, Any]) -> dict[str, Any]:
    paths = {
        path: deepcopy(value)
        for path, value in document.get("paths", {}).items()
        if path == "/v2" or path.startswith("/v2/")
    }
    schemas = dict(document.get("components", {}).get("schemas", {}))
    selected: dict[str, Any] = {}
    pending = _schema_references(paths)
    while pending:
        name = pending.pop()
        if name in selected or name not in schemas:
            continue
        selected[name] = deepcopy(schemas[name])
        pending.update(_schema_references(selected[name]))
    return {
        "openapi": document.get("openapi", "3.1.0"),
        "info": {
            "title": "joyhousebot Public Execution API",
            "version": "2.0.0-experimental",
            "description": "Owner and Installation EntryPoint execution contract.",
        },
        "paths": paths,
        "components": {"schemas": {name: selected[name] for name in sorted(selected)}},
    }


def _schema_references(value: Any) -> set[str]:
    references: set[str] = set()
    if isinstance(value, dict):
        reference = value.get("$ref")
        prefix = "#/components/schemas/"
        if isinstance(reference, str) and reference.startswith(prefix):
            references.add(reference.removeprefix(prefix))
        for item in value.values():
            references.update(_schema_references(item))
    elif isinstance(value, list):
        for item in value:
            references.update(_schema_references(item))
    return references


__all__ = ["public_v2_openapi_document"]
