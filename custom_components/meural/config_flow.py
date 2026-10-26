"""Config flow for Meural integration."""
import logging
from typing import Any, Dict, Optional

import voluptuous as vol

from homeassistant import config_entries, core, exceptions
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN  # pylint:disable=unused-import

from . import pymeural

_LOGGER = logging.getLogger(__name__)

DATA_SCHEMA = vol.Schema({"email": str, "password": str})


async def validate_input(hass: core.HomeAssistant, data: Dict[str, Any]) -> str:
    """Validate the user input allows us to connect.

    Data has the keys from DATA_SCHEMA with values provided by the user.
    """
    session = async_get_clientsession(hass)
    return await pymeural.authenticate(session, data["email"], data["password"])


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Meural."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            try:
                token = await validate_input(self.hass, user_input)

                await self.async_set_unique_id(user_input["email"], raise_on_progress=False)
                return self.async_create_entry(
                    title=user_input["email"],
                    data={
                        "email": user_input["email"],
                        "password": user_input["password"],
                        "token": token,
                    },
                )
            except pymeural.CannotConnect:
                errors["base"] = "cannot_connect"
                raise
            except pymeural.InvalidAuth:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user", data_schema=DATA_SCHEMA, errors=errors
        )

    async def async_step_reconfigure(self, user_input=None):
        """Handle reconfiguration of Meural credentials."""
        entry = self._get_reconfigure_entry()
        errors = {}

        if user_input is not None:
            email = user_input.get("email", entry.data.get("email"))
            password = user_input.get("password")
            data = {"email": email, "password": password}
            try:
                token = await validate_input(self.hass, data)
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        **entry.data,
                        "email": email,
                        "password": password,
                        "token": token,
                    },
                )
            except pymeural.CannotConnect:
                errors["base"] = "cannot_connect"
            except (pymeural.InvalidAuth, pymeural.PasswordResetRequired):
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during reconfigure")
                errors["base"] = "unknown"

        schema = vol.Schema(
            {
                vol.Required("email", default=entry.data.get("email", "")): str,
                vol.Required("password"): str,
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data):
        """Perform reauth upon an authentication error."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Dialog that informs the user that reauth is required."""
        entry = self._get_reauth_entry()
        errors = {}

        if user_input is not None:
            email = entry.data.get("email", "")
            password = user_input.get("password")
            data = {"email": email, "password": password}
            try:
                token = await validate_input(self.hass, data)
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        **entry.data,
                        "password": password,
                        "token": token,
                    },
                )
            except pymeural.CannotConnect:
                errors["base"] = "cannot_connect"
            except (pymeural.InvalidAuth, pymeural.PasswordResetRequired):
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during reauth")
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required("password"): str}),
            description_placeholders={"email": entry.data.get("email", "")},
            errors=errors,
        )
