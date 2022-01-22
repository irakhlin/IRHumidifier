from abc import ABC, abstractmethod
from base64 import b64encode
import binascii
import logging

from homeassistant.const import ATTR_ENTITY_ID
from . import Helper

_LOGGER = logging.getLogger(__name__)

BROADLINK_CONTROLLER = 'Broadlink'

ENC_BASE64 = 'Base64'
ENC_HEX = 'Hex'
ENC_PRONTO = 'Pronto'

BROADLINK_COMMANDS_ENCODING = [ENC_BASE64, ENC_HEX, ENC_PRONTO]


def get_controller(hass, controller, encoding, controller_data, delay):
    """Return a controller compatible with the specification provided."""
    controllers = {
        BROADLINK_CONTROLLER: BroadlinkController
    }
    try:
        return controllers[controller](hass, controller, encoding, controller_data, delay)
    except KeyError:
        raise Exception("The controller is not supported.")


class AbstractController(ABC):
    """Representation of a controller."""

    def __init__(self, hass, controller, encoding, controller_data, delay):
        self.check_encoding(encoding)
        self.hass = hass
        self._controller = controller
        self._encoding = encoding
        self._controller_data = controller_data
        self._delay = delay

    @abstractmethod
    def check_encoding(self, encoding):
        """Check if the encoding is supported by the controller."""
        pass

    @abstractmethod
    async def send(self, command):
        """Send a command."""
        pass


class BroadlinkController(AbstractController):
    """Controls a Broadlink device."""

    def check_encoding(self, encoding):
        """Check if the encoding is supported by the controller."""
        if encoding not in BROADLINK_COMMANDS_ENCODING:
            raise Exception("The encoding is not supported "
                            "by the Broadlink controller.")

    async def send(self, command):
        """Send a command."""
        commands = []

        if not isinstance(command, list):
            command = [command]

        for _command in command:
            if self._encoding == ENC_HEX:
                try:
                    _command = binascii.unhexlify(_command)
                    _command = b64encode(_command).decode('utf-8')
                except:
                    raise Exception("Error while converting "
                                    "Hex to Base64 encoding")

            if self._encoding == ENC_PRONTO:
                try:
                    _command = _command.replace(' ', '')
                    _command = bytearray.fromhex(_command)
                    _command = Helper.pronto2lirc(_command)
                    _command = Helper.lirc2broadlink(_command)
                    _command = b64encode(_command).decode('utf-8')
                except:
                    raise Exception("Error while converting "
                                    "Pronto to Base64 encoding")

            commands.append('b64:' + _command)
            _LOGGER.error(f"sending command: {_command}")
        service_data = {
            ATTR_ENTITY_ID: self._controller_data,
            'command': commands,
            'delay_secs': self._delay
        }

        await self.hass.services.async_call(
            'remote', 'send_command', service_data)