"""Number platform for HGSmart Pet Feeder."""
import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import HGSmartApiClient
from .const import DOMAIN, MAX_PORTIONS, MIN_PORTIONS, SCHEDULE_SLOTS
from .coordinator import HGSmartDataUpdateCoordinator
from .helpers import get_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HGSmart number entities."""
    coordinator: HGSmartDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    api = hass.data[DOMAIN][entry.entry_id]["api"]

    if "manual_feed_portions" not in hass.data[DOMAIN][entry.entry_id]:
        hass.data[DOMAIN][entry.entry_id]["manual_feed_portions"] = {}

    entities = []
    for device_id, device_data in coordinator.data.items():
        device_info = device_data["device_info"]

        # Add manual feed portions entity
        entities.append(
            HGSmartManualFeedPortions(hass, entry.entry_id, coordinator, device_id, device_info)
        )

        # Add food remaining percentage entity
        entities.append(
            HGSmartFoodRemainingNumber(coordinator, api, device_id, device_info)
        )

        # Add schedule portions entities for each slot
        for slot in range(SCHEDULE_SLOTS):
            entities.append(
                HGSmartSchedulePortions(coordinator, api, device_id, device_info, slot)
            )

    async_add_entities(entities)


class HGSmartManualFeedPortions(CoordinatorEntity, RestoreEntity, NumberEntity):
    """Number entity for manual feed portions."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        coordinator: HGSmartDataUpdateCoordinator,
        device_id: str,
        device_info: dict[str, Any],
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
        self._attr_device_info = get_device_info(device_id, device_info)
        self._native_value = 1  # Default value

    async def async_added_to_hass(self) -> None:
        """Restore previous state when entity is added to hass."""
        await super().async_added_to_hass()

        if (last_state := await self.async_get_last_state()) is not None:
            if last_state.state not in (None, "unknown", "unavailable"):
                try:
                    self._native_value = int(float(last_state.state))
                except (ValueError, TypeError):
                    self._native_value = 1

        self.hass.data[DOMAIN][self.entry_id]["manual_feed_portions"][self.device_id] = self._native_value

    @property
    def native_value(self) -> int:
        """Return the portions value."""
        return self._native_value

    async def async_set_native_value(self, value: float) -> None:
        """Set the portions value."""
        self._native_value = int(value)
        self.hass.data[DOMAIN][self.entry_id]["manual_feed_portions"][self.device_id] = self._native_value
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.device_id in self.coordinator.data
        )


class HGSmartFoodRemainingNumber(CoordinatorEntity, NumberEntity):
    """Number entity for setting food remaining percentage."""

    def __init__(
        self,
        coordinator: HGSmartDataUpdateCoordinator,
        api: HGSmartApiClient,
        device_id: str,
        device_info: dict[str, Any],
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self.api = api
        self.device_id = device_id
        self._attr_unique_id = f"{device_id}_set_food_remaining"
        self._attr_name = f"{device_info['name']} Set Food Remaining"
        self._attr_icon = "mdi:bowl"
        self._attr_native_min_value = 0
        self._attr_native_max_value = 100
        self._attr_native_step = 1
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_mode = NumberMode.SLIDER
        self._attr_device_info = get_device_info(device_id, device_info)

    @property
    def native_value(self) -> int | None:
        """Return the current food remaining percentage from sensor."""
        device_data = self.coordinator.data.get(self.device_id)
        if device_data and device_data.get("stats"):
            remaining = device_data["stats"].get("remaining")
            if remaining is not None:
                return int(remaining)
        return None

    async def async_set_native_value(self, value: float) -> None:
        """Set the food remaining percentage."""
        percentage = int(value)
        _LOGGER.info("Setting food remaining to %d%% for device %s", percentage, self.device_id)
        success = await self.api.set_food_remaining(self.device_id, percentage)

        if success:
            _LOGGER.info("Food remaining updated successfully")
            # Request coordinator refresh to update the sensor
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Failed to update food remaining")
            raise HomeAssistantError("Failed to update food remaining percentage")

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.device_id in self.coordinator.data
        )


class HGSmartSchedulePortions(CoordinatorEntity, NumberEntity):
    """Number entity for schedule portions per slot."""

    def __init__(
        self,
        coordinator: HGSmartDataUpdateCoordinator,
        api: HGSmartApiClient,
        device_id: str,
        device_info: dict[str, Any],
        slot: int,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator)
        self.api = api
        self.device_id = device_id
        self.slot = slot
        self._attr_unique_id = f"{device_id}_schedule_{slot}_portions"
        self._attr_name = f"{device_info['name']} Schedule {slot + 1} Portions"
        self._attr_icon = "mdi:food"
        self._attr_native_min_value = MIN_PORTIONS
        self._attr_native_max_value = MAX_PORTIONS
        self._attr_native_step = 1
        self._attr_mode = NumberMode.BOX
        self._attr_device_info = get_device_info(device_id, device_info)

    @property
    def native_value(self) -> int | None:
        """Return the current portions value."""
        device_data = self.coordinator.data.get(self.device_id)
        if device_data and device_data.get("schedules"):
            schedule = device_data["schedules"].get(self.slot)
            if schedule:
                return schedule.get("portions", 1)
        return 1

    async def async_set_native_value(self, value: float) -> None:
        """Set the portions value."""
        async with self.coordinator.get_schedule_lock(self.device_id, self.slot):
            device_data = self.coordinator.data.get(self.device_id)
            if not device_data or not device_data.get("schedules"):
                raise HomeAssistantError("Device data not available")

            schedule = device_data["schedules"].get(self.slot, {})
            enabled = schedule.get("enabled", False)
            hour = schedule.get("hour", 8)
            minute = schedule.get("minute", 0)
            old_portions = schedule.get("portions")

            portions = int(value)

            self.coordinator.data[self.device_id]["schedules"][self.slot]["portions"] = portions
            self.async_write_ha_state()

            try:
                success = await self.api.set_schedule(
                    self.device_id, self.slot, hour, minute, portions, enabled
                )
                if not success:
                    raise HomeAssistantError(
                        f"Failed to set portions for schedule slot {self.slot}"
                    )
            except Exception:
                self.coordinator.data[self.device_id]["schedules"][self.slot]["portions"] = old_portions
                self.async_write_ha_state()
                raise

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.device_id in self.coordinator.data
        )
