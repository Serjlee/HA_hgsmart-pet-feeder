"""Config flow for HGSmart Pet Feeder integration."""
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult

from .api import HGSmartApiClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class HGSmartConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for HGSmart Pet Feeder."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            password = user_input[CONF_PASSWORD]

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

                        return self.async_create_entry(
                            title=f"HGSmart ({username})",
                            data={
                                CONF_USERNAME: username,
                                CONF_PASSWORD: password,
                            },
                        )
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
