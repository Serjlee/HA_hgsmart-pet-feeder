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
from .helpers import get_device_info, parse_batinfo

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
        entities.append(HGSmartBatterySensor(coordinator, device_id, device_info))
        attributes = device_data.get("attributes")
        # SF35: lid state (absent on S-series)
        if attributes and "capstatus" in attributes:
            entities.append(HGSmartLidSensor(coordinator, device_id, device_info))
        # SF35: battery charging state (absent on S-series)
        if parse_batinfo(attributes) is not None:
            entities.append(HGSmartBatteryChargingSensor(coordinator, device_id, device_info))

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

class HGSmartBatterySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor for device backup battery present status."""

    def __init__(
        self,
        coordinator: HGSmartDataUpdateCoordinator,
        device_id: str,
        device_info: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.device_id = device_id
        self._attr_unique_id = f"{device_id}_battery_backup"
        self._attr_name = f"{device_info['name']} Battery Backup"
        self._attr_device_class = BinarySensorDeviceClass.POWER
        self._attr_device_info = get_device_info(device_id, device_info)

    @property
    def is_on(self) -> bool:
        """Return true if the device has battery backup."""
        device_data = self.coordinator.data.get(self.device_id)
        if device_data and device_data.get("attributes"):
            attributes = device_data["attributes"]
            if "batstate" in attributes:
                return attributes.get("batstate") == "1"
            # SF35 has no "batstate"; "batinfo" says whether a battery is fitted
            batinfo = parse_batinfo(attributes)
            if batinfo:
                return batinfo["present"]
        return False

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.device_id in self.coordinator.data
        )


class _HGSmartAttributeBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Base for binary sensors derived from the attribute payload."""

    def __init__(
        self,
        coordinator: HGSmartDataUpdateCoordinator,
        device_id: str,
        device_info: dict[str, Any],
        key: str,
        name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.device_id = device_id
        self._attr_unique_id = f"{device_id}_{key}"
        self._attr_name = f"{device_info['name']} {name}"
        self._attr_device_info = get_device_info(device_id, device_info)

    def _attributes(self) -> dict[str, Any]:
        device_data = self.coordinator.data.get(self.device_id) or {}
        return device_data.get("attributes") or {}

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.device_id in self.coordinator.data
            and self.is_on is not None
        )


class HGSmartBatteryChargingSensor(_HGSmartAttributeBinarySensor):
    """On while the backup battery charges (SF35)."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    def __init__(self, coordinator, device_id, device_info) -> None:
        """Initialize the sensor."""
        super().__init__(
            coordinator, device_id, device_info, "battery_charging", "Battery Charging"
        )

    @property
    def is_on(self) -> bool | None:
        """Return true while charging."""
        batinfo = parse_batinfo(self._attributes())
        return batinfo["charging"] if batinfo else None


class HGSmartLidSensor(_HGSmartAttributeBinarySensor):
    """On while the lid is open, i.e. a paired pet is at the bowl (SF35).

    The cloud reflects lid changes within seconds, but HA only sees them at
    the next poll, so short meals are missed with long update intervals.
    """

    _attr_device_class = BinarySensorDeviceClass.OPENING

    def __init__(self, coordinator, device_id, device_info) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, device_info, "lid", "Lid")

    @property
    def is_on(self) -> bool | None:
        """Return true while the lid is open."""
        raw = str(self._attributes().get("capstatus", "")).strip()
        return {"1": True, "0": False}.get(raw)

