"""Data update coordinator for HGSmart Pet Feeder."""
from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HGSmartApiClient
from .const import DOMAIN, SCHEDULE_SLOTS
from .helpers import parse_plan_value

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

            # Filter to only S25D devices (only tested model)
            supported_devices = []
            for device in devices:
                device_type = device.get("type", "")
                if device_type == "S25D":
                    supported_devices.append(device)
                else:
                    _LOGGER.warning(
                        "Skipping unsupported device model '%s' (name: %s, id: %s). "
                        "Only S25D model is currently supported.",
                        device_type,
                        device.get("name", "Unknown"),
                        device.get("deviceId", "Unknown"),
                    )

            if not supported_devices:
                raise UpdateFailed("No supported S25D devices found")

            # Fetch stats for each device
            device_data = {}
            for device in supported_devices:
                device_id = device["deviceId"]
                stats = await self.api.get_feeder_stats(device_id)
                attributes = await self.api.get_device_attributes(device_id)

                # Parse schedule slots from attributes
                schedules = {}
                if attributes:
                    for slot in range(SCHEDULE_SLOTS):  # Slots 0-5
                        plan_key = f"plan{slot}"
                        plan_value = attributes.get(plan_key, "0")
                        parsed = parse_plan_value(str(plan_value))

                        if parsed:
                            schedules[slot] = parsed
                        else:
                            # Default disabled slot
                            schedules[slot] = {
                                "enabled": False,
                                "hour": 8,  # Default 8:00 UTC
                                "minute": 0,
                                "portions": 1,
                                "slot": slot,
                            }

                device_data[device_id] = {
                    "device_info": device,
                    "stats": stats or {},
                    "attributes": attributes or {},
                    "schedules": schedules,
                }

            return device_data

        except Exception as err:
            _LOGGER.exception("Error fetching data: %s", err)
            raise UpdateFailed(f"Error communicating with API: {err}") from err
