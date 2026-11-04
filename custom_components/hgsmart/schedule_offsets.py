"""Compensate feeder UTC schedules when Home Assistant's UTC offset changes."""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SCHEDULE_SLOTS
from .helpers import (
    ScheduleSlotData,
    parse_plan_value,
    shift_weekday_mask,
    utc_day_offset,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import HGSmartDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)
OFFSET_CHECK_INTERVAL = timedelta(minutes=5)


class PendingCorrection(TypedDict):
    """Persist an absolute target before sending a possibly ambiguous command."""

    source: int
    source_suffix: str
    target: int
    target_suffix: str
    offset: int


class SlotOffset(TypedDict):
    """Track the offset last applied to a slot, in minutes east of UTC."""

    offset: int
    pending: NotRequired[PendingCorrection]


def schedule_offset_store(hass: HomeAssistant, entry_id: str) -> Store:
    """Return entry-specific storage, independent of config-entry updates."""
    return Store(hass, 1, f"{DOMAIN}.schedule_offsets.{entry_id}", atomic_writes=True)


def _parse_slot(attributes: dict[str, Any], slot: int) -> ScheduleSlotData | None:
    """Ignore deleted and empty slots rather than creating default schedules."""
    value = str(attributes.get(f"plan{slot}", "0"))
    if len(value) < 8 or value[0] not in ("0", "1"):
        return None
    return parse_plan_value(value)


def _minutes(schedule: ScheduleSlotData) -> int:
    return schedule["hour"] * 60 + schedule["minute"]


def _shifted_suffix(schedule: ScheduleSlotData, target: int) -> str:
    """Keep local weekdays stable when a UTC correction crosses midnight."""
    suffix = schedule["suffix"]
    if not suffix:
        return ""
    hour, minute = divmod(target, 60)
    return shift_weekday_mask(
        suffix,
        utc_day_offset(hour, minute)
        - utc_day_offset(schedule["hour"], schedule["minute"]),
    )


