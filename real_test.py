"""Manual smoke-test script: scan for a real Medisana Bluetooth scale, connect
and collect weight/body/person data as it arrives.

The BS444 (and compatible models) use a two-phase BLE protocol:
  Phase 1 (~10 s): scale sends a slot=255 person record while it runs the
    BIA cycle, then disconnects.
  Phase 2: scale re-advertises; new connection delivers the stored
    measurement (slot=1..8, real weight, body composition).

This script handles both phases automatically by re-scanning after an
early disconnect.

Run directly against real hardware (needs `bleak` installed):
    python3 real_test.py

Reuses the integration's own (unit-tested) parser.py for an end-to-end
check of the same decoding logic Home Assistant will use.
"""

from __future__ import annotations

import asyncio
import sys
import time as _time
from pathlib import Path

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

sys.path.insert(0, str(Path(__file__).resolve().parent / "custom_components" / "medisana_bt_scale"))
from parser import build_trigger_command, parse_body, parse_person, parse_weight  # noqa: E402

CHAR_WEIGHT_UUID = "00008a21-0000-1000-8000-00805f9b34fb"
CHAR_BODY_UUID   = "00008a22-0000-1000-8000-00805f9b34fb"
CHAR_PERSON_UUID = "00008a82-0000-1000-8000-00805f9b34fb"
CHAR_COMMAND_UUID = "00008a81-0000-1000-8000-00805f9b34fb"

MEDISANA_MANUFACTURER_ID = 0x00DC
MEDISANA_MAC_OUI = "E4:54:EB"
MEDISANA_NAME_PREFIXES = ("013197", "013198", "0202B6")

QUIET_PERIOD = 3.0    # silence after first measurement = history dump done
SESSION_TIMEOUT = 30.0  # per-connection cap
SCAN_TIMEOUT = 20.0   # how long to wait for scale to re-advertise (phase 2)


def looks_like_medisana(device: BLEDevice, adv: AdvertisementData) -> bool:
    """Detection heuristic shared with the HA integration's config flow.

    On macOS, device.address is a CoreBluetooth pseudo-UUID, not a real
    MAC address, so the OUI check never matches there — the name-prefix
    check is what actually works on macOS.
    """
    name = device.name or ""
    if any(name.startswith(p) for p in MEDISANA_NAME_PREFIXES):
        return True
    if "BS4" in name:
        return True
    if device.address.upper().startswith(MEDISANA_MAC_OUI):
        return True
    if adv.manufacturer_data and MEDISANA_MANUFACTURER_ID in adv.manufacturer_data:
        return True
    return False


async def collect_one_session(device: BLEDevice, label: str) -> bool:
    """Connect, trigger, collect.  Returns True if measurement data arrived."""
    quiet_event = asyncio.Event()
    disconnect_event = asyncio.Event()
    has_measurement = False
    t0 = _time.monotonic()

    def on_weight(_sender: object, data: bytearray) -> None:
        nonlocal has_measurement
        print(f"  +{_time.monotonic()-t0:.1f}s [weight] {data.hex()} -> {parse_weight(bytes(data))}")
        has_measurement = True
        quiet_event.set()

    def on_body(_sender: object, data: bytearray) -> None:
        nonlocal has_measurement
        print(f"  +{_time.monotonic()-t0:.1f}s [body]   {data.hex()} -> {parse_body(bytes(data))}")
        has_measurement = True
        quiet_event.set()

    def on_person(_sender: object, data: bytearray) -> None:
        print(f"  +{_time.monotonic()-t0:.1f}s [person] {data.hex()} -> {parse_person(bytes(data))}")
        quiet_event.set()

    def on_disconnect(_client: object) -> None:
        print(f"  +{_time.monotonic()-t0:.1f}s [disconnect] scale closed connection")
        disconnect_event.set()
        quiet_event.set()

    print(f"\n--- {label} ---")
    async with BleakClient(device, disconnected_callback=on_disconnect) as client:
        print(f"  +{_time.monotonic()-t0:.1f}s Connected to {device.name} ({device.address})")

        await client.start_notify(CHAR_WEIGHT_UUID, on_weight)
        await client.start_notify(CHAR_BODY_UUID, on_body)
        await client.start_notify(CHAR_PERSON_UUID, on_person)
        await client.write_gatt_char(CHAR_COMMAND_UUID, build_trigger_command())
        print(f"  +{_time.monotonic()-t0:.1f}s Trigger sent, listening...")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + SESSION_TIMEOUT
        while True:
            quiet_event.clear()
            if disconnect_event.is_set() and not has_measurement:
                print(f"  +{_time.monotonic()-t0:.1f}s Early disconnect (phase 1 — BIA in progress)")
                break
            remaining = deadline - loop.time()
            if remaining <= 0:
                print(f"  +{_time.monotonic()-t0:.1f}s Session timeout")
                break
            wait = QUIET_PERIOD if has_measurement else remaining
            try:
                await asyncio.wait_for(quiet_event.wait(), timeout=min(wait, remaining))
            except TimeoutError:
                print(f"  +{_time.monotonic()-t0:.1f}s Quiet period expired")
                break

    return has_measurement


async def scan_for_scale(timeout: float) -> BLEDevice | None:
    """Scan until a Medisana scale is seen or timeout expires."""
    found: asyncio.Event = asyncio.Event()
    device: BLEDevice | None = None

    def on_detect(d: BLEDevice, adv: AdvertisementData) -> None:
        nonlocal device
        if found.is_set():
            return
        if looks_like_medisana(d, adv):
            print(f"Found: {d.name} ({d.address})")
            device = d
            found.set()

    async with BleakScanner(on_detect):
        try:
            await asyncio.wait_for(found.wait(), timeout=timeout)
        except TimeoutError:
            return None
    return device


async def main() -> None:
    print(f"Scanning for Medisana scale (up to {SCAN_TIMEOUT:.0f}s) — step on it...")
    device = await scan_for_scale(60.0)
    if device is None:
        print("No scale found. Step on the scale and try again.")
        return

    got_data = await collect_one_session(device, "Phase 1")
    if got_data:
        return

    # Phase 1 ended with an early disconnect (BIA cycle). The scale will
    # re-advertise immediately with the stored measurement — reconnect now.
    print(f"\nWaiting for scale to re-advertise (phase 2, up to {SCAN_TIMEOUT:.0f}s)...")
    device2 = await scan_for_scale(SCAN_TIMEOUT)
    if device2 is None:
        print("Scale did not re-advertise in time.")
        return

    await collect_one_session(device2, "Phase 2 (stored measurement)")


if __name__ == "__main__":
    asyncio.run(main())
