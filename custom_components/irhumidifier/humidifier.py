from __future__ import annotations
from datetime import timedelta

import asyncio
import json
import logging
import os.path

from homeassistant.components.demo import humidifier
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.helpers.entity_platform import AddEntitiesCallback

import voluptuous as vol

from homeassistant.components.humidifier import (
    HumidifierDeviceClass,
    HumidifierEntity,
    PLATFORM_SCHEMA,
)
from homeassistant.components.humidifier.const import (
    ATTR_HUMIDITY,
    DEFAULT_MAX_HUMIDITY,
    DEFAULT_MIN_HUMIDITY,
    SUPPORT_MODES,
    MODE_BABY,
    MODE_AUTO,
    MODE_NORMAL,
)

from homeassistant.const import (
    CONF_NAME,
    STATE_ON,
    STATE_OFF,
    STATE_UNKNOWN,
    STATE_UNAVAILABLE,
)
from homeassistant.helpers import (
    config_validation as cv,
    entity_platform,
    service,
    entity,
)
from homeassistant.helpers.restore_state import RestoreEntity
from .controller import get_controller
from . import COMPONENT_ABS_DIR
from .const import (
    DEFAULT_HUMIDITY,
    DEFAULT_MANUAL_SPEED,
    MIN_MANUAL_SPEED,
    MAX_MANUAL_SPEED,
    HUMIDIFIER_FUNCTIONS,
    COMMAND_WARM_MIST,
    COMMAND_UV,
    COMMAND_LIGHT,
    COMMAND_DECREASE,
    COMMAND_INCREASE,
    COMMAND_NIGHT_MODE,
    DOMAIN
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "irhumidifier"
DEFAULT_DELAY = 0.5
DEFAULT_MODE = "normal"

SERVICE_TOGGLE_FUNCTION = "toggle_function"
SERVICE_INCREASE = "increase"
SERVICE_DECREASE = "decrease"
SERVICE_SET_SPEED = "set_speed"

CONF_UNIQUE_ID = "unique_id"
CONF_DEVICE_CODE = "device_code"
CONF_CONTROLLER_DATA = "controller_data"
CONF_DELAY = "delay"
SUPPORTED_FEATURES = SUPPORT_MODES

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_UNIQUE_ID): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Required(CONF_DEVICE_CODE): cv.positive_int,
        vol.Required(CONF_CONTROLLER_DATA): cv.string,
        vol.Optional(CONF_DELAY, default=DEFAULT_DELAY): cv.positive_float,
    }
)
async def async_setup_platform(
    hass,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None):

    
    device_code = config.get(CONF_DEVICE_CODE)
    device_files_absdir = os.path.join(COMPONENT_ABS_DIR, "codes")

    if not os.path.isdir(device_files_absdir):
        os.makedirs(device_files_absdir)

    device_json_filename = str(device_code) + ".json"
    device_json_path = os.path.join(device_files_absdir, device_json_filename)

    if not os.path.exists(device_json_path):
        _LOGGER.warning("Couldn't find the device Json file. The component will " \
                        "try to download it from the GitHub repo.")

        try:
            codes_source = ("https://raw.githubusercontent.com/"
                            "irakhlin/ IRHumidifier/main/"
                            "codes/{}.json")

            await Helper.downloader(codes_source.format(device_code), device_json_path)
        except Exception:
            _LOGGER.error("There was an error while downloading the device Json file. " \
                          "Please check your internet connection or if the device code " \
                          "exists on GitHub. If the problem still exists please " \
                          "place the file manually in the proper directory.")
            return

    with open(device_json_path) as j:
        try:
            device_data = json.load(j)
        except Exception:
            _LOGGER.error("The device Json file is invalid")
            return
    _LOGGER.error(f"Device json file has been loaded from: {device_json_path}")

    async_add_entities([IRHumidifier(hass, config, device_data)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_TOGGLE_FUNCTION,
        {
            vol.Required("function"): cv.string,
        },
        "async_toggle_function",
    )
    platform.async_register_entity_service(
        SERVICE_DECREASE,
        {},
        "async_decrease",
    )
    platform.async_register_entity_service(
        SERVICE_INCREASE,
        {},
        "async_increase",
    )
    platform.async_register_entity_service(
        SERVICE_SET_SPEED,
        {vol.Required("speed"): vol.All(vol.Coerce(int), vol.Range(min=1, max=7))},
        "async_set_speed",
    )
"""
    async def async_setup_entry(hass, config_entry, async_add_entities):
        ir_humidifier = hass.data[DOMAIN][config_entry.entry_id]
        humidifier = set()
        platform = entity_platform.async_get_current_platform()
        platform.async_register_entity_service(
            SERVICE_TOGGLE_FUNCTION,
            {
                vol.Required("function"): cv.string,
            },
            "async_toggle_function",
        )
        platform.async_register_entity_service(
            SERVICE_DECREASE,
            {},
            "async_decrease",
        )
        platform.async_register_entity_service(
            SERVICE_INCREASE,
            {},
            "async_increase",
        )
        platform.async_register_entity_service(
            SERVICE_SET_SPEED,
            {vol.Required("speed"): vol.All(vol.Coerce(int), vol.Range(min=1, max=7))},
            "async_set_speed",
        )

"""
HumidifierEntity
class IRHumidifier(HumidifierEntity, RestoreEntity):
    def set_humidity(self, humidity: int) -> None:
        pass

    def set_mode(self, mode: str) -> None:
        pass

    def __init__(self, hass, config, device_data):
        self.hass = hass
        self._attr_unique_id = config.get(CONF_UNIQUE_ID)
        self._attr_name = config.get(CONF_NAME)
        self._device_code = config.get(CONF_DEVICE_CODE)
        self._controller_data = config.get(CONF_CONTROLLER_DATA)
        self._delay = config.get(CONF_DELAY)

        self._state = None

        self._manufacturer = device_data["manufacturer"]
        self._supported_models = device_data["supportedModels"]
        self._supported_controller = device_data["supportedController"]
        self._commands_encoding = device_data["commandsEncoding"]

        # Default values for resetting after shutting down
        self._min_humidity = device_data["minHumidity"]
        self._max_humidity = device_data["maxHumidity"]
        self._default_humidity = int(device_data["defaultHumidity"])
        self._default_mode = DEFAULT_MODE
        self._min_speed = int(device_data["minManualSpeed"])
        self._max_speed = int(device_data["maxManualSpeed"])
        self._default_speed = int(device_data["defaultManualSpeed"])

        self._supported_extra_functions = [
            x for x in device_data["extraFunctions"] if x in HUMIDIFIER_FUNCTIONS
        ]
        self._device_type = device_data["type"]

        if self._device_type == "humidifier":
            self._attr_device_class = HumidifierDeviceClass.HUMIDIFIER
        if self._device_type == "dehumidifier":
            self._attr_device_class = HumidifierDeviceClass.DEHUMIDIFIER

        self._attr_available_modes = [x for x in device_data["operationModes"]]
        self._attr_target_humidity = self._default_humidity
        self._attr_mode = MODE_NORMAL
        self._attr_speed = DEFAULT_MANUAL_SPEED
        self._attr_is_on = False

        self._attr_is_uv_on = STATE_OFF
        self._attr_is_night_on = STATE_OFF
        self._attr_is_light_on = STATE_OFF
        self._attr_is_warm_mist_on = STATE_OFF

        self._commands = device_data["commands"]

        self._temp_lock = asyncio.Lock()
        self._controller = get_controller(
            self.hass,
            self._supported_controller,
            self._commands_encoding,
            self._controller_data,
            self._delay,
        )

    async def async_added_to_hass(self):
        """Run when entity about to be added."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if not last_state:
            return

        self._state = last_state.state

        if last_state is not None:
            self._attr_is_on = last_state.state
            self._attr_name = last_state.attributes.get("name")
            self._attr_available_modes = last_state.attributes.get("available_modes")
            self._attr_supported_features = last_state.attributes.get(
                "supported_features"
            )

            self._attr_is_on = last_state.attributes.get("is_on")
            self._attr_mode = last_state.attributes["mode"]
            self._attr_target_humidity = last_state.attributes.get("target_humidity")

            self._attr_speed = int(last_state.attributes.get("currents_speed"))
            self._attr_is_uv_on = last_state.attributes.get("uv_mode")
            self._attr_is_night_on = last_state.attributes.get("night_mode")
            self._attr_is_light_on = last_state.attributes.get("light_mode")
            self._attr_is_warm_mist_on = last_state.attributes.get("warm_mist")

    @property
    def currents_speed(self):
        return self._attr_speed

    @property
    def uv_mode(self):
        return self._attr_is_uv_on

    @property
    def night_mode(self):
        return self._attr_is_night_on

    @property
    def light_mode(self):
        return self._attr_is_light_on

    @property
    def warm_mist(self):
        return self._attr_is_warm_mist_on

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._attr_unique_id

    @property
    def name(self):
        """Return the name of the climate device."""
        return self._attr_name

    @property
    def mode(self):
        return self._attr_mode

    @property
    def available_modes(self):
        return self._attr_available_modes

    @property
    def supported_features(self):
        return SUPPORT_MODES

    @property
    def target_humidity(self):
        return self._attr_target_humidity

    @property
    def max_humidity(self):
        return self._max_humidity

    @property
    def min_humidity(self):
        return self._min_humidity

    @property
    def device_class(self):
        return self._attr_device_class

    @property
    def is_on(self) -> bool:
        return self._attr_is_on

    @property
    def extra_state_attributes(self):
        """Platform specific attributes."""
        return {
            "device_code": self._device_code,
            "manufacturer": self._manufacturer,
            "supported_models": self._supported_models,
            "supported_controller": self._supported_controller,
            "commands_encoding": self._commands_encoding,
            "default_humidity": self._default_humidity,
            "default_mode": self._default_mode,
            "default_speed": self._default_speed,
            "supported_functions": self._supported_extra_functions,
            "currents_speed": self._attr_speed,
            "uv_mode": self._attr_is_uv_on,
            "night_mode": self._attr_is_night_on,
            "light_mode": self._attr_is_light_on,
            "warm_mist": self._attr_is_warm_mist_on,
        }

    async def async_set_mode(self, mode):
        """Set new target preset mode."""
        on = self._attr_is_on
        if not on:
            return
        await self.async_send_command(mode)
        self._attr_mode = mode
        await self.async_update_ha_state()

    async def async_set_humidity(self, humidity):
        """Set new target humidity."""
        on = self._attr_is_on
        previous_mode = self._attr_mode
        previous_humidity = self._attr_target_humidity

        if not on:
            return

        command = []

        if previous_mode != MODE_AUTO:
            command.append("auto")

    # if humidity > previous_humidity:

    async def async_turn_off(self, **kwargs):
        """Turn the device off."""
        _LOGGER.error("power off has been called")
        await self.async_send_command("off")
        self._attr_is_on = False
        self._attr_mode = self._default_mode
        self._attr_target_humidity = self._default_humidity
        await self.async_update_ha_state()

    async def async_turn_on(self, **kwargs):
        """Turn the device on."""
        _LOGGER.error("power on has been called")
        await self.async_send_command("on")
        self._attr_is_on = True
        await self.async_update_ha_state()

    async def async_send_command(self, command):
        async with self._temp_lock:
            try:
                is_on = self._attr_is_on
                current_mode = self._attr_mode

                if command.lower() == "on":
                    await self._controller.send(self._commands["on"])
                    await asyncio.sleep(self._delay)
                    return
                if command.lower() == "off":
                    await self._controller.send(self._commands["off"])
                    return

                if current_mode != command:
                    await self._controller.send(self._commands[command])
                    await asyncio.sleep(self._delay)
                    return

            except Exception as e:
                _LOGGER.exception(e)

    async def async_toggle_function(self, function: str) -> None:
        night_mode_status = self._attr_is_night_on
        if function not in self._supported_extra_functions:
            _LOGGER.error(f"{function} is not supported by {self.entity_id}")
            return

        if not self._attr_is_on:
            _LOGGER.warm(
                f"Cannot toggle function on {self.entity_id} when device is off"
            )
            return

        if night_mode_status == STATE_ON and function == "light":
            _LOGGER.error(
                f"Cannot turn on light when night mode is enabled on {self.entity_id}"
            )
            return

        _LOGGER.error(
            f"Printing all state_attrinutes for device: {self.state_attributes}"
        )
