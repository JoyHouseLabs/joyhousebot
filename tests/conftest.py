"""Pytest hooks and fixtures."""

import os

import pytest


def pytest_configure(config):
    """Register custom markers (also in pyproject.toml)."""
    config.addinivalue_line(
        "markers",
        "requires_pairing: requires device pairing / full runtime (skipped in CI)",
    )


def pytest_collection_modifyitems(config, items):
    """Skip requires_pairing tests when running in CI (no device paired)."""
    if os.environ.get("CI") != "true":
        return
    skip = pytest.mark.skip(reason="Requires device pairing (skipped in CI)")
    for item in items:
        if "requires_pairing" in item.keywords:
            item.add_marker(skip)
