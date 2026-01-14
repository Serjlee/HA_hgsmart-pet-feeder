"""The HGSmart Pet Feeder integration."""
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import device_registry as dr

from .api import HGSmartApiClient
from .const import DOMAIN, CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
from .coordinator import HGSmartDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

# Service constants
SERVICE_FEED = "feed"
ATTR_PORTIONS = "portions"

# Service schema - allow extra keys for target selector data
SERVICE_FEED_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_PORTIONS, default=1): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=10)
        ),
    },
    extra=vol.ALLOW_EXTRA,
)

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

    # Register services
    async def handle_feed_service(call: ServiceCall) -> None:
        """Handle the feed service call."""
        portions = call.data.get(ATTR_PORTIONS, 1)

        # Get device IDs from target (HA passes this via call.data when using device target selector)
        target_device_ids = []
        if "target" in call.data:
            target = call.data["target"]
            if "device_id" in target:
                device_ids = target["device_id"]
                if isinstance(device_ids, str):
                    target_device_ids = [device_ids]
                else:
                    target_device_ids = device_ids

        if not target_device_ids:
            raise HomeAssistantError("No devices specified in target")

        _LOGGER.info("Feed service called for devices %s with %d portions", target_device_ids, portions)

        # Get device registry
        dev_reg = dr.async_get(hass)

        # Process each target device
        for ha_device_id in target_device_ids:
            # Get device from registry
            device = dev_reg.async_get(ha_device_id)
            if not device:
                _LOGGER.error("Device %s not found in device registry", ha_device_id)
                continue

            # Find our device_id from the device identifiers
            our_device_id = None
            for identifier in device.identifiers:
                if identifier[0] == DOMAIN:
                    our_device_id = identifier[1]
                    break

            if not our_device_id:
                _LOGGER.error("Could not find device identifier for %s", ha_device_id)
                continue

            # Find the API client for this device
            api_client = None
            for entry_id, entry_data in hass.data[DOMAIN].items():
                if isinstance(entry_data, dict) and "coordinator" in entry_data:
                    coordinator = entry_data["coordinator"]
                    if our_device_id in coordinator.data:
                        api_client = entry_data["api"]
                        break

            if not api_client:
                raise HomeAssistantError(f"API client not found for device {our_device_id}")

            # Send feed command
            success = await api_client.send_feed_command(our_device_id, portions)

            if not success:
                raise HomeAssistantError(f"Failed to send feed command to device {our_device_id}")

            _LOGGER.info("Feed command sent successfully to %s (%d portions)", our_device_id, portions)

    # Register service only once (check if not already registered)
    if not hass.services.has_service(DOMAIN, SERVICE_FEED):
        hass.services.async_register(
            DOMAIN,
            SERVICE_FEED,
            handle_feed_service,
            schema=SERVICE_FEED_SCHEMA,
        )

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
