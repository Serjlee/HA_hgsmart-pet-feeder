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
    build_plan_value,
    get_schedule_slot_count,
    has_weekday_mask,
    is_plan_attribute,
    is_sf_model,
    is_supported_model,
    local_days_to_mask,
    parse_plan_value,
)

if TYPE_CHECKING:
    from .schedule_offsets import HGSmartScheduleOffsets

_LOGGER = logging.getLogger(__name__)


def _merge_schedule_snapshot(
    device_data: dict[str, Any], snapshot: dict[str, Any]
) -> None:
    """Use this snapshot's plans without overwriting unrelated attributes."""
    attributes = {
        key: value
        for key, value in device_data["attributes"].items()
        if not is_plan_attribute(key)
    }
    attributes.update(
        (key, value)
        for key, value in snapshot["attributes"].items()
        if is_plan_attribute(key)
    )
    device_data.update(
        attributes=attributes,
        schedules=snapshot["schedules"],
        schedule_slots=snapshot["schedule_slots"],
        schedule_attributes_available=snapshot["schedule_attributes_available"],
    )


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
        self._schedule_locks: dict[str, asyncio.Lock] = {}
        self._schedule_revisions: dict[str, int] = {}
        self._warned_unsupported: set[str] = set()

    def get_schedule_lock(self, device_id: str) -> asyncio.Lock:
        """Serialize schedule writes against the device's shared snapshot."""
        if device_id not in self._schedule_locks:
            self._schedule_locks[device_id] = asyncio.Lock()
        return self._schedule_locks[device_id]

    async def async_update_schedule(
        self,
        device_id: str,
        slot: int,
        update: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        time_offset: int | None = None,
    ) -> None:
        """Apply update(schedule) optimistically and write the slot to the device.

        update runs under the device lock so it sees the latest schedule. The
        change is rolled back if the write fails.
        """
        async with self.get_schedule_lock(device_id):
            device_data = (self.data or {}).get(device_id)
            if not device_data:
                raise HomeAssistantError(f"Device {device_id} is not loaded")
            name = device_data["device_info"].get("name", device_id)
            schedule = device_data["schedules"].get(slot)
            if not schedule:
                raise HomeAssistantError(f"{name} has no schedule {slot + 1}")

            old = dict(schedule)
            changes = update(schedule)
            schedule.update(changes)
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
            except Exception:
                schedule.update(old)
                self.async_update_listeners()
                raise

            # Keep the raw plan consistent with the optimistic schedule. A
            # concurrent poll may have replaced the containing device dict.
            self._advance_schedule_revision(device_id)
            if (current := self.data.get(device_id)) is not None:
                current["attributes"][f"plan{slot}"] = build_plan_value(
                    schedule["hour"],
                    schedule["minute"],
                    schedule["portions"],
                    slot,
                    schedule["enabled"],
                    schedule["suffix"],
                )
            if self.schedule_offsets is not None:
                if time_offset is not None:
                    await self.schedule_offsets.async_record_time(
                        device_id, slot, time_offset
                    )
                elif "suffix" in changes:
                    await self.schedule_offsets.async_record_weekdays(
                        device_id, slot, schedule
                    )

    async def async_set_schedule_days(
        self, device_id: str, slot: int, days: list[str]
    ) -> None:
        """Set the local weekdays a schedule slot runs on (SF35 weekday mask)."""

        def update(schedule: dict[str, Any]) -> dict[str, Any]:
            if not has_weekday_mask(schedule["suffix"]):
                raise HomeAssistantError(
                    f"Schedule {slot + 1} does not support weekly schedules"
                )
            offset = (
                self.schedule_offsets.offset_for_schedule(device_id, slot, schedule)
                if self.schedule_offsets is not None
                else None
            )
            return {
                "suffix": local_days_to_mask(
                    days, schedule["hour"], schedule["minute"], offset=offset
                )
            }

        await self.async_update_schedule(device_id, slot, update)
        await self.async_request_refresh()

    @staticmethod
    def _schedule_snapshot(
        device: dict[str, Any], attributes: dict[str, Any] | None
    ) -> dict[str, Any]:
        """Extract all slots from one device attributes response."""
        slot_count = get_schedule_slot_count(device, attributes)
        schedules = {}
        if attributes:
            parsed_plans = {
                slot: parse_plan_value(str(attributes.get(f"plan{slot}", "0")))
                for slot in range(slot_count)
            }
            # Empty slots on weekly models default to every day.
            empty_suffix = (
                "_1111111"
                if is_sf_model(device)
                or any(p and has_weekday_mask(p["suffix"]) for p in parsed_plans.values())
                else ""
            )
            for slot, parsed in parsed_plans.items():
                schedules[slot] = parsed or {
                    "enabled": False,
                    "hour": 8,
                    "minute": 0,
                    "portions": 1,
                    "slot": slot,
                    "suffix": empty_suffix,
                }
        return {
            "attributes": attributes or {},
            "schedules": schedules,
            "schedule_slots": slot_count,
            "schedule_attributes_available": attributes is not None,
        }

    def _advance_schedule_revision(self, device_id: str) -> None:
        self._schedule_revisions[device_id] = self._schedule_revisions.get(device_id, 0) + 1

    def async_update_schedule_snapshot(
        self, device_id: str, attributes: dict[str, Any]
    ) -> None:
        """Publish a fresh schedule snapshot while holding the device lock."""
        if (device_data := self.data.get(device_id)) is not None:
            _merge_schedule_snapshot(
                device_data, self._schedule_snapshot(device_data["device_info"], attributes)
            )
            self._advance_schedule_revision(device_id)
            self.async_update_listeners()

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from API."""
        try:
            devices = await self.api.get_devices()

            if not devices:
                raise UpdateFailed("No devices found or API error")

            # Fetch stats for each device
            device_data = {}
            schedule_revisions = {}
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
                    schedule_revisions[device_id] = self._schedule_revisions.get(device_id, 0)
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

                device_data[device_id] = {
                    "device_info": device,
                    "stats": stats or {},
                    **self._schedule_snapshot(device, attributes),
                    "today_events": today_events
                    or {"events": [], "total": 0},
                }

            if not device_data:
                raise UpdateFailed("No usable devices found")

            # A poll may finish after a schedule write or while that write is
            # in flight. Keep the newer plans and publish the rest of the poll.
            deletions_observed = False
            offsets = self.schedule_offsets
            for device_id, polled in device_data.items():
                lock = self._schedule_locks.get(device_id)
                if (
                    schedule_revisions[device_id] != self._schedule_revisions.get(device_id, 0)
                    or (lock is not None and lock.locked())
                ) and (current := (self.data or {}).get(device_id)) is not None:
                    _merge_schedule_snapshot(polled, current)
                elif offsets is not None and polled["schedule_attributes_available"]:
                    deletions_observed |= offsets.observe_schedule_deletions(
                        device_id, polled["attributes"]
                    )

            if offsets is not None and deletions_observed:
                self.hass.async_create_task(
                    offsets.async_save(), "save deleted HGSmart schedule references"
                )
            return device_data

        except HGSmartAuthError as err:
            raise ConfigEntryAuthFailed("Authentication failed, please reauthenticate") from err
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
