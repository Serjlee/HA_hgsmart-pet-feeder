"""Data update coordinator for HGSmart Pet Feeder."""
import asyncio
from datetime import timedelta
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HGSmartApiClient, HGSmartAuthError
from .const import DOMAIN, SCHEDULE_SLOTS
from .helpers import is_supported_model, parse_plan_value

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
        self._schedule_locks: dict[tuple[str, int], asyncio.Lock] = {}
        self._warned_unsupported: set[str] = set()

    def get_schedule_lock(self, device_id: str, slot: int) -> asyncio.Lock:
        """Return a per-slot lock, creating it lazily."""
        key = (device_id, slot)
        if key not in self._schedule_locks:
            self._schedule_locks[key] = asyncio.Lock()
        return self._schedule_locks[key]

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API."""
        try:
            devices = await self.api.get_devices()

            if not devices:
                raise UpdateFailed("No devices found or API error")

            # Fetch stats for each device
            device_data = {}
            for device in devices:
                device_id = device.get("deviceId")
                if not device_id:
                    continue
                # Untested models may omit fields entities rely on
                device["name"] = device.get("name") or f"HGSmart {device_id}"
                device["type"] = device.get("type") or "Unknown"
                supported = is_supported_model(device)
                if not supported and device_id not in self._warned_unsupported:
                    self._warned_unsupported.add(device_id)
                    _LOGGER.warning(
                        "Device model '%s' (name: %s, id: %s) has not been tested "
                        "with this integration; treating it as a generic feeder. "
                        "Some entities may not work.",
                        device["type"],
                        device["name"],
                        device_id,
                    )
                try:
                    stats = await self.api.get_feeder_stats(device_id)
                    attributes = await self.api.get_device_attributes(device_id)
                    today_events = await self.api.get_device_today_events(device_id)
                except HGSmartAuthError:
                    raise
                except Exception:  # pylint: disable=broad-except
                    if supported:
                        raise
                    # Don't let an untested model break the supported ones
                    _LOGGER.exception(
                        "Failed to fetch data for untested model '%s' (%s); skipping",
                        device["type"],
                        device_id,
                    )
                    continue
                if not isinstance(stats, dict):
                    stats = None
                if not isinstance(attributes, dict):
                    attributes = None

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
                    "today_events": today_events
                    or {"events": [], "total": 0},
                }

            if not device_data:
                raise UpdateFailed("No usable devices found")

            return device_data

        except HGSmartAuthError as err:
            raise ConfigEntryAuthFailed("Authentication failed, please reauthenticate") from err
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
