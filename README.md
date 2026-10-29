# Medisana BT Scale — Home Assistant integration

A local, native Home Assistant integration for Medisana Bluetooth
Low Energy (BLE) bathroom scales. No ESPHome device, gateway, or cloud account
required — Home Assistant talks to the scale directly over your existing
Bluetooth adapter.

## Supported models

| Model  | Status              | Notes                                       |
|--------|---------------------|---------------------------------------------|
| BS444  | Confirmed working   | Developed and tested on real hardware       |
| BS440  | Likely compatible   | Same GATT service UUID and protocol         |
| BS410  | Likely compatible   | Same GATT service UUID and 2010-epoch timestamps |
| BS430  | Not supported       | Uses raw Unix timestamps; different advertised name prefix |

If you test this on a BS410 or BS440 and it works, please open an issue so the
compatibility table can be updated.

## Features

- **Bluetooth discovery** — the scale shows up automatically under
  *Settings → Devices & services* when it's nearby and awake; manual setup
  (scan-and-pick) also works if auto-discovery doesn't fire.
- **Multi-user support** — the scale stores up to **8 user slots**. Each
  measurement is tagged with the slot it was taken for, and each slot gets
  its own device and set of entities.
- **Link scale slots to Home Assistant persons** — during setup (and any time
  after, via *Configure*) you can map each of the 8 slots to a Home
  Assistant `person`. Mapped slots are named after that person (e.g. device
  "Luke" instead of "User 3") instead of a generic label. The setup flow
  makes a best-effort live connection to the scale to show hints (gender,
  age, height) for whichever slots it currently holds data for, so it's
  easier to tell which physical slot belongs to whom.
- **Event-driven, not polling** — the scale is only reachable for a few
  seconds after someone steps on it, so this integration doesn't poll on a
  fixed interval. It listens passively for the scale's Bluetooth
  advertisements and only opens an active connection when the scale is
  actually there, then disconnects again once the data's been pulled.
- **Full history replay** — the scale stores up to the last 30 measurements
  per user; the integration processes all of them on each connection, not
  just the newest one — and since that history isn't always delivered in
  chronological order, it's the record with the latest *timestamp* that
  wins per slot, not whichever packet happens to arrive last.
- **"Unrecognized" debug device** — the scale occasionally fails to tell
  which user stepped on it and tags the reading as unassigned instead of a
  real slot (confirmed on real hardware). Rather than silently dropping
  that data, it's surfaced under its own **Medisana BT Scale Unrecognized**
  device so you can still see it happened.
- **Survives a Home Assistant restart** — the last known reading per slot
  is persisted to disk and restored before entities are set up, so they
  show your last real weigh-in immediately rather than going blank until
  the scale happens to be used again. Removing the integration (not just
  restarting HA) clears this stored data, so a fresh re-add starts clean.

### Entities created (per mapped/generic slot)

| Entity | Description |
|---|---|
| Weight | kg |
| BMI | computed locally from weight + the slot's stored height |
| Body Fat | % |
| Body Water | % |
| Muscle Mass | % |
| Bone Mass | % |
| Daily Calorie Requirement | kcal |
| Age | years |
| Height | cm |
| Male / Female | binary sensors |
| High Activity Level | binary sensor |

All values for fat/water/muscle/bone/kcal come directly from the scale's own
on-device calculation — it never transmits a raw bioimpedance value, so no
independent formula is applied on the Home Assistant side.

## Requirements

- Home Assistant 2024.x or newer, with the built-in `bluetooth` integration
  enabled (a Bluetooth adapter, built-in or USB, that HA can see).
- A supported Medisana Bluetooth scale (see the supported models table above).

## Installation

### HACS (custom repository)

1. HACS → **Integrations** → ⋮ → **Custom repositories** → add this
   repository URL, category **Integration**.
2. Find **Medisana BT Scale** in HACS, download it, then restart Home
   Assistant.

### Manual

1. Copy `custom_components/medisana_bt_scale/` into your Home Assistant
   config's `custom_components/` directory.
2. Restart Home Assistant.

## Setup

1. **Settings → Devices & services → Add Integration → Medisana BT Scale.**
2. If the scale was recently active, it may already be listed as a
   discovered device — otherwise it'll be found by scanning.
3. You'll be shown a form with 8 slots. If the scale is awake, hints
   (gender/age/height) are shown per slot it currently has data for — step
   on the scale first if you want these to populate. Map each slot to a
   Home Assistant `person`, or leave it "Not assigned".
4. Finish — entities for all 8 slots are created; mapped ones are named
   after the linked person.

You can redo the mapping at any time from the integration's **Configure**
option — this is also how you fill in a slot's mapping later if it wasn't
detected during initial setup (e.g. because no one had stepped on the scale
yet).

## How it works

