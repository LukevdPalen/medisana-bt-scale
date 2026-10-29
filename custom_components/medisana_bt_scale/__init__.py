"""The Medisana BT Scale integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import MedisanaDataUpdateCoordinator, async_remove_stored_measurements

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Medisana BT Scale from a config entry.

    The scale is push-based: it only becomes reachable for a short window
    after someone steps on it, so setup must not block (or fail) waiting
    for a live connection. The coordinator just starts listening for
    advertisements here; entities populate whenever the scale is next used.
    """
    address = entry.data[CONF_ADDRESS]

    coordinator = MedisanaDataUpdateCoordinator(hass, address, entry.entry_id)
    await coordinator.async_load_stored_measurements()
    entry.async_on_unload(coordinator.async_start())

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options (the slot-to-person mapping) change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up persisted measurements when the integration is removed.

    Unlike async_unload_entry (which also runs on every reload/restart and
    must not touch stored data), this only fires when the user actually
    removes the integration - a re-add should start fresh, not resurface
    old measurements from before it was removed.
    """
    await async_remove_stored_measurements(hass, entry.entry_id)
