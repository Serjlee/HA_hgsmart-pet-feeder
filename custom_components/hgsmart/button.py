"""Button platform for HGSmart Pet Feeder."""
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import HGSmartApiClient
from .const import DOMAIN
from .coordinator import HGSmartDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HGSmart buttons."""
    coordinator: HGSmartDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    api: HGSmartApiClient = hass.data[DOMAIN][entry.entry_id]["api"]

    entities = []
    for device_id, device_data in coordinator.data.items():
        device_info = device_data["device_info"]
        entities.append(HGSmartFeedButton(hass, entry.entry_id, coordinator, api, device_id, device_info))
        entities.append(HGSmartResetDesiccantButton(coordinator, api, device_id, device_info))

    async_add_entities(entities)


class HGSmartFeedButton(CoordinatorEntity, ButtonEntity):
    """Button to trigger feeding."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        coordinator: HGSmartDataUpdateCoordinator,
        api: HGSmartApiClient,
        device_id: str,
        device_info: dict,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self.hass = hass
        self.entry_id = entry_id
        self.api = api
        self.device_id = device_id
        self._attr_unique_id = f"{device_id}_feed"
        self._attr_name = f"{device_info['name']} Feed"
        self._attr_icon = "mdi:food"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device_id)},
            "name": device_info["name"],
            "manufacturer": "HGSmart",
            "model": device_info["type"],
            "sw_version": device_info.get("fwVersion"),
        }

    async def async_press(self) -> None:
        """Handle the button press."""
        # Get portions from the manual feed portions entity
        portions = self.hass.data[DOMAIN][self.entry_id].get("manual_feed_portions", {}).get(self.device_id, 1)
        _LOGGER.info("Feed button pressed for device %s (%d portions)", self.device_id, portions)
        success = await self.api.send_feed_command(self.device_id, portions)

        if success:
            _LOGGER.info("Feed command sent successfully")
        else:
            _LOGGER.error("Feed command failed")
            raise HomeAssistantError("Failed to send feed command to device")

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.device_id in self.coordinator.data
        )


class HGSmartResetDesiccantButton(CoordinatorEntity, ButtonEntity):
    """Button to reset desiccant expiration timer."""

    def __init__(
        self,
        coordinator: HGSmartDataUpdateCoordinator,
        api: HGSmartApiClient,
        device_id: str,
        device_info: dict,
    ) -> None:
        """Initialize the button."""
        super().__init__(coordinator)
        self.api = api
        self.device_id = device_id
        self._attr_unique_id = f"{device_id}_reset_desiccant"
        self._attr_name = f"{device_info['name']} Reset Desiccant"
        self._attr_icon = "mdi:air-filter"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, device_id)},
            "name": device_info["name"],
            "manufacturer": "HGSmart",
            "model": device_info["type"],
            "sw_version": device_info.get("fwVersion"),
        }

    async def async_press(self) -> None:
        """Handle the button press."""
        _LOGGER.info("Reset desiccant button pressed for device %s", self.device_id)
        success = await self.api.reset_desiccant(self.device_id)

        if success:
            _LOGGER.info("Desiccant reset successfully")
            # Request coordinator refresh to update the sensor
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("Desiccant reset failed")
            raise HomeAssistantError("Failed to reset desiccant timer")

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.device_id in self.coordinator.data
        )
