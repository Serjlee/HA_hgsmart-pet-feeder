"""Shared fixtures for the HGSmart tests."""

from typing import Any

import pytest

from custom_components.hgsmart.const import DOMAIN

from .common import FakeCloud, add_entry


@pytest.fixture
def cloud(monkeypatch) -> FakeCloud:
    cloud = FakeCloud()
    factory = lambda *args, **kwargs: cloud.api  # noqa: E731
    monkeypatch.setattr("custom_components.hgsmart.HGSmartApiClient", factory)
    monkeypatch.setattr("custom_components.hgsmart.config_flow.HGSmartApiClient", factory)
    return cloud


@pytest.fixture
def setup_integration(hass, cloud):
    """Return a coroutine that sets up the integration and its coordinator."""

    async def _setup(options: dict[str, Any] | None = None):
        entry = add_entry(hass, options=options)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return hass.data[DOMAIN][entry.entry_id]["coordinator"]

    return _setup
