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


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Meural."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    def __init__(self) -> None:
        """Initialize the Meural config flow."""
        super().__init__()
        self._authenticator: Optional[pymeural.NetgearAuthenticator] = None
        self._pending_challenge: Optional[pymeural.PendingChallenge] = None
        self._flow_email: Optional[str] = None
        self._flow_password: Optional[str] = None
        self._flow_entry: Optional[config_entries.ConfigEntry] = None

    async def _async_authenticate(self, email: str, password: str) -> str:
        """Authenticate with Netgear Accounts."""
        session = async_get_clientsession(self.hass)
        self._authenticator = pymeural.NetgearAuthenticator(session)
        self._flow_email = email
        self._flow_password = password
        return await pymeural.authenticate(
            session, email, password, authenticator=self._authenticator
        )

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        errors = {}
        if user_input is not None:
            self._flow_entry = None
            try:
                token = await self._async_authenticate(
                    user_input["email"], user_input["password"]
                )

                await self.async_set_unique_id(user_input["email"], raise_on_progress=False)
                return self.async_create_entry(
                    title=user_input["email"],
                    data={
                        "email": user_input["email"],
                        "password": user_input["password"],
                        "token": token,
                    },
                )
            except pymeural.ChallengeRequired as err:
                self._pending_challenge = err.challenge
                return await self.async_step_mfa()
            except pymeural.CannotConnect:
                errors["base"] = "cannot_connect"
            except (pymeural.InvalidAuth, pymeural.PasswordResetRequired):
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
            self._flow_entry = entry
            try:
                token = await self._async_authenticate(email, password)
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        **entry.data,
                        "email": email,
                        "password": password,
                        "token": token,
                    },
                )
            except pymeural.ChallengeRequired as err:
                self._pending_challenge = err.challenge
                return await self.async_step_mfa()
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
            self._flow_entry = entry
            try:
                token = await self._async_authenticate(email, password)
                return self.async_update_reload_and_abort(
                    entry,
                    data={
                        **entry.data,
                        "password": password,
                        "token": token,
                    },
                )
            except pymeural.ChallengeRequired as err:
                self._pending_challenge = err.challenge
                return await self.async_step_mfa()
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

    async def async_step_mfa(self, user_input=None):
        """Prompt user for OTP/MFA verification code."""
        errors = {}
        if user_input is not None:
            code = user_input.get("code")
            try:
                if not self._authenticator or not self._pending_challenge:
                    raise pymeural.CannotConnect("Authentication session expired")
                auth_result = await self._authenticator.complete_challenge(
                    self._pending_challenge, code
                )
                token = auth_result.access_token
                if self._flow_entry is not None:
                    return self.async_update_reload_and_abort(
                        self._flow_entry,
                        data={
                            **self._flow_entry.data,
                            "email": self._flow_email,
                            "password": self._flow_password,
                            "token": token,
                        },
                    )
                await self.async_set_unique_id(self._flow_email, raise_on_progress=False)
                return self.async_create_entry(
                    title=self._flow_email,
                    data={
                        "email": self._flow_email,
                        "password": self._flow_password,
                        "token": token,
                    },
                )
            except pymeural.InvalidChallenge:
                errors["base"] = "invalid_code"
            except pymeural.CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception completing MFA challenge")
                errors["base"] = "unknown"

        factor = "email"
        if self._pending_challenge and self._pending_challenge.parameters:
            factor = (
                self._pending_challenge.parameters.get("factorName")
                or self._pending_challenge.parameters.get("factorValue")
                or "email"
            )

        return self.async_show_form(
            step_id="mfa",
            data_schema=vol.Schema({vol.Required("code"): str}),
            description_placeholders={"factor": str(factor)},
            errors=errors,
        )
