"""Data update coordinator for HGSmart Pet Feeder."""
from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HGSmartApiClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class HGSmartDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching HGSmart data."""

    def __init__(self, hass: HomeAssistant, api: HGSmartApiClient, update_interval: int) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=update_interval),
        )
        self.api = api

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API."""
        try:
            devices = await self.api.get_devices()
            
            if not devices:
                raise UpdateFailed("No devices found or API error")

            # Fetch stats for each device
            device_data = {}
            for device in devices:
                device_id = device["deviceId"]
                stats = await self.api.get_feeder_stats(device_id)
                attributes = await self.api.get_device_attributes(device_id)
                
                device_data[device_id] = {
                    "device_info": device,
                    "stats": stats or {},
                }

            return device_data

        except Exception as err:
            _LOGGER.exception("Error fetching data: %s", err)
            raise UpdateFailed(f"Error communicating with API: {err}") from err
