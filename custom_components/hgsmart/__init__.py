"""The HGSmart Pet Feeder integration."""
import asyncio
import logging
import shutil
import tempfile
from functools import partial
from pathlib import Path
from typing import Any

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.components.media_player import async_process_play_media_url
from homeassistant.components.media_source import async_resolve_media
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.event import async_track_time_interval
import voluptuous as vol

from .api import HGSmartApiClient, HGSmartConnectionError
from .audio import (
    AUDIO_VOLUME_DEFAULT,
    SILENT_WAV,
    HGSmartAudioError,
    async_convert_audio,
    async_send_audio,
    find_local_endpoint,
    inspect_wav,
    parse_endpoint,
)
from .const import (
    CONF_COMPENSATE_TIMEZONE_OFFSET,
    CONF_REFRESH_TOKEN,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MAX_SCHEDULE_SLOTS,
    WEEKDAYS,
)
from .coordinator import HGSmartDataUpdateCoordinator
from .helpers import api_locale_from_hass, api_timezone_from_hass
from .schedule_offsets import (
    OFFSET_CHECK_INTERVAL,
    HGSmartScheduleOffsets,
    schedule_offset_store,
)

_LOGGER = logging.getLogger(__name__)

# Service constants
SERVICE_FEED = "feed"
SERVICE_MUTE_MEAL_CALL = "mute_meal_call"
SERVICE_SET_SCHEDULE_DAYS = "set_schedule_days"
SERVICE_UPLOAD_SOUND = "upload_sound"
ATTR_DEVICE_ID = "device_id"
ATTR_DAYS = "days"
ATTR_HOST = "host"
ATTR_MEDIA = "media"
ATTR_PORTIONS = "portions"
ATTR_SLOT = "slot"
ATTR_VOLUME = "volume"

AudioTarget = tuple[
    str,
    HGSmartApiClient,
    HGSmartDataUpdateCoordinator,
    dict[str, Any],
    str,
]

SET_SCHEDULE_DAYS_SCHEMA = vol.Schema(
    {
        vol.Required("device_id"): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_SLOT): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_SCHEDULE_SLOTS)),
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


def _service_device_ids(call: ServiceCall) -> list[str]:
    """Return device registry IDs from either service field representation."""
    raw_ids: str | list[str] | None = None
    target = call.data.get("target")
    if isinstance(target, dict):
        raw_ids = target.get(ATTR_DEVICE_ID)
    if not raw_ids:
        raw_ids = call.data.get(ATTR_DEVICE_ID)
    if isinstance(raw_ids, str):
        return [raw_ids]
    if isinstance(raw_ids, list):
        return raw_ids
    return []


def _find_device_client(
    hass: HomeAssistant, ha_device_id: str
) -> tuple[str, HGSmartApiClient, HGSmartDataUpdateCoordinator, dict[str, Any]] | None:
    """Map a Home Assistant device ID to HGSmart runtime objects."""
    device = dr.async_get(hass).async_get(ha_device_id)
    if not device:
        return None
    cloud_device_id = next(
        (identifier[1] for identifier in device.identifiers if identifier[0] == DOMAIN),
        None,
    )
    if not cloud_device_id:
        return None
    for entry_data in hass.data.get(DOMAIN, {}).values():
        if not isinstance(entry_data, dict) or "coordinator" not in entry_data:
            continue
        coordinator = entry_data["coordinator"]
        if cloud_device_id in coordinator.data:
            return (
                cloud_device_id,
                entry_data["api"],
                coordinator,
                entry_data,
            )
    return None


def _resolve_audio_devices(
    hass: HomeAssistant, call: ServiceCall
) -> list[AudioTarget]:
    """Resolve selected HA devices and their local audio endpoints."""
    target_device_ids = _service_device_ids(call)
    if not target_device_ids:
        raise HomeAssistantError("No HGSmart feeder was selected")

    resolved_devices = []
    endpoint_override = call.data.get(ATTR_HOST)
    for ha_device_id in target_device_ids:
        runtime = _find_device_client(hass, ha_device_id)
        if runtime is None:
            raise HomeAssistantError(
                f"Device {ha_device_id} is not an available HGSmart feeder"
            )
        cloud_device_id, api_client, device_coordinator, entry_data = runtime
        device_data = device_coordinator.data[cloud_device_id]
        endpoint = (
            endpoint_override.strip()
            if isinstance(endpoint_override, str) and endpoint_override.strip()
            else find_local_endpoint(device_data)
        )
        if not endpoint:
            raise HomeAssistantError(
                "The feeder did not publish its local IP address. Enter it in "
                "the Host field (for example 192.168.1.42)."
            )
        try:
            parse_endpoint(endpoint)
        except HGSmartAudioError as err:
            raise HomeAssistantError(str(err)) from err
        resolved_devices.append(
            (
                cloud_device_id,
                api_client,
                device_coordinator,
                entry_data,
                endpoint,
            )
        )
    return resolved_devices