Home Assistant listens for the scale's Bluetooth advertisements. When one
is seen, it opens a GATT connection, enables indications on the weight,
body-composition, and person-profile characteristics, and writes a trigger
command telling the scale to replay its stored history. The scale then
sends up to 30 stored measurements per user; each is decoded, attributed to
the correct user slot, merged with that slot's stored profile (for BMI),
and the connection is closed again.

### Two-phase BLE protocol

The BS444 (and compatible models) use a two-phase protocol:

1. **Phase 1** (~10 s): you step on the scale; it sends an unrecognized-user
   (slot=255) person record while it runs the BIA cycle, then disconnects.
2. **Phase 2**: the scale re-advertises immediately with the stored result.
   The integration reconnects and receives the real slot, weight, and body
   composition data.

The integration handles both phases automatically. `POLL_COOLDOWN_SECONDS`
is set to `0.0` so the phase-2 re-advertisement is never blocked.

### Protocol reference

The scale uses a **proprietary BLE GATT service**, not the standard
Bluetooth SIG Weight/Body-Composition services. Cross-checked against two
independent reverse-engineering projects — the
[weegschaal ESPHome component](https://github.com/bwynants/weegschaal) and
the [openScale wiki](https://github.com/oliexdev/openScale/wiki/Medisana-BS444)
plus openScale's Android drivers — both agree on the GATT layer; only
weegschaal documents/uses the Person characteristic, which is what makes
per-slot routing and BMI possible.

| Name | UUID |
|---|---|
| Service | `000078b2-0000-1000-8000-00805f9b34fb` |
| Weight (indicate) | `00008a21-0000-1000-8000-00805f9b34fb` |
| Body composition (indicate) | `00008a22-0000-1000-8000-00805f9b34fb` |
| Command (write) | `00008a81-0000-1000-8000-00805f9b34fb` |
| Person / user profile (indicate) | `00008a82-0000-1000-8000-00805f9b34fb` |

Device fingerprint used for discovery: Bluetooth MAC OUI `E4:54:EB`,
manufacturer ID `0x00DC` (Medisana/Transtek).

**Handshake:** enable indications on Weight, Body, and Person, then write
`0x02` + a live 4-byte little-endian timestamp to the Command characteristic.
The timestamp uses a **2010-based epoch** (real Unix time minus
`1262304000`) — this must be computed fresh each time; a fixed/hardcoded
byte string will not reliably trigger the scale.

**Weight packet** (19 bytes):

| Offset | Field | Encoding |
|---|---|---|
| 0 | flag | `0x1d`=kg, `0x3d`=lb (× 0.45359237 → kg) |
| 1–2 | weight | uint16 LE ÷ 100 |
| 5–8 | timestamp | uint32 LE, 2010-epoch |
| 13 | user slot | `1..8`, `0xFF` = unassigned |

**Body composition packet:**

| Offset | Field | Encoding |
|---|---|---|
| 0 | flag | fixed `0x6f` |
| 1–4 | timestamp | uint32 LE, 2010-epoch |
| 5 | user slot | `1..8` |
| 6–7 | kcal | uint16 LE, unmasked |
| 8–9 / 10–11 / 12–13 / 14–15 | fat / water / muscle / bone % | `(uint16 LE) & 0x0FFF` ÷ 10 |

**Person / profile packet** (only documented by weegschaal — openScale
enables indications on this characteristic but never parses it):

| Offset | Field | Encoding |
|---|---|---|
| 0 | flag | fixed `0x84` |
| 2 | user slot | `1..8` |
| 4 | gender | `1`=male, `2`=female |
| 5 | age | years |
| 6 | height | cm |
| 8 | activity | `0`=normal, `3`=high |

BMI is computed locally as `weight_kg / (height_m ** 2)` once both a Weight
and Person record exist for a slot — the scale never transmits it directly.

## Known limitations

- **Data only updates when someone steps on the scale.** This is a push
  device, not a poll target — there's no "refresh now" outside of that.
- **Slot detection during setup is best-effort.** If the scale hasn't been
  stepped on recently, its Person-profile data may not be available yet;
  the mapping form still works, just without hints. Revisit via
  **Configure** once someone has used the scale.
- **No raw impedance access.** Fat/water/muscle/bone come pre-computed from
  the scale's firmware; there's no way to apply a different body-composition
  formula to this device's data.
- The scale must not be actively connected to the Medisana phone app at the
  same time — BLE devices generally only accept one active connection.

## Development

Packet parsing (`custom_components/medisana_bt_scale/parser.py`) is
dependency-free and unit tested against real captured bytes:

```bash
python3 -m pip install pytest
python3 -m pytest tests/
```

## Credits

- Protocol reverse-engineering: [weegschaal](https://github.com/bwynants/weegschaal) (ESPHome) and the [openScale project](https://github.com/oliexdev/openScale).

## License

MIT.
