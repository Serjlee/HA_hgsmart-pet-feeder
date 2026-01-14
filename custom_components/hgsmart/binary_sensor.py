"""Binary sensor platform for HGSmart Pet Feeder."""
import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
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
    """Set up HGSmart binary sensors."""
    coordinator: HGSmartDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities = []
    for device_id, device_data in coordinator.data.items():
        device_info = device_data["device_info"]
        entities.append(HGSmartOnlineSensor(coordinator, device_id, device_info))

    async_add_entities(entities)


class HGSmartOnlineSensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor for device online status."""

    def __init__(
        self,
        coordinator: HGSmartDataUpdateCoordinator,
        device_id: str,
        device_info: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.device_id = device_id
        self._attr_unique_id = f"{device_id}_online"
        self._attr_name = f"{device_info['name']} Online"
        self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
        self._attr_device_info = get_device_info(device_id, device_info)

    @property
    def is_on(self) -> bool:
        """Return true if the device is online."""
        device_data = self.coordinator.data.get(self.device_id)
        if device_data and device_data.get("device_info"):
            return device_data["device_info"].get("online", False)
        return False

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.device_id in self.coordinator.data
        )
