import base64
from Crypto.Cipher import AES
from datetime import timedelta
import json
import logging
import socket

from homeassistant.components.climate import (ClimateEntity, PLATFORM_SCHEMA)
from homeassistant.components.climate.const import (
    HVAC_MODE_OFF, HVAC_MODE_AUTO, HVAC_MODE_COOL, HVAC_MODE_DRY,
    HVAC_MODE_FAN_ONLY, HVAC_MODE_HEAT, SUPPORT_FAN_MODE,
    SUPPORT_TARGET_TEMPERATURE, SUPPORT_SWING_MODE,
)
from homeassistant.const import (
    ATTR_TEMPERATURE,
    CONF_NAME, CONF_HOST, CONF_PORT, CONF_MAC,
)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
import voluptuous as vol

DEFAULT_NAME = 'Gree Climate'
DEFAULT_PORT = 7000
DEFAULT_TIMEOUT = 10
POLLING_INTERVAL = timedelta(minutes=1)

HEAT_MODE_OFFSET = 3
MIN_TEMP = 16
MAX_TEMP = 30
GENERIC_GREE_DEVICE_KEY = "a3K8Bx%2r8Y7#xDh"

SUPPORT_FLAGS = SUPPORT_TARGET_TEMPERATURE | SUPPORT_FAN_MODE | SUPPORT_SWING_MODE

FAN_MODES = ['Auto', 'Low', 'Medium-Low', 'Medium', 'Medium-High', 'High', 'Turbo', 'Quiet']
HVAC_MODES = [HVAC_MODE_AUTO, HVAC_MODE_COOL, HVAC_MODE_DRY, HVAC_MODE_FAN_ONLY, HVAC_MODE_HEAT, HVAC_MODE_OFF]
SWING_MODES = ['Default', 'Swing in full range', 'Fixed in the upmost position', 'Fixed in the middle-up position', 'Fixed in the middle position', 'Fixed in the middle-low position', 'Fixed in the lowest position', 'Swing in the downmost region', 'Swing in the middle-low region', 'Swing in the middle region', 'Swing in the middle-up region', 'Swing in the upmost region']

AC_FIELDS = ["Pow","Mod","SetTem","WdSpd","Air","Blo","Health","SwhSlp","Lig","SwingLfRig","SwUpDn","Quiet","Tur","StHt","TemUn","HeatCoolType","TemRec","SvSt","SlpMod","TemSen"]

_LOGGER = logging.getLogger(__name__)


REQUIREMENTS = ['pycryptodome']

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
    vol.Required(CONF_HOST): cv.string,
    vol.Required(CONF_PORT, default=DEFAULT_PORT): cv.positive_int,
    vol.Required(CONF_MAC): cv.string,
})


async def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    name = config.get(CONF_NAME)
    host = config.get(CONF_HOST)
    port = config.get(CONF_PORT)
    mac = config.get(CONF_MAC).replace(':', '')

    coordinator = GreeCoordinator(hass, name, host, port, mac)
    await coordinator.async_config_entry_first_refresh()

    async_add_devices([
        GreeClimate(hass, coordinator, name, mac)
    ])


class GreeCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, name, host, port, mac):
        super().__init__(
            hass,
            _LOGGER,
            name=name,
            update_interval=POLLING_INTERVAL,
        )

        self._host = host
        self._port = port
        self._mac = mac
        self._timeout = DEFAULT_TIMEOUT
        self._uid = 0

        self._cipher = None

        self.updates = {}

    def update_state(self, **kwargs):
        self.updates.update(kwargs)

    def _request(self, data, cipher=None, i=0):
        exc = Exception("initial")
        for _ in range(10):
            try:
                return self._raw_request(data, cipher, i)
            except socket.timeout as e:
                _LOGGER.warning('Retrying Gree request timeout')
                exc = e
                continue
        raise exc

    def _raw_request(self, data, cipher, i):
        if cipher is None:
            if self._cipher is None:
                AES.new(self._get_device_key().encode("utf8"), AES.MODE_ECB)
            cipher = self._cipher

        _LOGGER.info('Request(%s)' % (data,))

        encodedPack = base64.b64encode(cipher.encrypt(_pad(json.dumps(data)).encode("utf-8"))).decode("utf-8")

        jsonData = json.dumps({
            "cid": "app",
            "i": i,
            "pack": encodedPack,
            "t": "pack",
            "tcid": self._mac,
            "uid": self._uid,
        })

        clientSock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        clientSock.settimeout(self._timeout)
        clientSock.sendto(bytes(jsonData, "utf-8"), (self._host, self._port))

        data, _ = clientSock.recvfrom(64000)
        clientSock.close()

        receivedJson = json.loads(data)
        pack = receivedJson['pack']
        base64decodedPack = base64.b64decode(pack)
        decryptedPack = cipher.decrypt(base64decodedPack)
        decodedPack = decryptedPack.decode("utf-8")
        replacedPack = decodedPack.replace('\x0f', '').replace(decodedPack[decodedPack.rindex('}')+1:], '')
        loadedJsonPack = json.loads(replacedPack)

        _LOGGER.info('Response(%s)' % (loadedJsonPack,))

        return loadedJsonPack

    def _get_device_key(self):
        cipher = AES.new(GENERIC_GREE_DEVICE_KEY.encode("utf8"), AES.MODE_ECB)
        return self._request({
            "t": "bind",
            "mac": self._mac,
            "uid": 0,
        }, cipher=cipher, i=1)['key']

    def _get_values(self):
        data = {
            "cols": AC_FIELDS,
            "mac": self._mac,
            "t": "status",
        }
        result = self._request(data)
        return {
            AC_FIELDS[i]: result['dat'][i] for i in range(len(AC_FIELDS))
        }

    def _set_values(self, new_values):
        data = {
            "t": "cmd",
            "opt": [k for k in AC_FIELDS if new_values[k] is not None],
            "p": [new_values[k] for k in AC_FIELDS if new_values[k] is not None],
        }
        self._request(data)

    async def _async_update_data(self):
        _LOGGER.info('_async_update_data: get current values')

        # load updates
        updates = self.updates.copy()

        current_values = self._get_values()

        _LOGGER.info('_async_update_data: current values: %s' % current_values)

        # remove any updates that are already set
        updates = dict(set(updates.items()) - set(current_values.items()))

        # return current values if no updates
        if updates == {}:
            return current_values

        # do nothing if power is off and we're not turning on
        if current_values.get('Pow') == 0 and updates.get('Pow') != 1:
            return current_values

        _LOGGER.info('_async_update_data: updates: %s' % updates)

        current_values.update(updates)
        self._set_values(current_values)

        _LOGGER.info('_async_update_data: new current values: %s' % current_values)

        # reset updates
        self.updates = {}

        return current_values


