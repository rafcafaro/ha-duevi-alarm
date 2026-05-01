"""Config flow for Duevi CE-LAN Alarm integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_PIN, DOMAIN
from .nabto_udp import DueviClient

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default="192.168.1.231"): str,
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PIN): str,
        vol.Optional(CONF_PORT, default=5570): int,
    }
)

async def validate_input(hass: HomeAssistant, data: dict[str, Any]) -> dict[str, Any]:
    """Validate the user input allows us to connect."""
    client = DueviClient(
        host=data[CONF_HOST],
        email=data[CONF_EMAIL],
        pin=data[CONF_PIN],
        port=data.get(CONF_PORT, 5570),
    )

    result = await hass.async_add_executor_job(client.connect)
    if not result:
        raise CannotConnect
        
    await hass.async_add_executor_job(client.disconnect)

    return {"title": f"Duevi Alarm ({data[CONF_HOST]})"}

class DueviConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Duevi CE-LAN Alarm."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                info = await validate_input(self.hass, user_input)
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(user_input[CONF_HOST])
                self._abort_if_unique_id_configured()
                
                return self.async_create_entry(title=info["title"], data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""
