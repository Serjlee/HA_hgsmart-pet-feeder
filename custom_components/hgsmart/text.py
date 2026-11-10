"""Text platform for HGSmart Pet Feeder (SF35 weekly schedule days)."""
import logging
from typing import Any

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HGSmartDataUpdateCoordinator
from .helpers import (
    format_days,
    get_device_info,
    has_weekday_mask,
    mask_to_local_days,
    parse_days,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up schedule-days text entities for models with weekly schedules."""
    coordinator: HGSmartDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]

    entities = []
    for device_id, device_data in coordinator.data.items():
        schedules = device_data.get("schedules") or {}
        # Only SF35-style devices carry a weekday mask on their plans
        if not any(has_weekday_mask(s.get("suffix")) for s in schedules.values()):
            continue
        for slot in sorted(schedules):
            entities.append(
                HGSmartScheduleDays(
                    coordinator, device_id, device_data["device_info"], slot
                )
            )

    async_add_entities(entities)


class HGSmartScheduleDays(CoordinatorEntity, TextEntity):
    """Local weekdays a schedule slot runs on, e.g. "mon,wed,fri" or "weekdays"."""

    _attr_native_min = 1
    _attr_native_max = 64
    _attr_pattern = r"^[A-Za-z, ]+$"

    def __init__(
        self,
        coordinator: HGSmartDataUpdateCoordinator,
        device_id: str,
        device_info: dict[str, Any],
        slot: int,
    ) -> None:
        """Initialize the text entity."""
        super().__init__(coordinator)
        self.device_id = device_id
        self.slot = slot
        self._attr_unique_id = f"{device_id}_schedule_{slot}_days"
        self._attr_name = f"{device_info['name']} Schedule {slot + 1} Days"
        self._attr_icon = "mdi:calendar-week"
        self._attr_device_info = get_device_info(device_id, device_info)

    def _schedule(self) -> dict[str, Any]:
        device_data = self.coordinator.data.get(self.device_id) or {}
        return (device_data.get("schedules") or {}).get(self.slot) or {}

    @property
    def native_value(self) -> str | None:
        """Return the local weekdays, or None if the slot has no weekday mask."""
        schedule = self._schedule()
        suffix = schedule.get("suffix", "")
        if not has_weekday_mask(suffix):
            return None
        days = mask_to_local_days(
            suffix, schedule.get("hour", 8), schedule.get("minute", 0)
        )
        return format_days(days) if days else "none"

    async def async_set_value(self, value: str) -> None:
        """Parse the typed days and write them to the device."""
        try:
            days = parse_days(value)
        except ValueError as err:
            raise HomeAssistantError(
                f"{err}. Use e.g. 'mon,wed,fri', 'weekdays', 'weekends' or 'daily'"
            ) from err
        await self.coordinator.async_set_schedule_days(self.device_id, self.slot, days)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.device_id in self.coordinator.data
            and has_weekday_mask(self._schedule().get("suffix"))
        )
