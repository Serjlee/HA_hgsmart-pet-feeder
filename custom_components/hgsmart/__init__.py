"""The HGSmart Pet Feeder integration."""
import logging

from homeassistant.config_entries import ConfigEntry

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform

from homeassistant.core import HomeAssistant

from homeassistant.exceptions import ConfigEntryNotReady

from homeassistant.helpers import device_registry as dr



from .api import HGSmartApiClient

from .const import DOMAIN, CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL, CONF_DEVICE_ROOMS

from .coordinator import HGSmartDataUpdateCoordinator



_LOGGER = logging.getLogger(__name__)



PLATFORMS: list[Platform] = [

    Platform.SENSOR,

    Platform.BINARY_SENSOR,

    Platform.BUTTON,

    Platform.NUMBER,

]





async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:

    """Set up HGSmart Pet Feeder from a config entry."""

    username = entry.data[CONF_USERNAME]

    password = entry.data[CONF_PASSWORD]



    # Initialize API client

    api = HGSmartApiClient(username, password)



    # Login - raise ConfigEntryNotReady on failure so HA will retry

    if not await api.login():

        _LOGGER.error("Failed to login to HGSmart API")

        raise ConfigEntryNotReady("Failed to authenticate with HGSmart API")



    # Get update interval

    update_interval = entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)



    # Create data update coordinator

    coordinator = HGSmartDataUpdateCoordinator(hass, api, update_interval)



    # Fetch initial data - this will raise ConfigEntryNotReady on failure

    await coordinator.async_config_entry_first_refresh()



    # Store coordinator

    hass.data.setdefault(DOMAIN, {})

    hass.data[DOMAIN][entry.entry_id] = {

        "coordinator": coordinator,

        "api": api,

    }



    # Assign devices to areas if configured

    device_rooms = entry.data.get(CONF_DEVICE_ROOMS)

    if device_rooms:

        dev_reg = dr.async_get(hass)

        for device_id, device_data in coordinator.data.items():

            if device_id in device_rooms and device_rooms[device_id]:

                area_id = device_rooms[device_id]

                device_info = device_data["device_info"]

                

                # Pre-create/update device in registry

                dev_reg.async_get_or_create(

                    config_entry_id=entry.entry_id,

                    identifiers={(DOMAIN, device_id)},

                    manufacturer="HGSmart",

                    model=device_info.get("type"),

                    name=device_info.get("name"),

                    sw_version=device_info.get("fwVersion"),

                    suggested_area=area_id,

                )



    # Forward setup to platforms

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)



    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
