"""DataUpdateCoordinator for the Medisana BT Scale integration.

The scale is not continuously connectable: it only starts advertising and
accepting a GATT connection for a short window after someone steps on it.
This rules out a fixed-interval poll. Instead this module uses Home
Assistant's ActiveBluetoothDataUpdateCoordinator, which listens passively
for advertisements and only opens an active connection (a "poll") when one
is seen - exactly the shape this device needs.

When a poll happens, the coordinator connects, enables indications on the
Weight/Body/Person characteristics, writes the trigger command, and collects
every indication the scale sends (it replays its stored history - up to 30
measurements per user slot - not just a single packet), before
disconnecting again.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    CHAR_BODY_UUID,
    CHAR_COMMAND_UUID,
    CHAR_PERSON_UUID,
    CHAR_WEIGHT_UUID,
    DEBUG_SLOT,
    DOMAIN,
    POLL_COOLDOWN_SECONDS,
    PROBE_TIMEOUT_SECONDS,
    QUIET_PERIOD_SECONDS,
    SESSION_TIMEOUT_SECONDS,
)
from .parser import (
    BodyMeasurement,
    PersonProfile,
    WeightMeasurement,
    build_trigger_command,
    compute_bmi,
    deserialize_measurements,
    is_newer_timestamp,
    newer,
    parse_body,
    parse_person,
    parse_weight,
    serialize_measurements,
)

_LOGGER = logging.getLogger(__name__)

# One measurement record per scale user slot (1-8).
MeasurementsBySlot = dict[int, dict[str, Any]]

STORAGE_VERSION = 1


class ScaleSession:
    """One connect -> handshake -> collect -> disconnect cycle.

    Shared between the coordinator (full data poll) and the config flow
    (short probe for Person-profile hints only), so the handshake logic
    lives in exactly one place.
    """

    def __init__(self) -> None:
        self.weights: dict[int | None, WeightMeasurement] = {}
        self.bodies: dict[int | None, BodyMeasurement] = {}
        self.persons: dict[int, PersonProfile] = {}
        self._quiet_event = asyncio.Event()
        self._disconnect_event = asyncio.Event()
        self._has_measurement = False

    async def run(
        self,
        ble_device: BLEDevice,
        name: str,
        *,
        person_only: bool = False,
        total_timeout: float = SESSION_TIMEOUT_SECONDS,
    ) -> None:
        """Connect, enable indications, trigger the scale, and collect data."""
        def _on_disconnect(_client: BleakClient) -> None:
            self._disconnect_event.set()
            self._quiet_event.set()

        client: BleakClient = await establish_connection(
            BleakClientWithServiceCache, ble_device, name,
            disconnected_callback=_on_disconnect,
        )
        try:
            if not person_only:
                await client.start_notify(CHAR_WEIGHT_UUID, self._on_weight)
                await client.start_notify(CHAR_BODY_UUID, self._on_body)
            await client.start_notify(CHAR_PERSON_UUID, self._on_person)

            await client.write_gatt_char(CHAR_COMMAND_UUID, build_trigger_command())

            await self._wait_for_quiet(total_timeout)
        finally:
            if client.is_connected:
                await client.disconnect()

    async def _wait_for_quiet(self, total_timeout: float) -> None:
        """Keep listening until the scale stops sending or we hit the cap.

        The BS444 uses a two-phase BLE protocol:

        Phase 1 — scale sends slot=255 person while the BIA cycle runs
        (~10 s), then disconnects. No measurement data arrives yet.
        When we detect this early disconnect we return immediately so the
        coordinator session ends and the next advertisement (phase 2) is
        not blocked by the session lock or a long cooldown.

        Phase 2 — scale re-advertises with the stored measurement. A new
        poll starts, receives weight/body/person with a real slot, and
        uses QUIET_PERIOD_SECONDS to drain the history burst.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + total_timeout
        while True:
            self._quiet_event.clear()
            # Early disconnect before any measurement → end immediately so
            # the coordinator can reconnect for phase 2.
            if self._disconnect_event.is_set() and not self._has_measurement:
                return
            remaining = deadline - loop.time()
            if remaining <= 0:
                return
            wait = QUIET_PERIOD_SECONDS if self._has_measurement else remaining
            try:
                await asyncio.wait_for(
                    self._quiet_event.wait(),
                    timeout=min(wait, remaining),
                )
            except TimeoutError:
                return

    def _on_weight(self, _sender: Any, data: bytearray) -> None:
        # A real connection replays the scale's whole stored history (up to
        # 30 measurements per user), not just the newest reading, and not
        # necessarily in chronological order - keep whichever has the
        # latest timestamp per slot rather than whichever arrives last.
        if (measurement := parse_weight(bytes(data))) is not None:
            self.weights[measurement.slot] = newer(
                self.weights.get(measurement.slot), measurement
            )
        self._has_measurement = True
        self._quiet_event.set()

    def _on_body(self, _sender: Any, data: bytearray) -> None:
        if (measurement := parse_body(bytes(data))) is not None:
            self.bodies[measurement.slot] = newer(
                self.bodies.get(measurement.slot), measurement
            )
        self._has_measurement = True
        self._quiet_event.set()

    def _on_person(self, _sender: Any, data: bytearray) -> None:
        # The Person packet carries no timestamp to compare by, but it's
        # just the slot's current profile settings (not a time series), so
        # last-received is fine here.
        if (profile := parse_person(bytes(data))) is not None:
            self.persons[profile.slot] = profile
        self._quiet_event.set()