class GreeClimate(CoordinatorEntity, ClimateEntity):
    def __init__(self, hass, coordinator, name, mac):
        super().__init__(coordinator)

        self._attr_name = name
        self._attr_fan_modes = FAN_MODES
        self._attr_hvac_modes = HVAC_MODES
        self._attr_supported_features = SUPPORT_FLAGS
        self._attr_swing_modes = SWING_MODES
        self._attr_target_temperature_step = 1
        self._attr_temperature_unit = hass.config.units.temperature_unit
        self._attr_unique_id = 'climate.gree_' + format_mac(mac)

    def _adjust_for_heat_mode(self, t, offset=HEAT_MODE_OFFSET):
        if not t:
            return
        if self.hvac_mode == HVAC_MODE_HEAT:
            t += HEAT_MODE_OFFSET
        return t

    @property
    def current_temperature(self):
        t = self.coordinator.data.get('TemSen')
        if not t:
            return
        if t > 40:
            t -= 40
        return self._adjust_for_heat_mode(t)

    @property
    def min_temp(self):
        return self._adjust_for_heat_mode(MIN_TEMP)

    @property
    def max_temp(self):
        return self._adjust_for_heat_mode(MAX_TEMP)

    @property
    def target_temperature(self):
        t = self.coordinator.data.get('SetTem')
        if self.coordinator.data.get('StHt') == 1:
            return 8
        return self._adjust_for_heat_mode(t)

    @property
    def hvac_mode(self):
        if self.coordinator.data.get('Pow') == 0:
            return HVAC_MODE_OFF
        return _choose(HVAC_MODES, self.coordinator.data.get('Mod'))

    @property
    def swing_mode(self):
        return _choose(SWING_MODES, self.coordinator.data.get('SwUpDn'))

    @property
    def fan_mode(self):
        if self.coordinator.data.get('Tur') == 1:
            return 'Turbo'
        if self.coordinator.data.get('Quiet') >= 1:
            return 'Quiet'
        return _choose(FAN_MODES, self.coordinator.data.get('WdSpd'))


    async def async_set_fan_mode(self, fan):
        if fan == 'Turbo':
            self.coordinator.update_state(Tur=1, Quiet=0)
        elif fan == 'Quiet':
            self.coordinator.update_state(Tur=0, Quiet=1)
        else:
            self.coordinator.update_state(WdSpd=str(FAN_MODES.index(fan)), Tur=0, Quiet=0)
        await self.coordinator.async_request_refresh()

    async def async_set_hvac_mode(self, hvac):
        if hvac == HVAC_MODE_OFF:
            self.coordinator.update_state(Pow=0)
        else:
            self.coordinator.update_state(Mod=HVAC_MODES.index(hvac), Pow=1)
        await self.coordinator.async_request_refresh()

    async def async_set_swing_mode(self, swing):
        self.coordinator.update_state(SwUpDn=SWING_MODES.index(swing))
        await self.coordinator.async_request_refresh()

    async def async_set_temperature(self, **kwargs):
        t = kwargs.get(ATTR_TEMPERATURE)
        if t is None:
            return
        self.coordinator.update_state(SetTem=self._adjust_for_heat_mode(int(t), offset=-HEAT_MODE_OFFSET))
        await self.coordinator.async_request_refresh()

def _choose(options, value):
    if value is None:
        return
    return options[int(value)]

def _pad(s):
    aesBlockSize = 16
    return s + (aesBlockSize - len(s) % aesBlockSize) * chr(aesBlockSize - len(s) % aesBlockSize)
