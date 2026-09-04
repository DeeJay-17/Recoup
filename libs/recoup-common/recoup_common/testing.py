"""Helpers for tests across services."""

from __future__ import annotations

import os

import pytest

integration = pytest.mark.integration


def test_database_url() -> str | None:
    return os.environ.get("TEST_DATABASE_URL")


def requires_db() -> pytest.MarkDecorator:
    return pytest.mark.skipif(
        test_database_url() is None, reason="TEST_DATABASE_URL not set; integration test skipped"
    )
