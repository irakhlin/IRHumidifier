from __future__ import annotations

from abc import ABC
from datetime import timedelta
from typing import Any
import attr

import asyncio
import json
import logging
import os.path

from homeassistant.components.demo import humidifier
from homeassistant.core import HomeAssistant, ServiceCall, callback, State
from homeassistant.helpers.typing import (
    ConfigType,
    DiscoveryInfoType,
    HomeAssistantType,
    ServiceDataType,
)
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
    MODE_COMFORT,
)

from homeassistant.helpers.dispatcher import (
    async_dispatcher_send,
    async_dispatcher_connect,
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
    device_registry,
    service,
    entity,
)
from homeassistant.helpers.restore_state import RestoreEntity
from .controller import get_controller
from . import COMPONENT_ABS_DIR, Helper

from .const import (
    DEFAULT_HUMIDITY,
    DEFAULT_MANUAL_SPEED,
    HUMIDIFIER_FUNCTIONS,
    COMMAND_WARM_MIST,
    COMMAND_UV,
    COMMAND_LIGHT,
    COMMAND_DECREASE,
    COMMAND_INCREASE,
    COMMAND_NIGHT_MODE,
    CURRENT_SPEED,
    DOMAIN,
    DEFAULT_DELAY,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "irhumidifier"
DEFAULT_MODE = "normal"

SERVICE_TOGGLE_FUNCTION = "toggle_function"
SERVICE_INCREASE = "increase"
SERVICE_DECREASE = "decrease"
SERVICE_SET_SPEED = "set_speed"
SERVICE_SYNC_STATE = "sync_state"
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
    hass: HomeAssistantType,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
):

    device_code = config.get(CONF_DEVICE_CODE)
    device_files_absdir = os.path.join(COMPONENT_ABS_DIR, "codes")

    if not os.path.isdir(device_files_absdir):
        os.makedirs(device_files_absdir)

    device_json_filename = str(device_code) + ".json"
    device_json_path = os.path.join(device_files_absdir, device_json_filename)

    if not os.path.exists(device_json_path):
        _LOGGER.warning(
            "Couldn't find the device Json file. The component will "
            "try to download it from the GitHub repo"
        )

        try:
            codes_source = (
                "https://raw.githubusercontent.com/"
                "irakhlin/IRHumidifier/main/"
                "codes/{}.json"
            )

            await Helper.downloader(codes_source.format(device_code), device_json_path)
        except Exception:
            _LOGGER.error(
                "There was an error while downloading the device Json file. "
                "Please check your internet connection or if the device code "
                "exists on GitHub. If the problem still exists please "
                "place the file manually in the proper directory."
            )
            return

    with open(device_json_path) as j:
        try:
            device_data = json.load(j)
        except Exception:
            _LOGGER.error("The device Json file is invalid")
            return

    _LOGGER.info("Device json file has been loaded from: {device_json_path}")

    async_add_entities([IRHumidifier(hass, config, device_data)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        SERVICE_TOGGLE_FUNCTION,
        {vol.Required("function"): cv.string},
        "_async_toggle_function",
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
    platform.async_register_entity_service(
        SERVICE_SYNC_STATE,
        {vol.Required("state"): cv.string},
        "async_sync_state",
    )


class IRHumidifier(HumidifierEntity, RestoreEntity, ABC):
    def __init__(self, hass: HomeAssistantType, config: ConfigType, device_data):
        self.hass = hass
        self._attr_unique_id = config.get(CONF_UNIQUE_ID)
        self._attr_name = config.get(CONF_NAME)
        self._attr_min_humidity = device_data["minHumidity"]
        self._attr_max_humidity = device_data["maxHumidity"]
        self._attr_supported_features = SUPPORTED_FEATURES
        self._attr_available_modes = list(device_data["operationModes"])
        self._attr_target_humidity = DEFAULT_HUMIDITY
        self._attr_mode = MODE_NORMAL
        self._state = STATE_OFF
        self._device_type = device_data["type"]
        if self._device_type == "humidifier":
            self._attr_device_class = HumidifierDeviceClass.HUMIDIFIER
        if self._device_type == "dehumidifier":
            self._attr_device_class = HumidifierDeviceClass.DEHUMIDIFIER

        self._device_code = config.get(CONF_DEVICE_CODE)
        self._controller_data = config.get(CONF_CONTROLLER_DATA)
        self._delay: float = config.get(CONF_DELAY)
        self._manufacturer = device_data["manufacturer"]
        self._supported_models = device_data["supportedModels"]
        self._supported_controller = device_data["supportedController"]
        self._commands_encoding = device_data["commandsEncoding"]
        self._min_speed = int(device_data["minManualSpeed"])
        self._max_speed = int(device_data["maxManualSpeed"])
        self._default_speed = int(device_data["defaultManualSpeed"])

        self._supported_extra_functions = [
            x for x in device_data["extraFunctions"] if x in HUMIDIFIER_FUNCTIONS
        ]

        self._commands = device_data["commands"]

        self._attr_extra_state_attributes = {
            "manufacturer": self._manufacturer,
            "model": self._supported_models,
            "default_humidity": DEFAULT_HUMIDITY,
            "default_mode": DEFAULT_MODE,
            "default_speed": DEFAULT_MANUAL_SPEED,
            "supported_functions": self._supported_extra_functions,
            "device_code": self._device_code,
            "supported_controller": self._supported_controller,
            "min_speed": self._min_speed,
            "max_speed": self._max_speed,
            CURRENT_SPEED: DEFAULT_MANUAL_SPEED,
            COMMAND_UV: STATE_OFF,
            COMMAND_NIGHT_MODE: STATE_OFF,
            COMMAND_LIGHT: STATE_OFF,
            COMMAND_WARM_MIST: STATE_OFF,
        }

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

        if last_state is not None:
            self._state = last_state.state == STATE_ON
            if last_state.state == STATE_ON:
                self._attr_mode = last_state.attributes.get("mode")
                self._attr_target_humidity = last_state.attributes.get("humidity")
                self.manaual_speed = last_state.attributes.get("current_speed")
                self.uv = last_state.attributes.get(COMMAND_UV)
                self.night = last_state.attributes.get(COMMAND_NIGHT_MODE)
                self.light = last_state.attributes.get(COMMAND_LIGHT)
                self.warm = last_state.attributes.get(COMMAND_WARM_MIST)
            else:
                self._attr_mode = DEFAULT_MODE
                self._attr_target_humidity = DEFAULT_HUMIDITY
                self.manaual_speed = DEFAULT_MANUAL_SPEED
                self.uv = STATE_OFF
                self.night = STATE_OFF
                self.light = STATE_OFF
                self.warm = STATE_OFF
            self._attr_extra_state_attributes = {
                "manufacturer": last_state.attributes.get("manufacturer"),
                "model": last_state.attributes.get("model"),
                "default_humidity": last_state.attributes.get("default_humidity"),
                "default_mode": last_state.attributes.get("default_mode"),
                "default_speed": last_state.attributes.get("default_speed"),
                "supported_functions": last_state.attributes.get("supported_functions"),
                "allowed_commands": last_state.attributes.get("allowed_commands"),
                "device_code": last_state.attributes.get("device_code"),
                "supported_controller": last_state.attributes.get(
                    "supported_controller"
                ),
                "min_speed": last_state.attributes.get("min_speed"),
                "max_speed": last_state.attributes.get("max_speed"),
                CURRENT_SPEED: self.manaual_speed,
                COMMAND_UV: self.uv,
                COMMAND_NIGHT_MODE: self.night,
                COMMAND_LIGHT: self.light,
                COMMAND_WARM_MIST: self.warm,
            }

    @property
    def mode(self):
        return self._attr_mode

    @property
    def target_humidity(self):
        return self._attr_target_humidity

    @property
    def is_on(self):
        return self._state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._attr_extra_state_attributes

    async def async_set_mode(self, mode: str):
        """Set new target preset mode."""
        if self._state is False:
            await self.async_update_ha_state(True)
            return
        self._attr_mode = mode

        await self.async_send_command(mode)

        if mode == MODE_BABY:
            self._attr_extra_state_attributes.update(
                {COMMAND_WARM_MIST: STATE_ON, COMMAND_UV: STATE_ON}
            )
            self._attr_target_humidity = 55

        if mode == MODE_COMFORT:
            self._attr_target_humidity = 45

        await self.async_update_ha_state()

    async def async_set_humidity(self, humidity: int):
        """Set new target humidity."""

        current_mode = self._attr_mode
        if self._state is False:
            await self.async_update_ha_state(True)
            return

        commands = []
        if current_mode != MODE_AUTO:
            commands.append(MODE_AUTO)

        previous_humidity = self._attr_target_humidity
        self._attr_target_humidity = humidity

        if previous_humidity > humidity:
            delta = previous_humidity - humidity
            send_events = int(delta / 10)
            commands += [COMMAND_DECREASE] * send_events
            commands += [COMMAND_DECREASE]

        if previous_humidity < humidity:
            delta = humidity - previous_humidity
            send_events = int(delta / 10)
            commands += [COMMAND_INCREASE] * send_events
            commands += [COMMAND_INCREASE]

        await self.async_send_commands(commands)
        await self.async_update_ha_state()

    async def async_turn_off(self, **kwargs):
        """Turn the device off."""
        _LOGGER.warning("Power off has been called")
        self._state = False
        await self._reset_state()
        await self.async_send_command(STATE_OFF)
        await self.async_update_ha_state()

    async def async_turn_on(self, **kwargs):
        """Turn the device on."""
        _LOGGER.warning("Power on has been called")
        await self._reset_state()
        self._state = True
        await self.async_send_command(STATE_ON)
        await self.async_update_ha_state()

    async def async_send_commands(self, commands: list[str]):
        async with self._temp_lock:
            try:
                for command in commands:
                    await self._controller.send(self._commands[command])
                    await asyncio.sleep(self._delay)
            except Exception as e:
                _LOGGER.exception(e)

    async def async_send_command(self, command: str):
        async with self._temp_lock:
            try:
                await self._controller.send(self._commands[command.lower()])
                _LOGGER.warning("sending command: %s", command)
                await asyncio.sleep(self._delay)
            except Exception as e:
                _LOGGER.exception(e)

    async def _async_toggle_function(self, function: str) -> None:
        self.hass.async_create_task(self.async_toggle_function(function))

    async def async_toggle_function(self, function: str) -> None:
        night_mode_status = self._attr_extra_state_attributes[COMMAND_NIGHT_MODE]

        if self._state is False:
            return

        if night_mode_status == STATE_ON and function == COMMAND_LIGHT:
            return

        state = self._attr_extra_state_attributes[function]

        if function == COMMAND_NIGHT_MODE:
            self._attr_extra_state_attributes.update(
                {function: COMMAND_NIGHT_MODE, COMMAND_LIGHT: STATE_OFF}
            )
        else:
            self._attr_extra_state_attributes.update(
                {function: STATE_ON if state == STATE_OFF else STATE_ON}
            )
        await self.async_send_command(function)
        await self.async_update_ha_state()

    async def _async_increase(self):
        self.hass.async_create_task(self.async_toggle_function("increase"))

    async def async_increase(self):
        current_mode = self._attr_mode
        if current_mode in [MODE_BABY, MODE_COMFORT] or self._state is False:
            return

        if current_mode == MODE_NORMAL:
            self._attr_extra_state_attributes[CURRENT_SPEED] += 1

        if current_mode == MODE_AUTO:
            self._attr_target_humidity += 10

        await self.async_send_command("increase")
        await self.async_update_ha_state()

    async def _async_decrease(self):
        self.hass.async_create_task(self.async_toggle_function("decrease"))

    async def async_decrease(self):
        current_mode = self._attr_mode
        if current_mode in [MODE_BABY, MODE_COMFORT] or self._state is False:
            return

        if current_mode == MODE_NORMAL:
            self._attr_extra_state_attributes[CURRENT_SPEED] -= 1

        if current_mode == MODE_AUTO:
            self._attr_target_humidity -= 10

        await self.async_send_command("decrease")
        await self.async_update_ha_state()

    async def _async_set_speed(self, speed: int):
        self.hass.async_create_task(self.async_set_speed(int))

    async def async_set_speed(self, speed: int):
        """Set new target humidity."""

        current_mode = self._attr_mode
        if self._state is False:
            await self.async_update_ha_state(True)
            return

        commands = []
        if current_mode != MODE_NORMAL:
            commands.append(MODE_NORMAL)

        previous_speed = self._attr_extra_state_attributes[CURRENT_SPEED]
        self._attr_extra_state_attributes[CURRENT_SPEED] = speed

        if previous_speed > speed:
            delta = previous_speed - speed
            commands += [COMMAND_DECREASE] * delta

        if previous_speed < speed:
            delta = speed - previous_speed
            commands += [COMMAND_INCREASE] * delta

        await self.async_send_commands(commands)
        await self.async_update_ha_state()

    async def async_sync_state(self, state: str):
        self.hass.async_create_task(self._async_sync_state(state))

    async def _async_sync_state(self, state: str):
        self._state = state
        await self.async_update_ha_state()

    async def _reset_state(self):
        self._attr_target_humidity = DEFAULT_HUMIDITY
        self._attr_extra_state_attributes.update(
            {
                CURRENT_SPEED: DEFAULT_MANUAL_SPEED,
                COMMAND_UV: STATE_OFF,
                COMMAND_NIGHT_MODE: STATE_OFF,
                COMMAND_LIGHT: STATE_OFF,
                COMMAND_WARM_MIST: STATE_OFF,
            }
        )
        self._attr_mode = DEFAULT_MODE
