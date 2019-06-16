"""
Support for Friedrich's Windows A/Cs with KWIFI module.
Only KWIFI modules bought after Jan 1, 2018.
configuration.yaml
climate:
  - platform: friedrich
    email: user@example.com
    password: hunter2

Limitations:
- does not support heating features, it's easy to add support for, but need a tester to confirm codes for operation modes
- does not support pulling unit state or live MQTT communication, need documentation on Xively MQTT JWT auth
- Xively session expiration is not well tested
- 10 devices max, requires implementation of pagination for more

TODOs:
- need to add a way to customize attributes?
- fuzzy/case match operation modes et al.
"""
import logging
import json
import voluptuous as vol

from homeassistant.components.climate import (STATE_COOL, STATE_FAN_ONLY, ClimateDevice,
                                              PLATFORM_SCHEMA,
                                              SUPPORT_TARGET_TEMPERATURE, SUPPORT_OPERATION_MODE,
                                              SUPPORT_FAN_MODE)
from homeassistant.const import (CONF_EMAIL, CONF_PASSWORD,
                                 STATE_OFF,
                                 TEMP_FAHRENHEIT, ATTR_TEMPERATURE)
from homeassistant.components.fan import (SPEED_LOW, SPEED_MEDIUM, SPEED_HIGH)
import homeassistant.helpers.config_validation as cv

import requests

_LOGGER = logging.getLogger(__name__)

SPEED_MAX = 'max'

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Required(CONF_EMAIL): cv.string,
    vol.Required(CONF_PASSWORD): cv.string,
})

# see: https://developer.xively.com/docs/using-jwts for expiration etc.
def _xively_jwt_authenticate(email, password, account_id):

    XIVELY_JWT_AUTH_URL = 'https://id.xively.com/api/v1/auth/login-user'

    payload = {
        'emailAddress': email,
        'password': password,
        'accountId': account_id,
    }

    # Using the json parameter in the request will change the Content-Type in the header to application/json.
    req = requests.post(XIVELY_JWT_AUTH_URL, json=payload)

    if req.status_code != requests.codes.ok:
        _LOGGER.error("Error authenticating to Xively, code %d", req.status_code)
        return None

    return req.json()['jwt']

def _xively_list_devices(account_id, jwt):

    FRIEDRICH_LIST_DEVICES = 'https://blueprint.xively.com/api/v1/devices'

    headers = {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + jwt
    }

    req = requests.get(FRIEDRICH_LIST_DEVICES, headers=headers, params={'accountId': account_id})

    if req.status_code != requests.codes.ok:
        _LOGGER.error("Error listing Xively devices, code %d", req.status_code)
        return None

    return req.json()['devices']['results']

FRIEDRICH_XIVELY_ACCOUNT_ID = '5c474e0f-d563-4947-a3f1-b517138e9961'
FRIEDRICH_TEMP_TIMESERIE = 'https://timeseries.xively.com/api/v4/data/xi/blue/v1/{0}/d/{1}/temp/latest'
FRIEDRICH_PUBLISH_COMMAND = 'https://friedrich.broker.xively.com/messaging/publish/xi/blue/v1/{0}/d/{1}/command'

# pylint: disable=unused-argument
def setup_platform(hass, config, add_devices, discovery_info=None):
    """Setup the friedrich platform."""
    # import requirements for connection

    # Assign configuration variables.
    email = config.get(CONF_EMAIL)
    password = config.get(CONF_PASSWORD)

    # Authenticate to Xively
    jwt = _xively_jwt_authenticate(email, password, FRIEDRICH_XIVELY_ACCOUNT_ID)
    if jwt is None:
        _LOGGER.error("Could not connect to Friedrich account")
        return

    # Fetch devices
    devices = _xively_list_devices(FRIEDRICH_XIVELY_ACCOUNT_ID, jwt)
    if devices is None:
        _LOGGER.error("Could not list Friedrich devices")
        return

    # Add devices
    add_devices(FriedrichHVAC(device['name'], jwt, device['id']) for device in devices)

