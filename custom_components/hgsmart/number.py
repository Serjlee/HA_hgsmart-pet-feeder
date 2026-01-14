"""Number platform for HGSmart Pet Feeder."""
import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import HGSmartDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HGSmart number entities."""
    coordinator: HGSmartDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    
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

    async_add_entities(entities)


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
    def native_value(self) -> int:
        """Return the portions value."""
        return int(
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
