"""Binary sensor platform for Medisana BT Scale."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEBUG_SLOT, DOMAIN, MAX_USERS
from .coordinator import MedisanaDataUpdateCoordinator
from .entity import MedisanaSlotEntity, device_name_for_slot, slot_map_for_entry

_LOGGER = logging.getLogger(__name__)


@dataclass
class MedisanaBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes Medisana binary sensor entity."""

    value_fn: Callable[[dict], bool | None] = lambda x: x


BINARY_SENSOR_TYPES: tuple[MedisanaBinarySensorEntityDescription, ...] = (
    MedisanaBinarySensorEntityDescription(
        key="male",
        name="Male",
        icon="mdi:gender-male",
    ),
    MedisanaBinarySensorEntityDescription(
        key="female",
        name="Female",
        icon="mdi:gender-female",
    ),
    MedisanaBinarySensorEntityDescription(
        key="high_activity",
        name="High Activity Level",
        icon="mdi:run-fast",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Medisana BT Scale binary sensor based on a config entry."""
    coordinator: MedisanaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    slot_map = slot_map_for_entry(entry)

    entities = []
    for slot in (DEBUG_SLOT, *range(1, MAX_USERS + 1)):
        device_name = device_name_for_slot(hass, slot, slot_map)
        for description in BINARY_SENSOR_TYPES:
            entities.append(
                MedisanaBinarySensor(coordinator, description, entry.entry_id, slot, device_name)
            )

    async_add_entities(entities)


class MedisanaBinarySensor(MedisanaSlotEntity, BinarySensorEntity):
    """Representation of a Medisana BT Scale binary sensor."""

    entity_description: MedisanaBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: MedisanaDataUpdateCoordinator,
        description: MedisanaBinarySensorEntityDescription,
        entry_id: str,
        slot: int,
        device_name: str,
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, entry_id, slot, device_name, description.key, description.name)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary sensor is on."""
        return self._record.get(self.entity_description.key, False)