async def _async_prepare_sound(
    hass: HomeAssistant,
    media: Any,
    output_path: Path,
    volume_percent: float,
) -> bytes:
    """Resolve a media selector value and convert it to feeder WAV bytes."""
    if not isinstance(media, dict):
        raise HGSmartAudioError("Select an audio file from Home Assistant media")
    media_content_id = media.get("media_content_id")
    if not isinstance(media_content_id, str) or not media_content_id:
        raise HGSmartAudioError("The selected media has no media_content_id")

    resolved = await async_resolve_media(hass, media_content_id, None)
    if resolved.path:
        input_source = str(resolved.path)
    elif resolved.url:
        input_source = async_process_play_media_url(hass, resolved.url)
    else:
        raise HGSmartAudioError("Home Assistant could not resolve the selected media")

    await async_convert_audio(
        get_ffmpeg_manager(hass).binary,
        input_source,
        output_path,
        volume_percent=volume_percent,
    )
    audio = await hass.async_add_executor_job(output_path.read_bytes)
    inspect_wav(audio)
    return audio


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up HGSmart Pet Feeder from a config entry."""
    compensate_offset = entry.options.get(CONF_COMPENSATE_TIMEZONE_OFFSET, False)
    if not compensate_offset:
        # Re-enabling later starts at the then-current offset, without catch-up.
        await schedule_offset_store(hass, entry.entry_id).async_remove()

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
        entry.data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
    )

    coordinator = HGSmartDataUpdateCoordinator(hass, api, update_interval)

    await coordinator.async_config_entry_first_refresh()

    if compensate_offset:
        offsets = HGSmartScheduleOffsets(hass, entry.entry_id, coordinator)
        await offsets.async_load()
        coordinator.schedule_offsets = offsets

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

    if (offsets := coordinator.schedule_offsets) is not None:
        entry.async_create_background_task(
            hass, offsets.async_check(), "hgsmart schedule offset check"
        )
        entry.async_on_unload(
            async_track_time_interval(
                hass, offsets.async_check, OFFSET_CHECK_INTERVAL
            )
        )

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    async def handle_feed_service(call: ServiceCall) -> None:
        """Handle the feed service call."""
        _LOGGER.info("Feed service called with full data: %s", call.data)

        portions = call.data.get(ATTR_PORTIONS, 1)

        target_device_ids = _service_device_ids(call)

        if not target_device_ids:
            _LOGGER.error("No devices found in service call. Call data: %s", call.data)
            raise HomeAssistantError("No devices specified in target")

        _LOGGER.info(
            "Feed service called for devices %s with %d portions",
            target_device_ids,
            portions,
        )

        processed_any = False
        for ha_device_id in target_device_ids:
            runtime = _find_device_client(hass, ha_device_id)
            if runtime is None:
                _LOGGER.warning(
                    "Device %s is not an available HGSmart feeder - skipping",
                    ha_device_id,
                )
                continue
            cloud_device_id, api_client, _, _ = runtime
            success = await api_client.send_feed_command(cloud_device_id, portions)

            if not success:
                raise HomeAssistantError(
                    f"Failed to send feed command to device {cloud_device_id}"
                )

            _LOGGER.info(
                "Feed command sent successfully to %s (%d portions)",
                cloud_device_id,
                portions,
            )
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

            coordinator = next(
                (
                    entry_data["coordinator"]
                    for entry_data in hass.data[DOMAIN].values()
                    if isinstance(entry_data, dict)
                    and "coordinator" in entry_data
                    and our_device_id in (entry_data["coordinator"].data or {})
                ),
                None,
            )
            if not coordinator:
                raise HomeAssistantError(f"Device {our_device_id} is not loaded")

            await coordinator.async_set_schedule_days(our_device_id, slot, days)
            processed_any = True

        if not processed_any:
            raise HomeAssistantError("None of the selected devices are HGSmart feeders")

    async def _async_install_audio(
        audio: bytes, resolved_devices: list[AudioTarget]
    ) -> None:
        """Upload and activate prepared audio on selected feeders."""
        updated_coordinators: set[HGSmartDataUpdateCoordinator] = set()
        try:
            for (
                cloud_device_id,
                api_client,
                device_coordinator,
                entry_data,
                endpoint,
            ) in resolved_devices:
                locks = entry_data.setdefault("voice_upload_locks", {})
                lock = locks.setdefault(cloud_device_id, asyncio.Lock())
                async with lock:
                    voice_url = await api_client.upload_voice_file(audio)
                    if not voice_url:
                        raise HomeAssistantError(
                            f"HGSmart rejected the sound for device {cloud_device_id}"
                        )
                    if not await api_client.set_custom_voice_url(
                        cloud_device_id, voice_url
                    ):
                        raise HomeAssistantError(
                            f"Could not prepare device {cloud_device_id} for the sound"
                        )
                    transfer_may_be_open = True
                    try:
                        if not await api_client.prepare_custom_voice_transfer(
                            cloud_device_id
                        ):
                            raise HomeAssistantError(
                                "Could not open the local audio transfer on "
                                f"{cloud_device_id}"
                            )

                        # The official app gives the feeder half a second to open
                        # its temporary TCP listener.
                        await asyncio.sleep(0.5)
                        await async_send_audio(
                            endpoint,
                            audio,
                            finalize_transfer=partial(
                                api_client.finish_custom_voice_transfer,
                                cloud_device_id,
                            ),
                        )
                        transfer_may_be_open = False
                    finally:
                        if transfer_may_be_open:
                            try:
                                await api_client.finish_custom_voice_transfer(
                                    cloud_device_id
                                )
                            except Exception:  # Best-effort cleanup preserves the cause.
                                _LOGGER.exception(
                                    "Could not close audio transfer mode on %s",
                                    cloud_device_id,
                                )

                    if not await api_client.activate_custom_voice(cloud_device_id):
                        raise HomeAssistantError(
                            "Sound transferred, but activation failed on "
                            f"{cloud_device_id}"
                        )
                    updated_coordinators.add(device_coordinator)
                    _LOGGER.info(
                        "Custom meal-call sound uploaded to HGSmart device %s",
                        cloud_device_id,
                    )
        finally:
            # A coordinator refresh fetches every device on the same account.
            for device_coordinator in updated_coordinators:
                await device_coordinator.async_request_refresh()

    async def handle_upload_sound_service(call: ServiceCall) -> None:
        """Convert and upload a custom meal-call sound."""
        resolved_devices = _resolve_audio_devices(hass, call)

        temp_dir = await hass.async_add_executor_job(
            tempfile.mkdtemp, "", "hgsmart_voice_"
        )
        output_path = Path(temp_dir) / "custom_voice.wav"
        try:
            audio = await _async_prepare_sound(
                hass,
                call.data.get(ATTR_MEDIA),
                output_path,
                call.data.get(ATTR_VOLUME, AUDIO_VOLUME_DEFAULT),
            )

            await _async_install_audio(audio, resolved_devices)
        except HGSmartAudioError as err:
            raise HomeAssistantError(str(err)) from err
        finally:
            await hass.async_add_executor_job(shutil.rmtree, temp_dir, True)

    async def handle_mute_meal_call_service(call: ServiceCall) -> None:
        """Install the shortest valid zero-volume meal-call sound."""
        resolved_devices = _resolve_audio_devices(hass, call)
        try:
            await _async_install_audio(SILENT_WAV, resolved_devices)
        except HGSmartAudioError as err:
            raise HomeAssistantError(str(err)) from err

    if not hass.services.has_service(DOMAIN, SERVICE_FEED):
        hass.services.async_register(
            DOMAIN,
            SERVICE_FEED,
            handle_feed_service,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_UPLOAD_SOUND):
        hass.services.async_register(
            DOMAIN,
            SERVICE_UPLOAD_SOUND,
            handle_upload_sound_service,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_MUTE_MEAL_CALL):
        hass.services.async_register(
            DOMAIN,
            SERVICE_MUTE_MEAL_CALL,
            handle_mute_meal_call_service,
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
        if entry_data["coordinator"].schedule_offsets is not None:
            await entry_data["coordinator"].schedule_offsets.async_stop()
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


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove saved timezone compensation state with the config entry."""
    await schedule_offset_store(hass, entry.entry_id).async_remove()
