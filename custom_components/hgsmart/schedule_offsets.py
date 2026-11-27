"""Compensate feeder UTC schedules when Home Assistant's UTC offset changes."""

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MAX_SCHEDULE_SLOTS
from .helpers import ScheduleSlotData, parse_plan_value, shift_weekday_mask

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import HGSmartDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)
OFFSET_CHECK_INTERVAL = timedelta(minutes=5)
_RETRY_DELAYS = tuple(timedelta(minutes=minutes) for minutes in (10, 20, 40, 60))
_MINUTES_PER_DAY = 24 * 60


class PendingCorrection(TypedDict):
    """Persist the source and absolute target before sending a command."""

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


def _matches_target(schedule: ScheduleSlotData, pending: PendingCorrection) -> bool:
    return (
        _minutes(schedule) == pending["target"]
        and schedule["suffix"] == pending["target_suffix"]
    )


class HGSmartScheduleOffsets:
    """Check offsets without polling the cloud until a correction is needed."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        coordinator: HGSmartDataUpdateCoordinator,
    ) -> None:
        self._hass = hass
        self.coordinator = coordinator
        self.api = coordinator.api
        self._store = schedule_offset_store(hass, entry_id)
        self._slots: dict[str, dict[str, SlotOffset]] = {}
        self._saved_slots: dict[str, dict[str, SlotOffset]] = {}
        self._retries: dict[str, tuple[int, int, datetime]] = {}
        self._check_lock = asyncio.Lock()
        self._save_lock = asyncio.Lock()
        self._stopped = False

    async def async_load(self) -> None:
        """Restore offsets and unfinished corrections across restarts."""
        self._slots = await self._store.async_load() or {}
        self._saved_slots = deepcopy(self._slots)

    @staticmethod
    def _current_offset() -> int:
        """Use HA's configured timezone, including non-hour offset changes."""
        offset = dt_util.now().utcoffset()
        assert offset is not None
        return int(offset.total_seconds() / 60)

    async def async_save(self) -> bool:
        """Save changed records and confirm Store wrote them."""
        async with self._save_lock:
            if self._stopped or self._hass.is_stopping:
                return False
            if self._slots == self._saved_slots:
                return True
            snapshot = deepcopy(self._slots)
            await self._store.async_save(snapshot)
            # Store can log a write failure without raising, or defer writes
            # during shutdown. Never send a correction on an unverified journal.
            if self._hass.is_stopping:
                return False
            if await self._store.async_load() != snapshot:
                _LOGGER.error("Could not verify the HGSmart schedule offset journal")
                return False
            self._saved_slots = snapshot
            return True

    async def async_record_time(self, device_id: str, slot: int, offset: int) -> None:
        """Record a manual HA time update while the caller holds the device lock."""
        self._slots.setdefault(device_id, {})[str(slot)] = {"offset": offset}
        await self.async_save()

    def offset_for_schedule(
        self, device_id: str, slot: int, schedule: ScheduleSlotData
    ) -> int | None:
        """Return the offset represented by a schedule's stored UTC time."""
        record = self._slots.get(device_id, {}).get(str(slot))
        if record is None:
            return None
        if (pending := record.get("pending")) is not None:
            current = _minutes(schedule)
            if current == pending["target"]:
                return pending["offset"]
            if current != pending["source"]:
                return None
        return record["offset"]

    async def async_record_weekdays(
        self, device_id: str, slot: int, schedule: ScheduleSlotData
    ) -> None:
        """Keep an explicit weekday edit when the new UTC time is already set."""
        record = self._slots.get(device_id, {}).get(str(slot))
        if record is not None and (pending := record.get("pending")) is not None:
            if _minutes(schedule) == pending["target"]:
                await self.async_record_time(device_id, slot, pending["offset"])

    def observe_schedule_deletions(
        self, device_id: str, attributes: dict[str, Any]
    ) -> bool:
        """Retire references for slots missing or deleted in a complete snapshot."""
        if (records := self._slots.get(device_id)) is None:
            return False
        changed = False
        for slot in tuple(records):
            value = str(attributes.get(f"plan{slot}", "0"))
            if value == "0" or value[:1] in ("2", "3"):
                del records[slot]
                changed = True
        return changed

    async def async_stop(self) -> None:
        """Finish an in-flight check before unloading and closing the API."""
        self._stopped = True
        async with self._check_lock, self._save_lock:
            pass

    async def async_check(self, _now: datetime | None = None) -> None:
        """Check at setup and on the timer, spacing retries per device."""
        if self._stopped or self._hass.is_stopping or self._check_lock.locked():
            return
        async with self._check_lock:
            offset = self._current_offset()
            for device_id in list(self.coordinator.data):
                if self._stopped:
                    return
                retry_offset, previous_index, retry_at = self._retries.get(
                    device_id, (None, 0, None)
                )
                retry_index = 0
                if retry_offset == offset and retry_at is not None:
                    if dt_util.utcnow() < retry_at:
                        continue
                    retry_index = min(previous_index + 1, len(_RETRY_DELAYS) - 1)

                try:
                    async with self.coordinator.get_schedule_lock(device_id):
                        if self._stopped:
                            return
                        success = await self._async_check_device(device_id, offset)
                except Exception:
                    _LOGGER.exception("Could not compensate schedules for %s", device_id)
                    success = False

                if success:
                    self._retries.pop(device_id, None)
                elif not self._stopped:
                    delay = _RETRY_DELAYS[retry_index]
                    self._retries[device_id] = (
                        offset, retry_index, dt_util.utcnow() + delay
                    )
                    _LOGGER.warning(
                        "Schedule compensation for %s is not confirmed; retrying in %s",
                        device_id, delay,
                    )

    async def _async_check_device(self, device_id: str, offset: int) -> bool:
        """Read one snapshot, write affected slots, and verify with one snapshot."""
        device_data = self.coordinator.data.get(device_id)
        if device_data is None:
            return await self.async_save()
        records = self._slots.setdefault(device_id, {})
        attributes = device_data["attributes"]
        available = device_data["schedule_attributes_available"]
        if available:
            self.observe_schedule_deletions(device_id, attributes)
        correction_needed = any(
            record["offset"] != offset or "pending" in record
            for record in records.values()
        )
        if correction_needed:
            attributes = await self.api.get_device_attributes(device_id)
            if attributes is None or self._stopped:
                return False
            self.observe_schedule_deletions(device_id, attributes)
        elif not available:
            # Missing attributes do not imply that any schedules were deleted.
            return await self.async_save()

        writes: dict[int, tuple[ScheduleSlotData, PendingCorrection]] = {}
        for slot in range(MAX_SCHEDULE_SLOTS):
            schedule = _parse_slot(attributes, slot)
            if schedule is None:
                continue
            pending = self._prepare_slot(records, slot, schedule, offset)
            if pending is not None:
                writes[slot] = (schedule, pending)

        if not await self.async_save():
            return False

        if not correction_needed or self._stopped:
            return not self._stopped
        if not writes:
            self.coordinator.async_update_schedule_snapshot(device_id, attributes)
            return True

        for slot, (schedule, pending) in writes.items():
            if self._stopped or self._hass.is_stopping:
                return False
            hour, minute = divmod(pending["target"], 60)
            await self.api.set_schedule(
                device_id,
                slot,
                hour,
                minute,
                schedule["portions"],
                schedule["enabled"],
                suffix=pending["target_suffix"],
            )

        if self._stopped:
            return False
        confirmed = await self.api.get_device_attributes(device_id)
        if confirmed is None or self._stopped:
            return False
        self.observe_schedule_deletions(device_id, confirmed)
        self.coordinator.async_update_schedule_snapshot(device_id, confirmed)

        confirmed_slots = 0
        for slot, (_, pending) in writes.items():
            schedule = _parse_slot(confirmed, slot)
            if str(slot) not in records:
                confirmed_slots += 1
            elif schedule is not None and _matches_target(schedule, pending):
                records[str(slot)] = {"offset": pending["offset"]}
                confirmed_slots += 1
        saved = await self.async_save()
        return saved and confirmed_slots == len(writes)

    @staticmethod
    def _prepare_slot(
        records: dict[str, SlotOffset],
        slot: int,
        schedule: ScheduleSlotData,
        offset: int,
    ) -> PendingCorrection | None:
        """Reconcile a pending command and calculate any new absolute target."""
        key = str(slot)
        record = records.setdefault(key, {"offset": offset})
        current = _minutes(schedule)
        suffix = schedule["suffix"]
        pending = record.get("pending")
        if pending is not None:
            if _matches_target(schedule, pending):
                # The device applied a previous attempt, even if its reply was lost.
                record = records[key] = {"offset": pending["offset"]}
            elif current == pending["source"]:
                if suffix == pending["source_suffix"] and pending["offset"] == offset:
                    return pending
                # A weekday edit or a new UTC offset supersedes the old target.
                record = records[key] = {"offset": record["offset"]}
            elif current == pending["target"]:
                if suffix == pending["source_suffix"]:
                    # The reported time changed before the weekday mask did.
                    return pending
                # The time already moved and the weekdays were edited.
                record = records[key] = {"offset": pending["offset"]}
            else:
                # Keep an intervening edit made in the official app.
                records[key] = {"offset": offset}
                return None

        if record["offset"] == offset:
            return None
        # The signed UTC shift determines whether the UTC weekday changed.
        day_shift, target = divmod(current + record["offset"] - offset, _MINUTES_PER_DAY)
        pending = {
            "source": current,
            "source_suffix": suffix,
            "target": target,
            "target_suffix": shift_weekday_mask(suffix, day_shift),
            "offset": offset,
        }
        record["pending"] = pending
        return pending
