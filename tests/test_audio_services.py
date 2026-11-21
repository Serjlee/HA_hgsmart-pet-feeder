"""Exercise the audio services through Home Assistant."""

from unittest.mock import AsyncMock, call

import pytest
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from custom_components.hgsmart.audio import HGSmartAudioError, SILENT_WAV
from custom_components.hgsmart.const import DOMAIN

from .common import ENTRY_ID, FEEDER


@pytest.mark.parametrize("confirmed", [True, False])
async def test_mute_service_activates_confirmed_transfers_and_refreshes_once(
    hass, cloud, setup_integration, monkeypatch, confirmed
):
    endpoints = {FEEDER: "127.0.0.1", "second": "127.0.0.2"}
    cloud.attributes[FEEDER]["localIp"] = endpoints[FEEDER]
    cloud.add_device("second", "S30D", localIp=endpoints["second"])
    api = cloud.api
    api.upload_voice_file.return_value = "https://example.invalid/custom_voice.wav"
    api.set_custom_voice_url.return_value = True
    api.prepare_custom_voice_transfer.return_value = True
    api.finish_custom_voice_transfer.return_value = True
    api.activate_custom_voice.return_value = True

    async def send_audio(endpoint, audio, *, finalize_transfer):
        assert endpoint in endpoints.values()
        assert audio == SILENT_WAV
        assert await finalize_transfer()
        if endpoint == endpoints["second"] and not confirmed:
            raise HGSmartAudioError("The feeder did not confirm reception")

    monkeypatch.setattr("custom_components.hgsmart.async_send_audio", send_audio)
    coordinator = await setup_integration()
    api.upload_voice_file.assert_not_awaited()

    registry = dr.async_get(hass)
    devices = [
        registry.async_get_device_by_identifier((DOMAIN, cloud_id), ENTRY_ID)
        for cloud_id in endpoints
    ]
    assert all(device is not None for device in devices)
    refresh = AsyncMock()
    monkeypatch.setattr(coordinator, "async_request_refresh", refresh)
    service_data = {"device_id": [device.id for device in devices if device is not None]}
    if confirmed:
        await hass.services.async_call(DOMAIN, "mute_meal_call", service_data, blocking=True)
        assert api.activate_custom_voice.await_args_list == [call(FEEDER), call("second")]
    else:
        with pytest.raises(HomeAssistantError, match="did not confirm reception"):
            await hass.services.async_call(DOMAIN, "mute_meal_call", service_data, blocking=True)
        api.activate_custom_voice.assert_awaited_once_with(FEEDER)

    assert api.upload_voice_file.await_args_list == [call(SILENT_WAV), call(SILENT_WAV)]
    assert api.prepare_custom_voice_transfer.await_args_list == [
        call(FEEDER), call("second")
    ]
    api.finish_custom_voice_transfer.assert_any_await(FEEDER)
    api.finish_custom_voice_transfer.assert_any_await("second")
    refresh.assert_awaited_once_with()
