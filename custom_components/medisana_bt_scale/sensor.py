"""Sensor platform for Medisana BT Scale."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfMass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEBUG_SLOT, DOMAIN, MAX_USERS
from .coordinator import MedisanaDataUpdateCoordinator
from .entity import MedisanaSlotEntity, device_name_for_slot, slot_map_for_entry

_LOGGER = logging.getLogger(__name__)


@dataclass
class MedisanaSensorEntityDescription(SensorEntityDescription):
    """Describes Medisana sensor entity."""

    value_fn: Callable[[dict], float | int | None] = lambda x: x


SENSOR_TYPES: tuple[MedisanaSensorEntityDescription, ...] = (
    MedisanaSensorEntityDescription(
        key="weight",
        name="Weight",
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        device_class=SensorDeviceClass.WEIGHT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    MedisanaSensorEntityDescription(
        key="bmi",
        name="BMI",
        icon="mdi:human",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    MedisanaSensorEntityDescription(
        key="fat",
        name="Body Fat",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:percent",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    MedisanaSensorEntityDescription(
        key="tbw",
        name="Body Water",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:water-percent",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    MedisanaSensorEntityDescription(
        key="muscle",
        name="Muscle Mass",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:arm-flex",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    MedisanaSensorEntityDescription(
        key="bone",
        name="Bone Mass",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:bone",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    MedisanaSensorEntityDescription(
        key="kcal",
        name="Daily Calorie Requirement",
        native_unit_of_measurement=UnitOfEnergy.KILO_CALORIE,
        icon="mdi:fire",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    MedisanaSensorEntityDescription(
        key="age",
        name="Age",
        icon="mdi:calendar",
    ),
    MedisanaSensorEntityDescription(
        key="size",
        name="Height",
        native_unit_of_measurement="cm",
        icon="mdi:human-male-height",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Medisana BT Scale sensor based on a config entry."""
    coordinator: MedisanaDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    slot_map = slot_map_for_entry(entry)

    entities = []
    # DEBUG_SLOT catches readings the scale couldn't attribute to any of
    # its 8 user slots (raw slot byte 0xFF), so they're still visible
    # instead of being silently dropped.
    for slot in (DEBUG_SLOT, *range(1, MAX_USERS + 1)):
        device_name = device_name_for_slot(hass, slot, slot_map)
        for description in SENSOR_TYPES:
            entities.append(
                MedisanaSensor(coordinator, description, entry.entry_id, slot, device_name)
            )

    async_add_entities(entities)


class MedisanaSensor(MedisanaSlotEntity, SensorEntity):
    """Representation of a Medisana BT Scale sensor."""

    entity_description: MedisanaSensorEntityDescription

    def __init__(
        self,
        coordinator: MedisanaDataUpdateCoordinator,
        description: MedisanaSensorEntityDescription,
        entry_id: str,
        slot: int,
        device_name: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, entry_id, slot, device_name, description.key, description.name)
        self.entity_description = description

    @property
    def native_value(self) -> float | int | None:
        """Return the state of the sensor."""
        return self._record.get(self.entity_description.key)
