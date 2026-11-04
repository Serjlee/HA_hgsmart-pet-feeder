"""Time platform for HGSmart Pet Feeder."""
import logging
from datetime import datetime, time as dt_time, timezone
from typing import Any

from homeassistant.components.time import TimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .api import HGSmartApiClient
from .const import DOMAIN
from .coordinator import HGSmartDataUpdateCoordinator
from .helpers import get_device_info, shift_weekday_mask, utc_day_offset

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HGSmart time entities."""
    coordinator: HGSmartDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    api: HGSmartApiClient = hass.data[DOMAIN][entry.entry_id]["api"]

    entities = []
    for device_id, device_data in coordinator.data.items():
        device_info = device_data["device_info"]

        # Add schedule time entities for each slot
        for slot in range(device_data["schedule_slots"]):
            entities.append(
                HGSmartScheduleTime(hass, coordinator, api, device_id, device_info, slot)
            )

    async_add_entities(entities)


class HGSmartScheduleTime(CoordinatorEntity, TimeEntity):
    """Time entity for a feeding schedule slot."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: HGSmartDataUpdateCoordinator,
        api: HGSmartApiClient,
        device_id: str,
        device_info: dict[str, Any],
        slot: int,
    ) -> None:
        """Initialize the time entity."""
        super().__init__(coordinator)
        self.hass = hass
        self.api = api
        self.device_id = device_id
        self.slot = slot
        self._attr_unique_id = f"{device_id}_schedule_{slot}_time"
        self._attr_name = f"{device_info['name']} Schedule {slot + 1} Time"
        self._attr_icon = "mdi:clock-outline"
        self._attr_device_info = get_device_info(device_id, device_info)

    @property
    def native_value(self) -> dt_time | None:
        """Return the current time value in local timezone."""
        device_data = self.coordinator.data.get(self.device_id)
        if device_data and device_data.get("schedules"):
            schedule = device_data["schedules"].get(self.slot)
            if schedule:
                # Convert UTC to local time
                utc_hour = schedule.get("hour", 8)
                utc_minute = schedule.get("minute", 0)

                # Create UTC datetime and convert to local
                utc_dt = datetime.now(timezone.utc).replace(
                    hour=utc_hour, minute=utc_minute, second=0, microsecond=0
                )
                local_dt = dt_util.as_local(utc_dt)
                return local_dt.time()
        return None

    async def async_set_value(self, value: dt_time) -> None:
        """Set the time value (converts local to UTC for API)."""
        offsets = self.coordinator.schedule_offsets
        now = dt_util.now()
        offset = now.utcoffset()
        assert offset is not None
        if offsets is not None:
            # This is a recurring time. On a transition day, its occurrence
            # earlier today may have had a different offset.
            now = now.astimezone(timezone(offset))
        local_dt = now.replace(
            hour=value.hour, minute=value.minute, second=0, microsecond=0
        )
        utc_dt = dt_util.as_utc(local_dt)
        offset = local_dt.utcoffset()
        assert offset is not None
        utc_hour = utc_dt.hour
        utc_minute = utc_dt.minute

        _LOGGER.debug(
            "Setting schedule %d time: local %02d:%02d -> UTC %02d:%02d",
            self.slot,
            value.hour,
            value.minute,
            utc_hour,
            utc_minute,
        )

        def update(schedule: dict[str, Any]) -> dict[str, Any]:
            # Preserve the scheduled local days even if the old UTC time
            # still uses the offset from before a timezone change.
            old_offset = (
                offsets.offset_for_schedule(self.device_id, self.slot, schedule)
                if offsets is not None
                else None
            )
            shift = (utc_dt.date() - local_dt.date()).days - utc_day_offset(
                schedule["hour"], schedule["minute"], offset=old_offset
            )
            return {
                "hour": utc_hour,
                "minute": utc_minute,
                "suffix": shift_weekday_mask(schedule["suffix"], shift),
            }

        await self.coordinator.async_update_schedule(
            self.device_id,
            self.slot,
            update,
            time_offset=int(offset.total_seconds() / 60),
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.device_id in self.coordinator.data
        )
