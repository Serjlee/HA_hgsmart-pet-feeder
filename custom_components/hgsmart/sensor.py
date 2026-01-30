"""Sensor platform for HGSmart Pet Feeder."""
import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HGSmartDataUpdateCoordinator
from .helpers import get_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HGSmart sensors."""
    coordinator: HGSmartDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []
    for device_id, device_data in coordinator.data.items():
        device_info = device_data["device_info"]
        
        # Add food remaining sensor
        entities.append(
            HGSmartFoodRemainingSensor(coordinator, device_id, device_info)
        )
        
        # Add desiccant expiry sensor
        entities.append(
            HGSmartDesiccantExpirySensor(coordinator, device_id, device_info)
        )

    async_add_entities(entities)


class HGSmartSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for HGSmart sensors."""

    def __init__(
        self,
        coordinator: HGSmartDataUpdateCoordinator,
        device_id: str,
        device_info: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.device_id = device_id
        self._device_info = device_info
        self._attr_device_info = get_device_info(device_id, device_info)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.device_id in self.coordinator.data
        )


class HGSmartFoodRemainingSensor(HGSmartSensorBase):
    """Sensor for food remaining percentage."""

    def __init__(
        self,
        coordinator: HGSmartDataUpdateCoordinator,
        device_id: str,
        device_info: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, device_info)
        self._attr_unique_id = f"{device_id}_food_remaining"
        self._attr_name = f"{device_info['name']} Food Remaining"
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:bowl"

    @property
    def native_value(self) -> int | None:
        """Return the state of the sensor."""
        device_data = self.coordinator.data.get(self.device_id)
        if device_data and device_data.get("stats"):
            remaining = device_data["stats"].get("remaining")
            if remaining is not None:
                return int(remaining)
        return None


class HGSmartDesiccantExpirySensor(HGSmartSensorBase):
    """Sensor for desiccant expiration in days."""

    def __init__(
        self,
        coordinator: HGSmartDataUpdateCoordinator,
        device_id: str,
        device_info: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, device_info)
        self._attr_unique_id = f"{device_id}_desiccant_expiry"
        self._attr_name = f"{device_info['name']} Desiccant Expiry"
        self._attr_native_unit_of_measurement = UnitOfTime.DAYS
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:air-filter"

    @property
    def native_value(self) -> int | None:
        """Return the state of the sensor."""
        device_data = self.coordinator.data.get(self.device_id)
        if device_data and device_data.get("stats"):
            desiccant = device_data["stats"].get("desiccantExpire")
            if desiccant is not None:
                return int(desiccant)
        return None