async def async_probe_person_slots(
    hass: HomeAssistant, address: str, timeout: float = PROBE_TIMEOUT_SECONDS
) -> dict[int, PersonProfile]:
    """Best-effort short connection used by the config flow to show slot hints.

    Only listens for Person-profile indications (not weight/body), and never
    raises - a scale that isn't reachable during setup just yields no hints,
    the config flow still lets the user complete the slot mapping blind.
    """
    ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if ble_device is None:
        _LOGGER.debug("%s: not reachable for slot probe", address)
        return {}

    session = ScaleSession()
    try:
        await session.run(ble_device, address, person_only=True, total_timeout=timeout)
    except (BleakError, TimeoutError, OSError) as err:
        # This one stays at debug: failing to grab hints during setup is
        # expected/harmless if the scale simply hasn't been used recently.
        _LOGGER.debug("%s: slot probe failed: %s", address, err)
    return session.persons


class MedisanaDataUpdateCoordinator(
    ActiveBluetoothDataUpdateCoordinator[MeasurementsBySlot]
):
    """Connects to the scale whenever it advertises and pulls its data."""

    def __init__(self, hass: HomeAssistant, address: str, entry_id: str) -> None:
        """Initialize the coordinator."""
        self.address = address
        self.measurements: MeasurementsBySlot = {}
        self._session_lock = asyncio.Lock()
        self._store: Store[dict[str, dict[str, Any]]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}_{entry_id}_measurements"
        )
        super().__init__(
            hass,
            _LOGGER,
            address=address,
            mode=BluetoothScanningMode.PASSIVE,
            needs_poll_method=self._needs_poll,
            poll_method=self._async_poll_scale,
            connectable=True,
        )

    async def async_load_stored_measurements(self) -> None:
        """Restore the last known measurements from disk, if any.

        The scale only advertises when someone steps on it, so after a
        Home Assistant restart it could otherwise be a long wait before any
        sensor shows a value again - call this before entities are set up
        so they see the restored data on their very first read.
        """
        stored = await self._store.async_load()
        if stored:
            self.measurements = deserialize_measurements(stored)
            _LOGGER.debug(
                "%s: restored %d slot(s) from storage", self.address, len(self.measurements)
            )

    @staticmethod
    def _needs_poll(
        service_info: BluetoothServiceInfoBleak, seconds_since_last_poll: float | None
    ) -> bool:
        """Decide whether this advertisement is worth connecting for.

        The scale only advertises for a short window after someone steps on
        it, so any advertisement is worth trying - but not more often than
        POLL_COOLDOWN_SECONDS, so a burst of advertisements from the same
        weigh-in doesn't trigger repeated connection attempts.
        """
        if seconds_since_last_poll is None:
            return True
        return seconds_since_last_poll > POLL_COOLDOWN_SECONDS

    async def _async_poll_scale(
        self, service_info: BluetoothServiceInfoBleak
    ) -> MeasurementsBySlot:
        """Connect, run the handshake, collect the history dump, disconnect."""
        if self._session_lock.locked():
            # A poll is already in flight (e.g. a duplicate advertisement
            # event); don't stack a second connection attempt on top of it.
            return self.measurements

        async with self._session_lock:
            _LOGGER.debug("%s: advertisement seen, connecting", self.address)
            session = ScaleSession()
            try:
                await session.run(service_info.device, service_info.name or self.address)
            except (BleakError, TimeoutError, OSError) as err:
                # Logged at warning (not debug) so this is visible in the
                # Home Assistant log without needing debug logging enabled
                # for this component - a silently failing poll otherwise
                # looks identical to "the scale was just never used".
                _LOGGER.warning("%s: failed to poll scale: %s", self.address, err)
                return self.measurements

            self._merge_session(session)
            _LOGGER.debug(
                "%s: poll complete - %d weight, %d body, %d person record(s) received",
                self.address,
                len(session.weights),
                len(session.bodies),
                len(session.persons),
            )
            await self._store.async_save(serialize_measurements(self.measurements))
            return self.measurements

    def _merge_session(self, session: ScaleSession) -> None:
        """Fold a completed session's decoded packets into self.measurements.

        The scale occasionally fails to recognize which user stepped on it
        (confirmed on real hardware) and tags the reading with slot 0xFF
        instead of a real 1..8 slot - parser.py surfaces that as
        slot=None.

        When exactly one person profile is received in the session we can
        safely infer the unrecognized reading belongs to that user (the
        common single-user case, and any session where only one person's
        scale was active). When multiple person profiles are present we
        cannot know which one the unrecognized reading belongs to, so it
        falls back to DEBUG_SLOT.

        A single BLE session can end up truncated - weak/marginal signal,
        the hard session timeout - before it reaches the scale's actual
        latest reading in its history replay. `newer()` already protects
        against out-of-order packets *within* one session; `_weight_ts`/
        `_body_ts` do the same *across* sessions, so a later, worse
        connection can never regress an already-known-newer value (which
        matters a lot for anything, like a Garmin sync automation, that
        reacts to this sensor's state changing).
        """
        # If exactly one person profile arrived this session, unrecognized
        # (slot=None) measurements can be attributed to that slot.
        inferred_slot = (
            next(iter(session.persons)) if len(session.persons) == 1 else None
        )

        def _resolve(slot: int | None) -> int:
            if slot is not None:
                return slot
            return inferred_slot if inferred_slot is not None else DEBUG_SLOT

        for slot, weight in session.weights.items():
            target_slot = _resolve(slot)
            record = self.measurements.setdefault(target_slot, {})
            if is_newer_timestamp(record.get("_weight_ts"), weight.timestamp):
                record["weight"] = round(weight.weight_kg, 2)
                record["last_measured"] = weight.timestamp
                record["_weight_ts"] = weight.timestamp

        for slot, body in session.bodies.items():
            target_slot = _resolve(slot)
            record = self.measurements.setdefault(target_slot, {})
            if is_newer_timestamp(record.get("_body_ts"), body.timestamp):
                record["fat"] = body.fat_pct
                record["tbw"] = body.water_pct
                record["muscle"] = body.muscle_pct
                record["bone"] = body.bone_pct
                record["kcal"] = body.kcal
                record["last_measured"] = body.timestamp
                record["_body_ts"] = body.timestamp

        for slot, person in session.persons.items():
            record = self.measurements.setdefault(slot, {})
            record["male"] = person.male
            record["female"] = not person.male
            record["age"] = person.age
            record["size"] = person.height_cm
            record["high_activity"] = person.high_activity

        for record in self.measurements.values():
            record["bmi"] = compute_bmi(record.get("weight"), record.get("size"))

    async def async_refresh_person_hints(self) -> dict[int, PersonProfile]:
        """Re-probe Person-profile hints on demand (used by the options flow)."""
        return await async_probe_person_slots(self.hass, self.address)


async def async_remove_stored_measurements(hass: HomeAssistant, entry_id: str) -> None:
    """Delete a removed config entry's persisted measurements.

    Called from async_remove_entry (integration actually removed), not
    async_unload_entry (which also fires on every reload/restart) - a
    stale storage file surviving removal would otherwise make a fresh
    re-add of the integration show old data as if it were current.
    """
    store: Store[dict[str, dict[str, Any]]] = Store(
        hass, STORAGE_VERSION, f"{DOMAIN}_{entry_id}_measurements"
    )
    await store.async_remove()
