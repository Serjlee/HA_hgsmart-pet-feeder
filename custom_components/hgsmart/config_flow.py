"""Config flow for HGSmart Pet Feeder integration."""
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult

from .api import HGSmartApiClient
from homeassistant.helpers.selector import AreaSelector, AreaSelectorConfig
from .const import DOMAIN, CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL, CONF_DEVICE_ROOMS

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): int,
    }
)


class HGSmartConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HGSmart Pet Feeder."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self.login_data: dict[str, Any] | None = None
        self.discovered_devices: list[dict[str, Any]] = []

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]
            update_interval = user_input[CONF_UPDATE_INTERVAL]

            # Test credentials
            api = HGSmartApiClient(username, password)
            
            try:
                if await api.login():
                    # Get devices to verify connection
                    devices = await api.get_devices()

                    if devices:
                        # Create unique ID from username
                        await self.async_set_unique_id(username.lower())
                        self._abort_if_unique_id_configured()

                        # Save data for next step
                        self.login_data = user_input
                        self.discovered_devices = devices
                        return await self.async_step_rooms()
                    else:
                        errors["base"] = "no_devices"
                else:
                    errors["base"] = "invalid_auth"
            except aiohttp.ClientError:
                _LOGGER.exception("Connection error during login")
                errors["base"] = "cannot_connect"
            except TimeoutError:
                _LOGGER.exception("Timeout during login")
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during login")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_rooms(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the room assignment step."""
        if user_input is not None:
            # Create the entry with room data
            data = self.login_data.copy()
            data[CONF_DEVICE_ROOMS] = user_input
            
            username = data[CONF_USERNAME]
            return self.async_create_entry(
                title=f"HGSmart ({username})",
                data=data,
            )

        # Build schema for rooms
        schema_dict = {}
        for device in self.discovered_devices:
            device_id = device["deviceId"]
            name = device.get("name", f"Device {device_id}")
            # Use device ID as key, AreaSelector as value
            schema_dict[vol.Optional(device_id)] = AreaSelector(
                AreaSelectorConfig(multiple=False)
            )

        # If no devices (shouldn't happen here), just create entry
        if not schema_dict:
             return self.async_create_entry(
                title=f"HGSmart ({self.login_data[CONF_USERNAME]})",
                data=self.login_data,
            )

        return self.async_show_form(
            step_id="rooms",
            data_schema=vol.Schema(schema_dict),
            description_placeholders={"device_count": str(len(self.discovered_devices))},
        )
