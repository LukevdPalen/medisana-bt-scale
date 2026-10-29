"""Config flow for Medisana BT Scale integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_SLOT_MAP,
    DOMAIN,
    MAX_USERS,
    MEDISANA_MAC_OUI,
    MEDISANA_MANUFACTURER_ID,
    MEDISANA_NAME_PREFIXES,
    SLOT_NOT_ASSIGNED,
)
from .coordinator import MedisanaDataUpdateCoordinator, async_probe_person_slots
from .parser import PersonProfile

_LOGGER = logging.getLogger(__name__)


def _looks_like_medisana_scale(discovery_info: BluetoothServiceInfoBleak) -> bool:
    """Best-effort match for a Medisana Bluetooth scale, since its advertised name is opaque.

    The scale's advertised name is a numeric string like
    "013198000000000000" - confirmed on real hardware - not something
    containing "BS4". The leading digits are a real, documented family
    prefix (see MEDISANA_NAME_PREFIXES), which is the most reliable signal;
    manufacturer ID and MAC OUI are kept as additional OR'd fallbacks
    (the MAC OUI only matches on Linux/BlueZ hosts - see the comment on
    MEDISANA_MAC_OUI).
    """
    name = discovery_info.name or ""
    if any(name.startswith(prefix) for prefix in MEDISANA_NAME_PREFIXES):
        return True
    if "BS4" in name:
        return True
    if discovery_info.address.upper().startswith(MEDISANA_MAC_OUI):
        return True
    if MEDISANA_MANUFACTURER_ID in discovery_info.manufacturer_data:
        return True
    return False


def _slot_field_options(hass: HomeAssistant) -> dict[str, str]:
    """Build the dropdown options for a slot: 'Not assigned' + every person."""
    options = {SLOT_NOT_ASSIGNED: "Not assigned"}
    for entity_id in sorted(hass.states.async_entity_ids("person")):
        if (state := hass.states.get(entity_id)) is not None:
            options[entity_id] = state.name
    return options


def _build_slot_schema(hass: HomeAssistant, current_fields: dict[str, str]) -> vol.Schema:
    """Build the 8-slot mapping form, one dropdown per scale user slot."""
    options = _slot_field_options(hass)
    fields: dict[Any, Any] = {}
    for slot in range(1, MAX_USERS + 1):
        key = f"slot_{slot}"
        default = current_fields.get(key, SLOT_NOT_ASSIGNED)
        fields[vol.Optional(key, default=default)] = vol.In(options)
    return vol.Schema(fields)


def _extract_slot_map(user_input: dict[str, str]) -> dict[str, str]:
    """Convert the form's slot_N fields into a {"1": entity_id_or_not_assigned} map."""
    return {
        str(slot): user_input.get(f"slot_{slot}", SLOT_NOT_ASSIGNED)
        for slot in range(1, MAX_USERS + 1)
    }


def _format_hints(hints: dict[int, PersonProfile]) -> str:
    """Render discovered Person-profile data as a human-readable hint block."""
    lines = [
        f"Slot {slot}: {'Male' if profile.male else 'Female'}, "
        f"{profile.age}y, {profile.height_cm}cm"
        for slot, profile in sorted(hints.items())
    ]
    if not lines:
        return (
            "No slot data received yet. Step on the scale so it wakes up, "
            "then try again (you can also redo this later via "
            "Settings → Devices & services → Configure)."
        )
    return "\n".join(lines)


class MedisanaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Medisana BT Scale."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._discovery_info: BluetoothServiceInfoBleak | None = None
        self._discovered_devices: dict[str, BluetoothServiceInfoBleak] = {}
        self._address: str | None = None
        self._title: str = "Medisana BT Scale"
        self._person_hints: dict[int, PersonProfile] | None = None

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle the bluetooth discovery step."""
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        self._discovery_info = discovery_info
        self._address = discovery_info.address
        self._title = discovery_info.name or "Medisana BT Scale"
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm discovery."""
        assert self._discovery_info is not None

        if user_input is not None:
            return await self.async_step_slot_mapping()

        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={
                "name": self._discovery_info.name or "Medisana BT Scale"
            },
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step to pick discovered device."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()

            discovery_info = self._discovered_devices[address]
            self._address = address
            self._title = discovery_info.name or "Medisana BT Scale"
            return await self.async_step_slot_mapping()

        current_addresses = self._async_current_ids()
        for discovery_info in async_discovered_service_info(self.hass):
            if (
                discovery_info.address in current_addresses
                or discovery_info.address in self._discovered_devices
            ):
                continue

            if _looks_like_medisana_scale(discovery_info):
                self._discovered_devices[discovery_info.address] = discovery_info

        if not self._discovered_devices:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {
                            address: f"{info.name or 'Medisana BT Scale'} ({address})"
                            for address, info in self._discovered_devices.items()
                        }
                    )
                }
            ),
        )

    async def async_step_slot_mapping(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user link scale user slots to Home Assistant persons.

        Best-effort connects to the scale first to pick up any Person-
        profile data it's currently holding, purely to show hints
        (gender/age/height) that make it easier to tell slots apart - if
        the scale isn't reachable right now, the form is still shown with
        no hints, and the mapping can always be redone later via the
        options flow ("Configure").
        """
        assert self._address is not None

        if user_input is not None:
            return self.async_create_entry(
                title=self._title,
                data={CONF_ADDRESS: self._address},
                options={CONF_SLOT_MAP: _extract_slot_map(user_input)},
            )

        if self._person_hints is None:
            self._person_hints = await async_probe_person_slots(self.hass, self._address)

        return self.async_show_form(
            step_id="slot_mapping",
            data_schema=_build_slot_schema(self.hass, {}),
            description_placeholders={"hints": _format_hints(self._person_hints)},
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> MedisanaOptionsFlowHandler:
        """Get the options flow for this handler."""
        return MedisanaOptionsFlowHandler()


class MedisanaOptionsFlowHandler(OptionsFlow):
    """Let the user redo the slot-to-person mapping at any time."""

    def __init__(self) -> None:
        """Initialize the options flow."""
        self._person_hints: dict[int, PersonProfile] | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the slot-to-person mapping."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SLOT_MAP: _extract_slot_map(user_input)}
            )

        if self._person_hints is None:
            self._person_hints = await self._async_get_person_hints()

        current = self.config_entry.options.get(CONF_SLOT_MAP, {})
        current_fields = {
            f"slot_{slot}": current.get(str(slot), SLOT_NOT_ASSIGNED)
            for slot in range(1, MAX_USERS + 1)
        }

        return self.async_show_form(
            step_id="init",
            data_schema=_build_slot_schema(self.hass, current_fields),
            description_placeholders={"hints": _format_hints(self._person_hints)},
        )

    async def _async_get_person_hints(self) -> dict[int, PersonProfile]:
        """Prefer an already-running coordinator, otherwise probe directly."""
        coordinator: MedisanaDataUpdateCoordinator | None = self.hass.data.get(
            DOMAIN, {}
        ).get(self.config_entry.entry_id)
        if coordinator is not None:
            return await coordinator.async_refresh_person_hints()
        return await async_probe_person_slots(
            self.hass, self.config_entry.data[CONF_ADDRESS]
        )
