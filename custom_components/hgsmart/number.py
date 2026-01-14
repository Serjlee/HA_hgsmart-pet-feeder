"""Number platform for HGSmart Pet Feeder."""
import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import HGSmartApiClient
from .const import DOMAIN, SCHEDULE_SLOTS
from .coordinator import HGSmartDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HGSmart number entities."""
    coordinator: HGSmartDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    api: HGSmartApiClient = hass.data[DOMAIN][entry.entry_id]["api"]

    # Initialize storage for manual feed portions
    if "manual_feed_portions" not in hass.data[DOMAIN][entry.entry_id]:
        hass.data[DOMAIN][entry.entry_id]["manual_feed_portions"] = {}

    entities = []
    for device_id, device_data in coordinator.data.items():
        device_info = device_data["device_info"]

        # Initialize default portions for this device
        hass.data[DOMAIN][entry.entry_id]["manual_feed_portions"][device_id] = 1

        # Add manual feed portions entity
        entities.append(
            HGSmartManualFeedPortions(hass, entry.entry_id, coordinator, device_id, device_info)
        )

        # Add number entity for each schedule slot
        for slot in range(SCHEDULE_SLOTS):
            entities.append(
                HGSmartSchedulePortions(coordinator, api, device_id, device_info, slot)
            )

    async_add_entities(entities)


class HGSmartSchedulePortions(CoordinatorEntity, NumberEntity):
    """Number entity for feeding schedule portions."""

    def __init__(
        self,
        coordinator: HGSmartDataUpdateCoordinator,
        api: HGSmartApiClient,
        device_id: str,
        device_info: dict,
        slot: int,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self.api = api
        self.device_id = device_id
        self.slot = slot
        self._attr_unique_id = f"{device_id}_schedule_{slot}_portions"
        self._attr_name = f"{device_info['name']} Schedule {slot + 1} Portions"
        self._attr_icon = "mdi:counter"
        self._attr_native_min_value = 1
        self._attr_native_max_value = 10
        self._attr_native_step = 1
        self._attr_mode = NumberMode.BOX
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device_id)},
            "name": device_info["name"],
            "manufacturer": "HGSmart",
            "model": device_info["type"],
            "sw_version": device_info.get("fwVersion"),
        }

    @property
    def native_value(self) -> float | None:
        """Return the portions value."""
        device_data = self.coordinator.data.get(self.device_id)
        if not device_data:
            return None
            
        schedule = device_data.get("schedules", {}).get(self.slot)
        if schedule:
            return float(schedule["portions"])
        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set the portions value."""
        device_data = self.coordinator.data.get(self.device_id)
        if not device_data:
            return
            
        # Get current time from schedule, default to 08:00
        schedule = device_data.get("schedules", {}).get(self.slot)
        if schedule:
            hour = schedule["hour"]
            minute = schedule["minute"]
        else:
            hour = 8
            minute = 0
        
        # Set the schedule with new portions
        await self.api.set_feeding_schedule(
            self.device_id,
            self.slot,
            hour,
            minute,
            int(value),
            enabled=True,
        )
        
        # Refresh data
        await self.coordinator.async_request_refresh()

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.device_id in self.coordinator.data
        )


class HGSmartManualFeedPortions(CoordinatorEntity, NumberEntity):
    """Number entity for manual feed portions."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        coordinator: HGSmartDataUpdateCoordinator,
        device_id: str,
        device_info: dict,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self.hass = hass
        self.entry_id = entry_id
        self.device_id = device_id
        self._attr_unique_id = f"{device_id}_manual_feed_portions"
        self._attr_name = f"{device_info['name']} Manual Feed Portions"
        self._attr_icon = "mdi:food"
        self._attr_native_min_value = 1
        self._attr_native_max_value = 10
        self._attr_native_step = 1
        self._attr_mode = NumberMode.BOX
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device_id)},
            "name": device_info["name"],
            "manufacturer": "HGSmart",
            "model": device_info["type"],
            "sw_version": device_info.get("fwVersion"),
        }

    @property
    def native_value(self) -> float:
        """Return the portions value."""
        return float(
            self.hass.data[DOMAIN][self.entry_id]["manual_feed_portions"].get(self.device_id, 1)
        )

    async def async_set_native_value(self, value: float) -> None:
        """Set the portions value."""
        self.hass.data[DOMAIN][self.entry_id]["manual_feed_portions"][self.device_id] = int(value)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.device_id in self.coordinator.data
        )
