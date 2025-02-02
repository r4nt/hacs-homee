"""The homee integration."""
import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import Entity
from pymee import Homee
from pymee.model import HomeeAttribute, HomeeNode
from pymee.const import AttributeType, NodeProfile
import voluptuous as vol

from .helpers import get_attribute_for_enum
from .const import (
    ATTR_ATTRIBUTE,
    ATTR_NODE,
    ATTR_VALUE,
    CONF_ADD_HOME_DATA,
    CONF_INITIAL_OPTIONS,
    DOMAIN,
    SERVICE_SET_VALUE,
)

_LOGGER = logging.getLogger(DOMAIN)

# TODO
CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema({})}, extra=vol.ALLOW_EXTRA)

PLATFORMS = ["light", "climate", "binary_sensor", "switch", "cover", "sensor"]


async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the homee component."""
    hass.data[DOMAIN] = {}
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Set up homee from a config entry."""
    # Create the Homee api object using host, user and password from the config
    homee = Homee(
        entry.data[CONF_HOST], entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
    )

    # Migrate initial options
    if entry.options is None or entry.options == {}:
        options = entry.data.get(CONF_INITIAL_OPTIONS, {})
        hass.config_entries.async_update_entry(entry, options=options)

    # Start the homee websocket connection as a new task and wait until we are connected
    hass.loop.create_task(homee.run())
    await homee.wait_until_connected()

    # Log info about nodes, to facilitate recognition of unknown nodes.
    for node in homee.nodes:
        _LOGGER.info(
            "Found node %s, with following Data: %s",
            node.name,
            node._data,
        )

    hass.data[DOMAIN][entry.entry_id] = homee

    # Register the set_value service that can be used for debugging and custom automations
    def handle_set_value(call: ServiceCall):
        """Handle the service call."""
        node = int(call.data.get(ATTR_NODE, 0))
        attribute = int(call.data.get(ATTR_ATTRIBUTE, 0))
        value = float(call.data.get(ATTR_VALUE, 0))

        hass.async_create_task(homee.set_value(node, attribute, value))

    hass.services.async_register(DOMAIN, SERVICE_SET_VALUE, handle_set_value)

    # create device register entry
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        # TODO: figure out how to derive the MAC address - will need to update pymee?
        # connections={(dr.CONNECTION_NETWORK_MAC, entry.mac)},
        identifiers={(DOMAIN, homee.deviceId)},
        manufacturer="homee",
        name=homee.settings.homee_name,
        model="homee",
        sw_version=homee.settings.version,
        hw_version="TBD",
    )

    # Forward entry setup to the platforms
    for component in PLATFORMS:
        hass.async_create_task(
            hass.config_entries.async_forward_entry_setup(entry, component)
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Unload a homee config entry."""
    # Unload platforms
    unload_ok = all(
        await asyncio.gather(
            *[
                hass.config_entries.async_forward_entry_unload(entry, component)
                for component in PLATFORMS
            ]
        )
    )
    if unload_ok:
        # Get Homee object and remove it from data
        homee: Homee = hass.data[DOMAIN][entry.entry_id]
        hass.data[DOMAIN].pop(entry.entry_id)

        # Schedule homee disconnect
        homee.disconnect()

        # Remove services
        hass.services.async_remove(DOMAIN, SERVICE_SET_VALUE)

    return unload_ok


class HomeeNodeEntity:
    """Representation of a Node in Homee."""

    def __init__(self, node: HomeeNode, entity: Entity, entry: ConfigEntry) -> None:
        """Initialize the wrapper using a HomeeNode and target entity."""
        self._node = node
        self._entity = entity
        self._clear_node_listener = None
        self._unique_id = node.id
        self._entry = entry

        self._homee_data = {
            "id": node.id,
            "name": node.name,
            "profile": node.profile,
            "attributes": [{"id": a.id, "type": a.type} for a in node.attributes],
        }

    async def async_added_to_hass(self) -> None:
        """Add the homee binary sensor device to home assistant."""
        self.register_listener()

    async def async_will_remove_from_hass(self):
        """Cleanup the entity."""
        self.clear_listener()

    @property
    def device_info(self):
        """Holds the available information about the device"""
        if self.has_attribute(AttributeType.SOFTWARE_REVISION):
            sw_version = self.attribute(AttributeType.SOFTWARE_REVISION)
        else:
            sw_version = "undefined"

        return {
            "identifiers": {
                # Serial numbers are unique identifiers within a specific domain
                (DOMAIN, self._node.id)
            },
            "name": self._node.name,
            "default_manufacturer": "unknown",
            "default_model": get_attribute_for_enum(
                NodeProfile, self._homee_data["profile"]
            ),
            "sw_version": sw_version,
            "via_device": (DOMAIN, self._entry.entry_id),
        }

    @property
    def should_poll(self) -> bool:
        """Return if the entity should poll."""
        return False

    @property
    def unique_id(self):
        """Return the unique ID of the entity."""
        return self._unique_id

    @property
    def name(self):
        """Return the display name of this entity."""
        return None

    @property
    def raw_data(self):
        """Return the raw data of the node."""
        return self._node._data

    @property
    def state_attributes(self):
        data = self._entity.__class__.__bases__[1].state_attributes.fget(self)
        if data is None:
            data = {}

        if self._entry.options.get(CONF_ADD_HOME_DATA, False):
            data["homee_data"] = self._homee_data

        return data if data != {} else None

    async def async_update(self):
        """Fetch new state data for this light."""
        self._node._remap_attributes()

    def register_listener(self):
        """Register the on_changed listener on the node."""
        self._clear_node_listener = self._node.add_on_changed_listener(
            self._on_node_updated
        )

    def clear_listener(self):
        """Clear the on_changed listener on the node."""
        if self._clear_node_listener is not None:
            self._clear_node_listener()

    def attribute(self, attributeType):
        """Try to get the current value of the attribute of the given type."""
        try:
            return self._node.get_attribute_by_type(attributeType).current_value
        except Exception:
            raise AttributeNotFoundException(attributeType)

    def get_attribute(self, attributeType):
        """Get the attribute object of the given type."""
        return self._node.get_attribute_by_type(attributeType)

    def has_attribute(self, attributeType):
        """Check if an attribute of the given type exists."""
        return attributeType in self._node._attribute_map

    async def async_set_value(self, attribute_type: int, value: float):
        """Set an attribute value on the homee node."""
        await self.async_set_value_by_id(self.get_attribute(attribute_type).id, value)

    async def async_set_value_by_id(self, attribute_id: int, value: float):
        """Set an attribute value on the homee node."""
        await self._entity.hass.services.async_call(
            DOMAIN,
            SERVICE_SET_VALUE,
            {
                ATTR_NODE: self._node.id,
                ATTR_ATTRIBUTE: attribute_id,
                ATTR_VALUE: value,
            },
        )

    def _on_node_updated(self, node: HomeeNode, attribute: HomeeAttribute):
        self._entity.schedule_update_ha_state()


class AttributeNotFoundException(Exception):
    """Raised if a requested attribute does not exist on a homee node."""

    def __init__(self, attributeType) -> None:
        """Initialize the exception."""
        self.attributeType = attributeType
