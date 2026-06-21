"""Select platform for HGSmart Pet Feeder."""
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import HGSmartApiClient
from .const import (
    ATTR_CHOOSEVOICE,
    DOMAIN,
    MEAL_CALL_OPTION_CUSTOM,
    MEAL_CALL_OPTION_DEFAULT,
)
from .coordinator import HGSmartDataUpdateCoordinator
from .helpers import get_device_info

def _read_choosevoice_raw(device_data: dict[str, Any]) -> str | None:
    """Read meal-call voice flag from device status first, then attribute payload."""
    info = device_data.get("device_info")
    if isinstance(info, dict):
        val = info.get(ATTR_CHOOSEVOICE)
        if val is not None:
            return str(val).strip()
    attrs = device_data.get("attributes")
    if isinstance(attrs, dict):
        val = attrs.get(ATTR_CHOOSEVOICE)
        if val is not None:
            return str(val).strip()
    return None


def _write_choosevoice_optimistic(device_data: dict[str, Any], value: str) -> None:
    """Mirror new voice value into status and attributes for instant UI feedback."""
    attrs = device_data.setdefault("attributes", {})
    attrs[ATTR_CHOOSEVOICE] = value
    info = device_data.setdefault("device_info", {})
    info[ATTR_CHOOSEVOICE] = value


def _snapshot_choosevoice_keys(device_data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Copy current choosevoice-related keys from attributes and device status."""
    attrs = device_data.get("attributes") or {}
    attr_keys = {k: attrs[k] for k in attrs if k == ATTR_CHOOSEVOICE}
    info = device_data.get("device_info") or {}
    info_keys = {k: info[k] for k in info if k == ATTR_CHOOSEVOICE}
    return (attr_keys, info_keys)


def _restore_choosevoice_keys(
    device_data: dict[str, Any],
    attr_keys: dict[str, Any],
    info_keys: dict[str, Any],
) -> None:
    """Restore attributes and device_info choosevoice keys to a prior snapshot."""
    attrs = device_data.setdefault("attributes", {})
    if ATTR_CHOOSEVOICE in attrs and ATTR_CHOOSEVOICE not in attr_keys:
        attrs.pop(ATTR_CHOOSEVOICE, None)
    attrs.update(attr_keys)

    info = device_data.setdefault("device_info", {})
    if ATTR_CHOOSEVOICE in info and ATTR_CHOOSEVOICE not in info_keys:
        info.pop(ATTR_CHOOSEVOICE, None)
    info.update(info_keys)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HGSmart select entities."""
    coordinator: HGSmartDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id][
        "coordinator"
    ]
    api: HGSmartApiClient = hass.data[DOMAIN][entry.entry_id]["api"]

    entities = []
    for device_id, device_data in coordinator.data.items():
        device_info = device_data["device_info"]
        entities.append(
            HGSmartMealCallSoundSelect(coordinator, api, device_id, device_info)
        )

    async_add_entities(entities)


class HGSmartMealCallSoundSelect(CoordinatorEntity, SelectEntity):
    """Select default vs custom recording for the meal call sound."""

    _attr_options = [MEAL_CALL_OPTION_DEFAULT, MEAL_CALL_OPTION_CUSTOM]

    def __init__(
        self,
        coordinator: HGSmartDataUpdateCoordinator,
        api: HGSmartApiClient,
        device_id: str,
        device_info: dict[str, Any],
    ) -> None:
        """Initialize the select."""
        super().__init__(coordinator)
        self.api = api
        self.device_id = device_id
        self._attr_unique_id = f"{device_id}_meal_call_sound"
        self._attr_name = f"{device_info['name']} Meal Call Sound"
        self._attr_icon = "mdi:bullhorn"
        self._attr_device_info = get_device_info(device_id, device_info)

    @property
    def current_option(self) -> str | None:
        """Return Default or Custom from device status (list), then attribute GET."""
        device_data = self.coordinator.data.get(self.device_id)
        if not device_data:
            return None
        raw = _read_choosevoice_raw(device_data)
        if raw is None:
            return MEAL_CALL_OPTION_DEFAULT
        if raw == "1":
            return MEAL_CALL_OPTION_CUSTOM
        return MEAL_CALL_OPTION_DEFAULT

    async def async_select_option(self, option: str) -> None:
        """Apply meal call mode: two PUTs (music then choosevoice)."""
        if option not in self._attr_options:
            raise ValueError(f"Invalid option: {option}")

        custom = option == MEAL_CALL_OPTION_CUSTOM
        device_data = self.coordinator.data.get(self.device_id)
        if not device_data:
            raise HomeAssistantError("Device data not available")

        new_val = "1" if custom else "0"
        snap_attr, snap_info = _snapshot_choosevoice_keys(device_data)
        _write_choosevoice_optimistic(device_data, new_val)
        self.async_write_ha_state()

        try:
            success = await self.api.set_meal_call_sound_mode(self.device_id, custom)
            if not success:
                raise HomeAssistantError("Failed to set meal call sound on device")
        except Exception:
            _restore_choosevoice_keys(device_data, snap_attr, snap_info)
            self.async_write_ha_state()
            raise

        await self.coordinator.async_request_refresh()

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.device_id in self.coordinator.data
        )
