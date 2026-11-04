"""Data update coordinator for HGSmart Pet Feeder."""
import asyncio
from collections.abc import Callable
import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HGSmartApiClient, HGSmartAuthError
from .const import DOMAIN
from .helpers import (
    get_schedule_slot_count,
    has_weekday_mask,
    is_sf_model,
    is_supported_model,
    local_days_to_mask,
    parse_plan_value,
)

if TYPE_CHECKING:
    from .schedule_offsets import HGSmartScheduleOffsets

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
        self.schedule_offsets: HGSmartScheduleOffsets | None = None
        self._schedule_locks: dict[tuple[str, int], asyncio.Lock] = {}
        self._warned_unsupported: set[str] = set()

    def get_schedule_lock(self, device_id: str, slot: int) -> asyncio.Lock:
        """Return a per-slot lock, creating it lazily."""
        key = (device_id, slot)
        if key not in self._schedule_locks:
            self._schedule_locks[key] = asyncio.Lock()
        return self._schedule_locks[key]

    async def async_update_schedule(
        self,
        device_id: str,
        slot: int,
        update: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        record_time_offset: bool = False,
    ) -> None:
        """Apply update(schedule) optimistically and write the slot to the device.

        update runs under the slot lock so it sees the latest schedule. The
        change is rolled back if the write fails.
        """
        async with self.get_schedule_lock(device_id, slot):
            device_data = (self.data or {}).get(device_id)
            if not device_data:
                raise HomeAssistantError(f"Device {device_id} is not loaded")
            name = device_data["device_info"].get("name", device_id)
            schedule = device_data["schedules"].get(slot)
            if not schedule:
                raise HomeAssistantError(f"{name} has no schedule {slot + 1}")

            old = dict(schedule)
            schedule.update(update(schedule))
            self.async_update_listeners()
            try:
                success = await self.api.set_schedule(
                    device_id,
                    slot,
                    schedule["hour"],
                    schedule["minute"],
                    schedule["portions"],
                    schedule["enabled"],
                    suffix=schedule["suffix"],
                )
                if not success:
                    raise HomeAssistantError(
                        f"Failed to update schedule {slot + 1} on {name}"
                    )
                if record_time_offset and self.schedule_offsets is not None:
                    await self.schedule_offsets.async_record_time(device_id, slot)
            except Exception:
                schedule.update(old)
                self.async_update_listeners()
                raise

    async def async_set_schedule_days(
        self, device_id: str, slot: int, days: list[str]
    ) -> None:
        """Set the local weekdays a schedule slot runs on (SF35 weekday mask)."""

        def update(schedule: dict[str, Any]) -> dict[str, Any]:
            if not has_weekday_mask(schedule["suffix"]):
                raise HomeAssistantError(
                    f"Schedule {slot + 1} does not support weekly schedules"
                )
            return {
                "suffix": local_days_to_mask(days, schedule["hour"], schedule["minute"])
            }

        await self.async_update_schedule(device_id, slot, update)
        await self.async_request_refresh()

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
                slot_count = get_schedule_slot_count(device, attributes)
                if attributes:
                    parsed_plans = {
                        slot: parse_plan_value(str(attributes.get(f"plan{slot}", "0")))
                        for slot in range(slot_count)
                    }
                    # Empty slots on weekly-schedule models need a mask to be
                    # written back; default to every day
                    empty_suffix = (
                        "_1111111"
                        if is_sf_model(device)
                        or any(
                            p and has_weekday_mask(p["suffix"])
                            for p in parsed_plans.values()
                        )
                        else ""
                    )
                    for slot, parsed in parsed_plans.items():
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
                                "suffix": empty_suffix,
                            }

                device_data[device_id] = {
                    "device_info": device,
                    "stats": stats or {},
                    "attributes": attributes or {},
                    "schedules": schedules,
                    "schedule_slots": slot_count,
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
