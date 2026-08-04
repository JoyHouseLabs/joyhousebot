"""Shared PostgreSQL test fixtures."""

from __future__ import annotations

import pytest

from tests.support.postgres_store import TEST_DATABASE_URL


@pytest.fixture(scope="session")
def postgres_database_url() -> str:
    return TEST_DATABASE_URL