class HGSmartScheduleOffsets:
    """Check offsets without polling the cloud until a correction is needed."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        coordinator: HGSmartDataUpdateCoordinator,
    ) -> None:
        self.coordinator = coordinator
        self.api = coordinator.api
        self._store = schedule_offset_store(hass, entry_id)
        self._slots: dict[str, dict[str, SlotOffset]] = {}
        self._check_lock = asyncio.Lock()
        self._save_lock = asyncio.Lock()
        self._stopped = False

    async def async_load(self) -> None:
        """Restore offsets and unfinished corrections across restarts."""
        self._slots = await self._store.async_load() or {}

    @staticmethod
    def _current_offset() -> int:
        """Use HA's configured timezone, including non-hour offset changes."""
        offset = dt_util.now().utcoffset()
        assert offset is not None
        return int(offset.total_seconds() / 60)

    async def _async_save(self) -> None:
        async with self._save_lock:
            await self._store.async_save(deepcopy(self._slots))

    async def async_record_time(self, device_id: str, slot: int) -> None:
        """A manual HA time setting already uses the current offset.

        The caller holds the coordinator's slot lock, as does compensation.
        """
        self._slots.setdefault(device_id, {})[str(slot)] = {
            "offset": self._current_offset()
        }
        await self._async_save()

    async def async_stop(self) -> None:
        """Finish an in-flight check before unloading and closing the API."""
        self._stopped = True
        async with self._check_lock:
            pass

    async def async_check(self, _now: datetime | None = None) -> None:
        """Check at setup and every five minutes; retry unconfirmed corrections."""
        if self._stopped or self._check_lock.locked():
            return
        async with self._check_lock:
            offset = self._current_offset()
            refresh = False
            for device_id, device_data in list(self.coordinator.data.items()):
                attributes = device_data.get("attributes")
                if not attributes:
                    # An unavailable device must not lose its previous offset.
                    continue
                slot_count = device_data.get("schedule_slots", SCHEDULE_SLOTS)
                for slot in range(slot_count):
                    try:
                        async with self.coordinator.get_schedule_lock(device_id, slot):
                            if self._stopped:
                                return
                            slots = self._slots.setdefault(device_id, {})
                            record = slots.get(str(slot))
                            if record is None:
                                if _parse_slot(attributes, slot) is not None:
                                    # First activation/new slot: establish a baseline.
                                    slots[str(slot)] = {"offset": offset}
                                    await self._async_save()
                                continue
                            if record["offset"] == offset and "pending" not in record:
                                continue
                            # Read fresh values under the same lock as HA controls.
                            refresh = True
                            await self._async_compensate_slot(device_id, slot, offset)
                    except Exception:  # One failed slot must not block the others.
                        _LOGGER.exception(
                            "Could not compensate schedule %d for %s; retrying next check",
                            slot + 1,
                            device_id,
                        )
            if refresh:
                # Never wait for a coordinator refresh while holding a slot lock.
                await self.coordinator.async_request_refresh()

    async def _async_compensate_slot(
        self, device_id: str, slot: int, offset: int
    ) -> None:
        """Apply old_offset - new_offset and confirm it through a cloud read."""
        attributes = await self.api.get_device_attributes(device_id)
        if self._stopped or attributes is None:
            return
        slots = self._slots[device_id]
        schedule = _parse_slot(attributes, slot)
        if schedule is None:
            # A deleted slot must not be resurrected by a pending correction.
            slots.pop(str(slot), None)
            await self._async_save()
            return

        record = slots[str(slot)]
        current = _minutes(schedule)
        pending = record.get("pending")
        if pending is not None:
            target_suffix = pending.get("target_suffix")
            if current == pending["target"] and (
                target_suffix is None or schedule["suffix"] == target_suffix
            ):
                # The device applied a previous attempt, even if its reply was lost.
                record = {"offset": pending["offset"]}
                slots[str(slot)] = record
                await self._async_save()
                pending = None
            elif current != pending["target"] and (
                current != pending["source"]
                or (
                    "source_suffix" in pending
                    and schedule["suffix"] != pending["source_suffix"]
                )
            ):
                # Respect an intervening edit made in the official app.
                _LOGGER.warning(
                    "Schedule %d for %s changed while compensation was pending; "
                    "keeping its new time",
                    slot + 1,
                    device_id,
                )
                record = {"offset": offset}
                slots[str(slot)] = record
                await self._async_save()
                return

        if record["offset"] == offset:
            if pending is not None and current == pending["source"]:
                # The offset returned before the cloud applied the correction.
                slots[str(slot)] = {"offset": offset}
                await self._async_save()
            return

        if pending is None:
            target = (current + record["offset"] - offset) % (24 * 60)
            target_suffix = _shifted_suffix(schedule, target)
            pending = {
                "source": current,
                "source_suffix": schedule["suffix"],
                "target": target,
                "target_suffix": target_suffix,
                "offset": offset,
            }
            record["pending"] = pending
            # Persist before PUT: a restart must resend an absolute target.
            await self._async_save()
        else:
            target = pending["target"]
            if target_suffix is None:
                # Migrate pending records written by version 1.4.0.
                target_suffix = _shifted_suffix(schedule, target)
                pending["source_suffix"] = schedule["suffix"]
                pending["target_suffix"] = target_suffix
                await self._async_save()

        if self._stopped:
            return
        hour, minute = divmod(target, 60)
        suffix_args = {"suffix": target_suffix} if target_suffix else {}
        await self.api.set_schedule(
            device_id,
            slot,
            hour,
            minute,
            schedule["portions"],
            schedule["enabled"],
            **suffix_args,
        )

        # PUT acknowledgement alone does not confirm the device's reported state.
        confirmed_attributes = await self.api.get_device_attributes(device_id)
        confirmed = (
            _parse_slot(confirmed_attributes, slot)
            if confirmed_attributes is not None
            else None
        )
        if (
            confirmed is None
            or _minutes(confirmed) != target
            or confirmed["suffix"] != target_suffix
        ):
            _LOGGER.warning(
                "Compensation of schedule %d for %s is not confirmed; "
                "retrying next check",
                slot + 1,
                device_id,
            )
            return

        slots[str(slot)] = {"offset": offset}
        await self._async_save()
        _LOGGER.info(
            "Compensated schedule %d for %s by %+d minutes to %02d:%02d UTC",
            slot + 1,
            device_id,
            record["offset"] - pending["offset"],
            hour,
            minute,
        )
