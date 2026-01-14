"""The HGSmart Pet Feeder integration."""
import logging

from homeassistant.config_entries import ConfigEntry

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform

from homeassistant.core import HomeAssistant

from homeassistant.exceptions import ConfigEntryNotReady

from homeassistant.helpers import device_registry as dr



from .api import HGSmartApiClient

from .const import DOMAIN, CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL

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



    # Get update interval from options (preferred) or data (fallback)

    update_interval = entry.options.get(

        CONF_UPDATE_INTERVAL,

        entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)

    )



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



    # Register devices with clean names

    dev_reg = dr.async_get(hass)

    for device_id, device_data in coordinator.data.items():

        device_info = device_data["device_info"]



        # Clean device name: remove line breaks, extra spaces, limit length

        raw_name = device_info.get("name", f"Device {device_id}")

        clean_name = " ".join(raw_name.split())  # Remove line breaks and extra spaces

        if len(clean_name) > 50:

            clean_name = clean_name[:47] + "..."



        # Clean model name

        raw_model = device_info.get("type", "Pet Feeder")

        clean_model = " ".join(raw_model.split())



        # Pre-create/update device in registry

        dev_reg.async_get_or_create(

            config_entry_id=entry.entry_id,

            identifiers={(DOMAIN, device_id)},

            manufacturer="HGSmart",

            model=clean_model,

            name=clean_name,

            sw_version=device_info.get("fwVersion"),

        )



    # Forward setup to platforms

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)



    # Add update listener to reload entry when options change

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))



    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id)
        # Close API client session
        await entry_data["api"].close()

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
