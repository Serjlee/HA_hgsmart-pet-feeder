"""Sensor platform for HGSmart Pet Feeder."""
import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
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
        
        # Add battery level sensor
        entities.append(
            HGSmartBatteryLevelSensor(coordinator, device_id, device_info)
        )

        # Add eating stats sensors for Left and Right bowls
        entities.append(
            HGSmartEatingTimesSensor(coordinator, device_id, device_info, "Left", "1")
        )
        entities.append(
            HGSmartEatingDurationSensor(coordinator, device_id, device_info, "Left", "1")
        )
        entities.append(
            HGSmartEatingTimesSensor(coordinator, device_id, device_info, "Right", "2")
        )
        entities.append(
            HGSmartEatingDurationSensor(coordinator, device_id, device_info, "Right", "2")
        )

        entities.append(
            HGSmartTodayEventsSensor(coordinator, device_id, device_info)
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


class HGSmartBatteryLevelSensor(HGSmartSensorBase):
    """Sensor for backup battery level percentage."""

    def __init__(
        self,
        coordinator: HGSmartDataUpdateCoordinator,
        device_id: str,
        device_info: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, device_info)
        self._attr_unique_id = f"{device_id}_battery_level"
        self._attr_name = f"{device_info['name']} Battery"
        self._attr_device_class = SensorDeviceClass.BATTERY
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int | None:
        """Return the state of the sensor."""
        device_data = self.coordinator.data.get(self.device_id)
        if device_data and device_data.get("attributes"):
            electric = device_data["attributes"].get("electric")
            if electric is not None:
                try:
                    return int(electric)
                except ValueError:
                    return None
        return None


class HGSmartEatingTimesSensor(HGSmartSensorBase):
    """Sensor for eating times (count)."""

    def __init__(
        self,
        coordinator: HGSmartDataUpdateCoordinator,
        device_id: str,
        device_info: dict[str, Any],
        bowl_side: str,
        bowl_type: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, device_info)
        self.bowl_type = bowl_type
        self._attr_unique_id = f"{device_id}_{bowl_side.lower()}_eating_times"
        self._attr_name = f"{device_info['name']} {bowl_side} Bowl Eating Times"
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_icon = "mdi:counter"

    @property
    def native_value(self) -> int | None:
        """Return the state of the sensor."""
        device_data = self.coordinator.data.get(self.device_id)
        if device_data and device_data.get("stats"):
            eating = device_data["stats"].get("eating")
            if isinstance(eating, list):
                for item in eating:
                    if isinstance(item, dict) and item.get("type") == self.bowl_type:
                        time_val = item.get("time")
                        return int(time_val) if time_val is not None else None
        return None


class HGSmartEatingDurationSensor(HGSmartSensorBase):
    """Total eating duration (seconds) for one bowl from feeder summary.

    Data comes from ``GET /app/device/feeder/summary/{deviceId}`` (API
    ``get_feeder_stats``). The coordinator stores the response ``data`` object as
    ``stats``; this sensor uses ``stats["eating"]``, matches ``type`` to
    ``bowl_type`` (``"1"`` left, ``"2"`` right), and reads ``duration`` in seconds.
    """

    def __init__(
        self,
        coordinator: HGSmartDataUpdateCoordinator,
        device_id: str,
        device_info: dict[str, Any],
        bowl_side: str,
        bowl_type: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, device_info)
        self.bowl_type = bowl_type
        self._attr_unique_id = f"{device_id}_{bowl_side.lower()}_eating_duration"
        self._attr_name = f"{device_info['name']} {bowl_side} Bowl Eating Duration"
        self._attr_device_class = SensorDeviceClass.DURATION
        self._attr_native_unit_of_measurement = UnitOfTime.SECONDS
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self) -> int | None:
        """Return cumulative eating duration for this bowl (seconds)."""
        device_data = self.coordinator.data.get(self.device_id)
        if not device_data:
            return None
        stats = device_data.get("stats")
        if not isinstance(stats, dict):
            return None
        eating = stats.get("eating")
        if not isinstance(eating, list):
            return None
        for item in eating:
            if not isinstance(item, dict):
                continue
            if str(item.get("type")) != str(self.bowl_type):
                continue
            duration_val = item.get("duration")
            if duration_val is None:
                return None
            try:
                return int(duration_val)
            except (TypeError, ValueError):
                return None
        return None


class HGSmartTodayEventsSensor(HGSmartSensorBase):
    """Sensor for today's device activity log (eating and feeding events)."""

    def __init__(
        self,
        coordinator: HGSmartDataUpdateCoordinator,
        device_id: str,
        device_info: dict[str, Any],
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, device_id, device_info)
        self._attr_unique_id = f"{device_id}_today_events"
        self._attr_name = f"{device_info['name']} Today's Events"
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_icon = "mdi:calendar-today"

    @property
    def native_value(self) -> int | None:
        """Return the number of events today (API total)."""
        device_data = self.coordinator.data.get(self.device_id)
        if not device_data:
            return None
        payload = device_data.get("today_events") or {}
        total = payload.get("total")
        if total is not None:
            try:
                return int(total)
            except (TypeError, ValueError):
                return None
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return parsed events (newest first) and latest entry for templates."""
        device_data = self.coordinator.data.get(self.device_id)
        if not device_data:
            return {"events": []}
        payload = device_data.get("today_events") or {}
        events = payload.get("events")
        if not isinstance(events, list):
            events = []
        attrs: dict[str, Any] = {"events": events}
        if events:
            first = events[0]
            if isinstance(first, dict):
                attrs["last_event_time"] = first.get("createTime")
                attrs["last_event_desc"] = first.get("eventDesc")
                attrs["last_event_code"] = first.get("event")
        return attrs
