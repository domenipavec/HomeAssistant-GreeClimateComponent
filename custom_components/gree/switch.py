import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import (
    CONF_NAME, CONF_MAC,
)
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_platform(hass, config, async_add_devices, discovery_info=None):
    # set up via discovery from climate only
    if discovery_info is None:
        return

    _LOGGER.info('Setting up Gree switch platform %s, %s' % (config, discovery_info))

    name = discovery_info.get(CONF_NAME)
    mac = discovery_info.get(CONF_MAC).replace(':', '')

    coordinator = hass.data[DOMAIN]['coordinator']

    _LOGGER.info('Adding Gree switch devices to hass')
    async_add_devices([
        GreeSwitch(coordinator, name + ' Lights', mac, 'Lig'),
        GreeSwitch(coordinator, name + ' XFan', mac, 'Blo'),
        GreeSwitch(coordinator, name + ' Health', mac, 'Health'),
        GreeSwitch(coordinator, name + ' Powersave', mac, 'SvSt'),
        GreeSwitch(coordinator, name + ' Sleep', mac, 'SwhSlp'),
        GreeSwitch(coordinator, name + ' 8C', mac, 'StHt'),
        GreeSwitch(coordinator, name + ' Air', mac, 'Air'),
    ])


class GreeSwitch(CoordinatorEntity, SwitchEntity):
    def __init__(self, coordinator, name, mac, key):
        super().__init__(coordinator)

        self._key = key

        self._attr_name = name
        self._attr_unique_id = 'switch.gree_' + key.lower() + '_' + format_mac(mac)
        self._attr_device_info = DeviceInfo(
            identifiers={
                (DOMAIN, format_mac(mac)),
            },
        )

    @property
    def is_on(self):
        v = self.coordinator.data.get(self._key)
        if v == 1:
            return True
        elif v == 0:
            return False
        else:
            return None

    async def async_turn_on(self, **kwargs):
        await self._update_key(1)

    async def async_turn_off(self, **kwargs):
        await self._update_key(0)

    async def _update_key(self, value):
        self.coordinator.update_state(**{self._key: value})
        await self.coordinator.async_request_refresh()
