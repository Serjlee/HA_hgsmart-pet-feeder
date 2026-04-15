"""Switch platform for HGSmart Pet Feeder."""
import asyncio
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
from .helpers import (
    find_child_lock_attr_key,
    get_device_info,
    is_child_lock_active,
    read_child_lock_raw,
    restore_child_lock_keys,
    snapshot_child_lock_keys,
    write_child_lock_optimistic,
)

_LOGGER = logging.getLogger(__name__)

# GET /attribute often lags the PUT; immediate refresh overwrites optimistic state.
CHILD_LOCK_REFRESH_DELAY_SEC = 8


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

        entities.append(
            HGSmartButtonLockoutSwitch(coordinator, api, device_id, device_info)
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
        async with self.coordinator.get_schedule_lock(self.device_id, self.slot):
            device_data = self.coordinator.data.get(self.device_id)
            if not device_data or not device_data.get("schedules"):
                raise HomeAssistantError("Device data not available")

            schedule = device_data["schedules"].get(self.slot, {})
            hour = schedule.get("hour", 8)
            minute = schedule.get("minute", 0)
            portions = schedule.get("portions", 1)
            old_enabled = schedule.get("enabled")

            self.coordinator.data[self.device_id]["schedules"][self.slot]["enabled"] = enabled
            self.async_write_ha_state()

            try:
                success = await self.api.set_schedule(
                    self.device_id, self.slot, hour, minute, portions, enabled
                )
                if not success:
                    raise HomeAssistantError(
                        f"Failed to {'enable' if enabled else 'disable'} schedule slot {self.slot}"
                    )
            except Exception:
                self.coordinator.data[self.device_id]["schedules"][self.slot]["enabled"] = old_enabled
                self.async_write_ha_state()
                raise

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.device_id in self.coordinator.data
        )


class HGSmartButtonLockoutSwitch(CoordinatorEntity, SwitchEntity):
    """Turn physical button lockout on or off (ctrl ``child`` = 0/1)."""

    def __init__(
        self,
        coordinator: HGSmartDataUpdateCoordinator,
        api: HGSmartApiClient,
        device_id: str,
        device_info: dict[str, Any],
    ) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self.api = api
        self.device_id = device_id
        self._attr_unique_id = f"{device_id}_button_lockout"
        self._attr_name = f"{device_info['name']} Button Lockout"
        self._attr_icon = "mdi:lock"
        self._attr_device_info = get_device_info(device_id, device_info)

    @property
    def is_on(self) -> bool:
        """Return true when button lockout is enabled."""
        device_data = self.coordinator.data.get(self.device_id)
        if not device_data:
            return False
        return is_child_lock_active(read_child_lock_raw(device_data))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose which attribute key holds child lock (debug / templates)."""
        device_data = self.coordinator.data.get(self.device_id)
        if not device_data:
            return {}
        attrs = device_data.get("attributes")
        if not isinstance(attrs, dict):
            return {"attribute_key": None, "raw_value": None}
        key = find_child_lock_attr_key(attrs)
        return {
            "attribute_key": key,
            "raw_value": read_child_lock_raw(device_data),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable button lockout."""
        await self._set_locked(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable button lockout."""
        await self._set_locked(False)

    async def _set_locked(self, locked: bool) -> None:
        """Send ``child`` ctrl and refresh coordinator data."""
        device_data = self.coordinator.data.get(self.device_id)
        if not device_data:
            raise HomeAssistantError("Device data not available")

        new_val = "1" if locked else "0"
        snap_attr, snap_info = snapshot_child_lock_keys(device_data)
        write_child_lock_optimistic(device_data, new_val)
        self.async_write_ha_state()

        try:
            success = await self.api.set_button_lockout(self.device_id, locked)
            if not success:
                raise HomeAssistantError("Failed to set button lockout on device")
        except Exception:
            restore_child_lock_keys(device_data, snap_attr, snap_info)
            self.async_write_ha_state()
            raise

        async def _deferred_refresh() -> None:
            await asyncio.sleep(CHILD_LOCK_REFRESH_DELAY_SEC)
            await self.coordinator.async_request_refresh()

        self.hass.async_create_task(_deferred_refresh())

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.device_id in self.coordinator.data
        )
