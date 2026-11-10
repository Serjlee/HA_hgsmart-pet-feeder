"""The HGSmart Pet Feeder integration."""
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import config_validation as cv, device_registry as dr
import voluptuous as vol

from .api import HGSmartApiClient, HGSmartConnectionError
from .const import (
    CONF_REFRESH_TOKEN,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    SCHEDULE_SLOTS,
    WEEKDAYS,
)
from .coordinator import HGSmartDataUpdateCoordinator
from .helpers import (
    api_locale_from_hass,
    api_timezone_from_hass,
    has_weekday_mask,
    local_days_to_mask,
)

_LOGGER = logging.getLogger(__name__)

# Service constants
SERVICE_FEED = "feed"
SERVICE_SET_SCHEDULE_DAYS = "set_schedule_days"
ATTR_PORTIONS = "portions"
ATTR_SLOT = "slot"
ATTR_DAYS = "days"

SET_SCHEDULE_DAYS_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SLOT): vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
        vol.Required(ATTR_DAYS): vol.All(
            cv.ensure_list, [vol.All(cv.string, vol.Lower, vol.In(WEEKDAYS))], vol.Length(min=1)
        ),
    },
    extra=vol.ALLOW_EXTRA,
)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.TIME,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HGSmart Pet Feeder from a config entry."""
    username = entry.data[CONF_USERNAME]
    refresh_token = entry.data.get(CONF_REFRESH_TOKEN)

    def persist_refresh_token(new_token: str) -> None:
        """Save a rotated refresh token so it survives restarts and reloads."""
        if entry.data.get(CONF_REFRESH_TOKEN) != new_token:
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_REFRESH_TOKEN: new_token}
            )

    api = HGSmartApiClient(
        username,
        refresh_token=refresh_token,
        locale=api_locale_from_hass(hass),
        timezone=api_timezone_from_hass(hass),
        on_token_refresh=persist_refresh_token,
    )

    # Authenticate
    try:
        authenticated = await api.authenticate()
    except HGSmartConnectionError as err:
        # API down or returning error pages: let HA retry setup later
        # instead of asking the user to log in again
        await api.close()
        raise ConfigEntryNotReady(f"HGSmart API unavailable: {err}") from err
    if not authenticated:
        await api.close()
        raise ConfigEntryAuthFailed("Refresh token expired or invalid, please reauthenticate")

    update_interval = entry.options.get(
        CONF_UPDATE_INTERVAL,
        entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)
    )

    coordinator = HGSmartDataUpdateCoordinator(hass, api, update_interval)

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "api": api,
        "options": dict(entry.options),
    }

    dev_reg = dr.async_get(hass)
    for device_id, device_data in coordinator.data.items():
        device_info = device_data["device_info"]

        raw_name = device_info.get("name", f"Device {device_id}")
        clean_name = " ".join(raw_name.split())
        if len(clean_name) > 50:
            clean_name = clean_name[:47] + "..."

        raw_model = device_info.get("type", "Pet Feeder")
        clean_model = " ".join(raw_model.split())

        dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, device_id)},
            manufacturer="HGSmart",
            model=clean_model,
            name=clean_name,
            sw_version=device_info.get("fwVersion"),
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    async def handle_feed_service(call: ServiceCall) -> None:
        """Handle the feed service call."""
        _LOGGER.info("Feed service called with full data: %s", call.data)

        portions = call.data.get(ATTR_PORTIONS, 1)

        target_device_ids = []

        if "target" in call.data:
            target = call.data["target"]
            if "device_id" in target:
                device_ids = target["device_id"]
                if isinstance(device_ids, str):
                    target_device_ids = [device_ids]
                elif isinstance(device_ids, list):
                    target_device_ids = device_ids

        if not target_device_ids and "device_id" in call.data:
            device_ids = call.data["device_id"]
            if isinstance(device_ids, str):
                target_device_ids = [device_ids]
            elif isinstance(device_ids, list):
                target_device_ids = device_ids

        if not target_device_ids:
            _LOGGER.error("No devices found in service call. Call data: %s", call.data)
            raise HomeAssistantError("No devices specified in target")

        _LOGGER.info("Feed service called for devices %s with %d portions", target_device_ids, portions)

        dev_reg = dr.async_get(hass)

        processed_any = False
        for ha_device_id in target_device_ids:
            device = dev_reg.async_get(ha_device_id)
            if not device:
                _LOGGER.warning("Device %s not found in device registry", ha_device_id)
                continue

            our_device_id = None
            for identifier in device.identifiers:
                if identifier[0] == DOMAIN:
                    our_device_id = identifier[1]
                    break

            if not our_device_id:
                _LOGGER.warning(
                    "Device %s (%s) is not an HGSmart pet feeder - skipping",
                    device.name,
                    ha_device_id
                )
                continue

            api_client = None
            for entry_id, entry_data in hass.data[DOMAIN].items():
                if isinstance(entry_data, dict) and "coordinator" in entry_data:
                    coordinator = entry_data["coordinator"]
                    if our_device_id in coordinator.data:
                        api_client = entry_data["api"]
                        break

            if not api_client:
                raise HomeAssistantError(f"API client not found for device {our_device_id}")

            success = await api_client.send_feed_command(our_device_id, portions)

            if not success:
                raise HomeAssistantError(f"Failed to send feed command to device {our_device_id}")

            _LOGGER.info("Feed command sent successfully to %s (%d portions)", our_device_id, portions)
            processed_any = True

        if not processed_any:
            raise HomeAssistantError(
                "None of the selected devices are HGSmart pet feeders. "
                "Please select a device from the HGSmart integration."
            )

    async def handle_set_schedule_days_service(call: ServiceCall) -> None:
        """Set which local weekdays a schedule slot runs on (SF35 only)."""
        slot = call.data[ATTR_SLOT] - 1  # entity names are 1-based
        days = call.data[ATTR_DAYS]
        dev_reg = dr.async_get(hass)

        processed_any = False
        for ha_device_id in call.data["device_id"]:
            device = dev_reg.async_get(ha_device_id)
            our_device_id = next(
                (i[1] for i in device.identifiers if i[0] == DOMAIN), None
            ) if device else None
            if not our_device_id:
                _LOGGER.warning("Device %s is not an HGSmart device - skipping", ha_device_id)
                continue

            coordinator = api_client = None
            for entry_data in hass.data[DOMAIN].values():
                if isinstance(entry_data, dict) and our_device_id in (
                    entry_data["coordinator"].data or {}
                ):
                    coordinator = entry_data["coordinator"]
                    api_client = entry_data["api"]
                    break
            if not coordinator:
                raise HomeAssistantError(f"Device {our_device_id} is not loaded")

            async with coordinator.get_schedule_lock(our_device_id, slot):
                device_data = coordinator.data[our_device_id]
                if slot >= device_data.get("schedule_slots", SCHEDULE_SLOTS):
                    raise HomeAssistantError(
                        f"{device.name} has no schedule {slot + 1}"
                    )
                schedule = device_data.get("schedules", {}).get(slot)
                if not schedule or not has_weekday_mask(schedule.get("suffix")):
                    raise HomeAssistantError(
                        f"{device.name} does not support weekly schedules"
                    )

                old_suffix = schedule["suffix"]
                suffix = local_days_to_mask(days, schedule["hour"], schedule["minute"])
                schedule["suffix"] = suffix
                try:
                    success = await api_client.set_schedule(
                        our_device_id,
                        slot,
                        schedule["hour"],
                        schedule["minute"],
                        schedule["portions"],
                        schedule["enabled"],
                        suffix=suffix,
                    )
                    if not success:
                        raise HomeAssistantError(
                            f"Failed to set days for schedule {slot + 1} on {device.name}"
                        )
                except Exception:
                    schedule["suffix"] = old_suffix
                    raise
            coordinator.async_update_listeners()
            await coordinator.async_request_refresh()
            processed_any = True

        if not processed_any:
            raise HomeAssistantError("None of the selected devices are HGSmart feeders")

    if not hass.services.has_service(DOMAIN, SERVICE_FEED):
        hass.services.async_register(
            DOMAIN,
            SERVICE_FEED,
            handle_feed_service,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_SET_SCHEDULE_DAYS):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SET_SCHEDULE_DAYS,
            handle_set_schedule_days_service,
            schema=SET_SCHEDULE_DAYS_SCHEMA,
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
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if (
        entry_data
        and entry.options == entry_data["options"]
        and entry.data.get(CONF_REFRESH_TOKEN) == entry_data["api"].refresh_token
    ):
        # Only the running client's own rotated token was saved; no reload needed
        return
    await hass.config_entries.async_reload(entry.entry_id)
