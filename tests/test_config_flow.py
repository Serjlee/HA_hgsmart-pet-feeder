"""Test the config, unsupported-model and reauth flows."""

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.hgsmart.const import DOMAIN

from .common import add_entry

USER_INPUT = {"username": "Test@Example.com", "password": "secret", "update_interval": 10}


async def start_user_flow(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})


async def test_user_flow_creates_entry_without_password(hass, cloud):
    result = await start_user_flow(hass)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "HGSmart (Test@Example.com)"
    assert result["data"] == {
        "username": "Test@Example.com",
        "refresh_token": "refresh-token",
        "update_interval": 10,
    }
    assert result["result"].unique_id == "test@example.com"
    cloud.api.close.assert_awaited()


async def test_user_flow_warns_about_untested_models(hass, cloud):
    cloud.add_device("sf", "SF35", name="Kitchen")
    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["step_id"] == "unsupported_models"
    assert result["description_placeholders"] == {"devices": "- Kitchen (SF35)"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    ("setup", "error"),
    [
        (lambda api: setattr(api.login, "return_value", False), "invalid_auth"),
        (lambda api: setattr(api.get_devices, "side_effect", lambda: []), "no_devices"),
        (lambda api: setattr(api.login, "side_effect", aiohttp.ClientError()), "cannot_connect"),
        (lambda api: setattr(api.login, "side_effect", TimeoutError()), "cannot_connect"),
        (lambda api: setattr(api.login, "side_effect", ValueError()), "unknown"),
    ],
)
async def test_user_flow_errors(hass, cloud, setup, error):
    setup(cloud.api)
    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    cloud.api.close.assert_awaited()


async def test_user_flow_aborts_for_a_configured_account(hass, cloud):
    add_entry(hass)
    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_stores_the_new_refresh_token(hass, cloud):
    entry = add_entry(hass)
    cloud.api.refresh_token = "new-token"
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["username"] == "test@example.com"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "secret"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["refresh_token"] == "new-token"
    assert "password" not in entry.data


async def test_reauth_with_a_wrong_password(hass, cloud):
    entry = add_entry(hass)
    cloud.api.login.return_value = False
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    assert "refresh_token" not in entry.data
