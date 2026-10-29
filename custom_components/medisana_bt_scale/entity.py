"""Shared entity base for the Medisana BT Scale sensor and binary_sensor platforms."""

from __future__ import annotations

from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothCoordinatorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_SLOT_MAP, DEBUG_SLOT, DEBUG_SLOT_NAME, DOMAIN, SLOT_NOT_ASSIGNED
from .coordinator import MedisanaDataUpdateCoordinator


def slot_map_for_entry(entry: ConfigEntry) -> dict[str, str]:
    """Return the {"1": person_entity_id_or_not_assigned} map for this entry."""
    return entry.options.get(CONF_SLOT_MAP, {})


def device_name_for_slot(hass: HomeAssistant, slot: int, slot_map: dict[str, str]) -> str:
    """Resolve a slot's device name: the linked person's name, or a generic one.

    DEBUG_SLOT is never a real scale user - it's where readings the scale
    itself couldn't attribute to any of its 8 user slots are routed, so it
    always gets its own fixed name and is never looked up in slot_map.
    """
    if slot == DEBUG_SLOT:
        return DEBUG_SLOT_NAME
    mapped_entity_id = slot_map.get(str(slot), SLOT_NOT_ASSIGNED)
    if mapped_entity_id != SLOT_NOT_ASSIGNED:
        if (state := hass.states.get(mapped_entity_id)) is not None:
            return state.name
    return f"Medisana BT Scale User {slot}"


class MedisanaSlotEntity(
    PassiveBluetoothCoordinatorEntity[MedisanaDataUpdateCoordinator]
):
    """Base entity representing one measurement of one scale user slot.

    Each slot gets its own Home Assistant device (previously all 8 user
    slots shared a single device identifier, which meant only one of them
    ever actually showed up in the device registry).
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MedisanaDataUpdateCoordinator,
        entry_id: str,
        slot: int,
        device_name: str,
        key: str,
        name: str,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._slot = slot
        self._key = key
        self._attr_unique_id = f"{entry_id}_{slot}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry_id}_{slot}")},
            name=device_name,
            manufacturer="Medisana",
            model="BS444",
        )

    @property
    def _record(self) -> dict:
        """The latest merged measurement record for this slot, if any."""
        return self.coordinator.measurements.get(self._slot, {})

    @property
    def available(self) -> bool:
        """Return True once this slot has data.

        A slot stays available once it has data, even after the scale
        disconnects again (which it does after every measurement) - gating
        on live BLE presence instead would flip every sensor to
        "unavailable" between weigh-ins, which defeats the purpose of a
        "last measured weight" sensor.
        """
        return self._key in self._record
