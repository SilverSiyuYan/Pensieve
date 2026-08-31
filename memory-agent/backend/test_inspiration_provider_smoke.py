"""Opt-in real search provider smoke test; never runs in normal CI/test runs."""

import os

import pytest

from search_service import create_search_provider


@pytest.mark.skipif(
    os.getenv("RUN_REAL_SEARCH_SMOKE") != "1",
    reason="Set RUN_REAL_SEARCH_SMOKE=1 to call the configured real search provider",
)
def test_configured_search_provider_smoke() -> None:
    provider = create_search_provider()
    results = provider.search("SQLite embedded database documentation", 5)
    assert results
    assert all(item.title and item.url and item.snippet for item in results)
    assert all(item.url.startswith(("http://", "https://")) for item in results)
