"""The homee climate platform."""

import logging

import homeassistant
from homeassistant.components.climate import (
    ATTR_TEMPERATURE,
    SUPPORT_TARGET_TEMPERATURE,
    SUPPORT_TARGET_TEMPERATURE_RANGE,
    ClimateEntity,
)
from homeassistant.components.climate.const import HVAC_MODE_HEAT
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import TEMP_CELSIUS, TEMP_FAHRENHEIT
from pymee.const import AttributeType, NodeProfile
from pymee.model import HomeeNode

from . import HomeeNodeEntity, helpers

_LOGGER = logging.getLogger(__name__)

HOMEE_UNIT_TO_HA_UNIT = {"°C": TEMP_CELSIUS, "°F": TEMP_FAHRENHEIT}


def get_climate_features(node: HomeeNodeEntity, default=0) -> int:
    """Determine the supported climate features of a homee node based on the available attributes."""
    features = default

    if node.has_attribute(AttributeType.TARGET_TEMPERATURE):
        features |= SUPPORT_TARGET_TEMPERATURE
    if node.has_attribute(AttributeType.TARGET_TEMPERATURE_LOW) and node.has_attribute(
        AttributeType.TARGET_TEMPERATURE_HIGH
    ):
        features |= SUPPORT_TARGET_TEMPERATURE_RANGE

    return features


async def async_setup_entry(hass, config_entry, async_add_devices):
    """Add the homee platform for the light integration."""
    # homee: Homee = hass.data[DOMAIN][config_entry.entry_id]

    devices = []
    for node in helpers.get_imported_nodes(hass, config_entry):
        if not is_climate_node(node):
            continue
        devices.append(HomeeClimate(node, config_entry))
    if devices:
        async_add_devices(devices)


async def async_unload_entry(hass: homeassistant, entry: ConfigEntry):
    """Unload a config entry."""


def is_climate_node(node: HomeeNode):
    """Determine if a node is controllable as a homee light based on it's profile and attributes."""
    return node.profile in [
        NodeProfile.RADIATOR_THERMOSTAT,
        NodeProfile.THERMOSTAT_WITH_HEATING_AND_COOLING,
        NodeProfile.HEATING_SYSTEM,
    ]


class HomeeClimate(HomeeNodeEntity, ClimateEntity):
    """Representation of a homee climate device."""

    def __init__(self, node: HomeeNode, entry: ConfigEntry):
        """Initialize a homee climate entity."""
        HomeeNodeEntity.__init__(self, node, self, entry)
        self._supported_features = get_climate_features(self)
        _LOGGER.info(f"{node.name}: {node.profile}")

    @property
    def supported_features(self):
        """Return the supported features of the entity."""
        return self._supported_features

    @property
    def temperature_unit(self) -> str:
        """Return the temperature unit of the device."""
        return HOMEE_UNIT_TO_HA_UNIT[self.get_attribute(AttributeType.TEMPERATURE).unit]

    @property
    def hvac_modes(self):
        """Return the available hvac operation modes."""
        return [HVAC_MODE_HEAT]

    @property
    def hvac_mode(self):
        """Return the hvac operation mode."""
        return HVAC_MODE_HEAT

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self.attribute(AttributeType.TEMPERATURE)

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self.attribute(AttributeType.TARGET_TEMPERATURE)

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return self.get_attribute(AttributeType.TARGET_TEMPERATURE).step_value

    async def async_set_temperature(self, **kwargs) -> None:
        """Set new target temperature."""

        if ATTR_TEMPERATURE in kwargs:
            await self.async_set_value(
                AttributeType.TARGET_TEMPERATURE, kwargs[ATTR_TEMPERATURE]
            )