# pylint: disable=abstract-method
# pylint: disable=too-many-instance-attributes
class FriedrichHVAC(ClimateDevice):
    """Representation of a Friedrich HVAC unit."""

    def __init__(self, name, jwt, device_id):
        """Initialize a Friedrich HVAC unit."""

        # initialization attributes
        self._name = name
        self._jwt = jwt
        self._device_id = device_id

        # this attribute can be obtained from the Xively API
        self._current_temperature = None

        # these attributes cannot be obtained and have to be assumed (=start defaults)
        self._target_temperature = 76
        self._current_operation = STATE_OFF
        self._current_fan_mode = SPEED_LOW

        # fixed settings
        self._operation_list = [STATE_OFF, STATE_COOL, STATE_FAN_ONLY]
        self._fan_list = [SPEED_LOW, SPEED_MEDIUM, SPEED_HIGH, SPEED_MAX]
        self._unit_of_measurement = TEMP_FAHRENHEIT
        self._target_temperature_step = 1

    @property
    def should_poll(self):
        """Polling needed for room temperature reading."""
        return True

    def update(self):
        """Read the room temperature from Fridrich HVAC unit."""
        headers = {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer {0}'.format(self._jwt)
        }
        req = requests.get(FRIEDRICH_TEMP_TIMESERIE.format(FRIEDRICH_XIVELY_ACCOUNT_ID, self._device_id), headers=headers)

        if req.status_code != requests.codes.ok:
            _LOGGER.error("Error obtaining Fridrich HVAC unit ambiant temperature reading, code %d", req.status_code)
            return

        self._current_temperature = req.json()['numericValue']

    def _command(self, target_temperature=None, operation_mode=None, fan_mode=None):
        """Device publish command method which simultaneously update all commands' state"""
        COMMAND_OPERATION = {
            STATE_OFF: 5,
            STATE_COOL: 1,
            STATE_FAN_ONLY: 3,
        }

        COMMAND_FAN_MODE = {
            SPEED_LOW: 0,
            SPEED_MEDIUM: 1,
            SPEED_HIGH: 2,
            SPEED_MAX: 3,
        }

        target_temperature = target_temperature or self._target_temperature
        operation_mode = operation_mode or self._current_operation
        fan_mode = fan_mode or self._current_fan_mode

        headers = {
            'Authorization': 'Bearer ' + self._jwt
        }
        payload = {
            'csp': target_temperature,
            'hsp': target_temperature - 8, # heat set point
            'asp': target_temperature - 4, # auto mode set point
            'mode': COMMAND_OPERATION[operation_mode],
            'fanMode': 0, # 0=auto operation, 1=continuous operation
            'fanSpeed': COMMAND_FAN_MODE[fan_mode], # the device also has an auto speed in Cool mode, unknown code
        }
        req = requests.post(FRIEDRICH_PUBLISH_COMMAND.format(FRIEDRICH_XIVELY_ACCOUNT_ID, self._device_id),
                            headers=headers,
                            json=payload)

        if req.status_code != requests.codes.ok:
            _LOGGER.error("Error publishing Fridrich HVAC unit command, code %d", req.status_code)
            return None

        return True

    @property
    def assumed_state(self):
        return True # we are assuming command state attributes at all times as we do not get updates
                    # from the device except for ambiant room temperature

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return SUPPORT_TARGET_TEMPERATURE | SUPPORT_OPERATION_MODE | SUPPORT_FAN_MODE

    @property
    def name(self):
        """Return the name of the thermostat."""
        return self._name

    @property
    def temperature_unit(self):
        """Return the unit of measurement."""
        return self._unit_of_measurement

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._current_temperature

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._target_temperature

    @property
    def current_operation(self):
        """Return the current mode of the HVAC."""
        return self._current_operation

    @property
    def current_fan_mode(self):
        """Return the fan setting."""
        return self._current_fan_mode

    @property
    def operation_list(self):
        """List of available operation modes."""
        return self._operation_list

    @property
    def fan_list(self):
        """Return the list of available fan modes."""
        return self._fan_list

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return self._target_temperature_step

    def set_operation_mode(self, operation_mode):
        """Set Fridrich HVAC operation mode (off, fan only, cool)."""
        if self._command(operation_mode=operation_mode) is None:
            _LOGGER.error("Could not set Friedrich HVAC unit operation mode")
            return

        self._current_operation = operation_mode

    def turn_on(self):
        self.set_operation_mode(STATE_COOL)

    def turn_off(self):
        self.set_operation_mode(STATE_OFF)

    def set_fan_mode(self, fan_mode):
        """Set Friedrich HVAC fan speed mode (auto, low, medium, high, max)."""
        if self._command(fan_mode=fan_mode) is None:
            _LOGGER.error("Could not set Friedrich HVAC unit fan mode")
            return

        self._current_fan_mode = fan_mode

    def set_temperature(self, **kwargs):
        """Set Friedrich HVAC cooling target temperature."""
        temperature = kwargs.get(ATTR_TEMPERATURE)

        if self._command(target_temperature=temperature) is None:
            _LOGGER.error("Could not set Friedrich HVAC target temperature")
            return

        self._target_temperature = temperature
