"""Sandbox isolation: Docker backend, registry, and domain service."""

from joyhousebot.sandbox.docker_backend import (
    is_docker_available,
    list_containers,
    remove_container,
    run_in_container,
)
from joyhousebot.sandbox.registry import read_registry, update_registry_after_remove, write_registry
from joyhousebot.sandbox.service import explain_local, list_containers_local, recreate_containers_local

__all__ = [
    "explain_local",
    "is_docker_available",
    "list_containers",
    "list_containers_local",
    "read_registry",
    "recreate_containers_local",
    "remove_container",
    "run_in_container",
    "update_registry_after_remove",
    "write_registry",
]
