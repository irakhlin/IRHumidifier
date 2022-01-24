import logging
from homeassistant.const import Platform

_LOGGER = logging.getLogger(__package__)

DOMAIN = "ir-humidifier"

PLATFORMS = [
    Platform.HUMIDIFIER,
]

MANUFACTURER = "ir-humidifier"

MIN_HUMIDITY = 30
MAX_HUMIDITY = 100
DEFAULT_HUMIDITY = 30

MIN_MANUAL_SPEED = 1
MAX_MANUAL_SPEED = 7
DEFAULT_MANUAL_SPEED = 4

CURRENT_SPEED = "current_speed"
DEFAULT_DELAY = 0.5

COMMAND_INCREASE = "increase"
COMMAND_DECREASE = "decrease"
COMMAND_NIGHT_MODE = "night_mode"
COMMAND_WARM_MIST = "warm_mist"
COMMAND_UV = "uv_mode"
COMMAND_LIGHT = "light_mode"

HUMIDIFIER_FUNCTIONS = [
    COMMAND_NIGHT_MODE,
    COMMAND_WARM_MIST,
    COMMAND_UV,
    COMMAND_LIGHT,
]
