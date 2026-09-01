import ssl
import json
import logging
import aiohttp
import paho.mqtt.client as mqtt
import pyeconet.api
from pyeconet.api import HEADERS, REST_URL, CLEAR_BLADE_SYSTEM_KEY, HOST
from pyeconet.errors import GenericHTTPError, InvalidResponseFormat

_LOGGER = logging.getLogger(__name__)

# Non-blocking permissive SSL context to bypass distrusted ClearBlade DigiCert G1 root
try:
    _ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    _ctx.check_hostname = False
    _ctx.verify_mode = ssl.CERT_NONE
    pyeconet.api._SSL_CONTEXT = _ctx

    # Patch _get_location and get_dynamic_action to always pass ssl=_ctx
    async def _patched_get_location(self):
        _headers = HEADERS.copy()
        _headers["ClearBlade-UserToken"] = self._user_token
        payload = {"resource": "friedrich"}
        async with aiohttp.request(
            'POST',
            f"{REST_URL}/code/{CLEAR_BLADE_SYSTEM_KEY}/getUserDataForApp",
            ssl=_ctx,
            json=payload,
            headers=_headers
        ) as resp:
            if resp.status == 200:
                _json = await resp.json()
                if _json.get("success"):
                    self._locations = _json["results"]["locations"]
                    return self._locations
                raise InvalidResponseFormat()
            raise GenericHTTPError(resp.status)

    async def _patched_get_dynamic_action(self, payload: dict) -> dict:
        _headers = HEADERS.copy()
        _headers["ClearBlade-UserToken"] = self._user_token
        async with aiohttp.request(
            'POST',
            f"{REST_URL}/code/{CLEAR_BLADE_SYSTEM_KEY}/dynamicAction",
            ssl=_ctx,
            json=payload,
            headers=_headers,
        ) as resp:
            if resp.status == 200:
                _json = await resp.json()
                if _json.get("success"):
                    return _json
                raise InvalidResponseFormat()
            raise GenericHTTPError(resp.status)

    def _patched_subscribe(self):
        if not self._equipment:
            _LOGGER.error("Equipment list is empty, did you call get_equipment before subscribing?")
            return False
        self._mqtt_client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION1,
            client_id=self._get_client_id(),
            clean_session=True,
            userdata=None,
            protocol=mqtt.MQTTv311,
        )
        self._mqtt_client.username_pw_set(
            self._user_token, password=CLEAR_BLADE_SYSTEM_KEY
        )
        self._mqtt_client.enable_logger()
        self._mqtt_client.tls_set_context(_ctx)
        self._mqtt_client.tls_insecure_set(True)
        self._mqtt_client.on_connect = self._on_connect
        self._mqtt_client.on_message = self._on_message
        self._mqtt_client.on_disconnect = self._on_disconnect
        self._mqtt_client.connect_async(HOST, 1884, 60)
        self._mqtt_client.loop_start()

    pyeconet.api.EcoNetApiInterface._get_location = _patched_get_location
    pyeconet.api.EcoNetApiInterface.get_dynamic_action = _patched_get_dynamic_action
    pyeconet.api.EcoNetApiInterface.subscribe = _patched_subscribe
except Exception as e:
    _LOGGER.error("Failed to apply pyeconet SSL/MQTT patches: %s", e)

"""Support for EcoNet products."""

import asyncio
from datetime import timedelta

from aiohttp.client_exceptions import ClientError
from pyeconet import EcoNetApiInterface
from pyeconet.equipment import Equipment, EquipmentType
from pyeconet.errors import (
    GenericHTTPError,
    InvalidCredentialsError,
    InvalidResponseFormat,
    PyeconetError,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.dispatcher import dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .const import PUSH_UPDATE

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
]

INTERVAL = timedelta(seconds=30)


type EconetConfigEntry = ConfigEntry[dict[EquipmentType, list[Equipment]]]


async def async_setup_entry(
    hass: HomeAssistant, config_entry: EconetConfigEntry
) -> bool:
    """Set up EcoNet as config entry."""

    email = config_entry.data[CONF_EMAIL]
    password = config_entry.data[CONF_PASSWORD]

    try:
        api = await EcoNetApiInterface.login(email, password=password)
    except InvalidCredentialsError:
        _LOGGER.error("Invalid credentials provided")
        return False
    except PyeconetError as err:
        _LOGGER.error("Config entry failed: %s", err)
        raise ConfigEntryNotReady from err

    try:
        equipment = await api.get_equipment_by_type(
            [EquipmentType.WATER_HEATER, EquipmentType.THERMOSTAT]
        )
    except (ClientError, GenericHTTPError, InvalidResponseFormat) as err:
        raise ConfigEntryNotReady from err

    config_entry.runtime_data = equipment

    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)

    api.subscribe()

    def update_published():
        """Handle a push update."""
        dispatcher_send(hass, PUSH_UPDATE)

    for _eqip in equipment[EquipmentType.WATER_HEATER]:
        _eqip.set_update_callback(update_published)

    for _eqip in equipment[EquipmentType.THERMOSTAT]:
        _eqip.set_update_callback(update_published)

    async def resubscribe(now):
        """Resubscribe and refresh device values."""
        try:
            await api.refresh_equipment()
            dispatcher_send(hass, PUSH_UPDATE)
        except Exception as err:
            _LOGGER.warning("EcoNet periodic state refresh failed: %s", err)
            try:
                await hass.async_add_executor_job(api.unsubscribe)
                api.subscribe()
            except Exception:
                pass

    config_entry.async_on_unload(async_track_time_interval(hass, resubscribe, INTERVAL))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: EconetConfigEntry) -> bool:
    """Unload a EcoNet config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
