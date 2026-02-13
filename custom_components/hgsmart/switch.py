"""Switch platform for HGSmart Pet Feeder."""
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import HGSmartApiClient
from .const import DOMAIN, SCHEDULE_SLOTS
from .coordinator import HGSmartDataUpdateCoordinator
from .helpers import get_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HGSmart switch entities."""
    coordinator: HGSmartDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    api: HGSmartApiClient = hass.data[DOMAIN][entry.entry_id]["api"]

    entities = []
    for device_id, device_data in coordinator.data.items():
        device_info = device_data["device_info"]

        # Add schedule enable switches for each slot
        for slot in range(SCHEDULE_SLOTS):
            entities.append(
                HGSmartScheduleSwitch(coordinator, api, device_id, device_info, slot)
            )

    async_add_entities(entities)


class HGSmartScheduleSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable/disable a feeding schedule slot."""

    def __init__(
        self,
        coordinator: HGSmartDataUpdateCoordinator,
        api: HGSmartApiClient,
        device_id: str,
        device_info: dict[str, Any],
        slot: int,
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self.api = api
        self.device_id = device_id
        self.slot = slot
        self._attr_unique_id = f"{device_id}_schedule_{slot}_enabled"
        self._attr_name = f"{device_info['name']} Schedule {slot + 1} Enabled"
        self._attr_icon = "mdi:calendar-clock"
        self._attr_device_info = get_device_info(device_id, device_info)

    @property
    def is_on(self) -> bool:
        """Return true if schedule slot is enabled."""
        device_data = self.coordinator.data.get(self.device_id)
        if device_data and device_data.get("schedules"):
            schedule = device_data["schedules"].get(self.slot)
            if schedule:
                return schedule.get("enabled", False)
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the schedule slot."""
        await self._set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the schedule slot."""
        await self._set_enabled(False)

    async def _set_enabled(self, enabled: bool) -> None:
        """Set the enabled state of the schedule slot."""
        device_data = self.coordinator.data.get(self.device_id)
        if not device_data or not device_data.get("schedules"):
            raise HomeAssistantError("Device data not available")

        schedule = device_data["schedules"].get(self.slot, {})
        hour = schedule.get("hour", 8)
        minute = schedule.get("minute", 0)
        portions = schedule.get("portions", 1)

        success = await self.api.set_schedule(
            self.device_id, self.slot, hour, minute, portions, enabled
        )

        if success:
            # Optimistically update local state immediately
            self.coordinator.data[self.device_id]["schedules"][self.slot]["enabled"] = enabled
            self.async_write_ha_state()  # Immediately reflect change in UI
            await self.coordinator.async_request_refresh()  # Sync in background
        else:
            raise HomeAssistantError(
                f"Failed to {'enable' if enabled else 'disable'} schedule slot {self.slot}"
            )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.device_id in self.coordinator.data
        )
