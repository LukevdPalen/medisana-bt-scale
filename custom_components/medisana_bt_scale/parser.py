"""Pure BLE packet decoders for the Medisana BT Scale.

These functions have no dependency on Home Assistant or bleak so they can be
unit tested in isolation (see tests/test_parser.py). The byte layouts here
were cross-verified against two independent reverse-engineering efforts:

- ESPHome component: https://github.com/bwynants/weegschaal
- openScale wiki: https://github.com/oliexdev/openScale/wiki/Medisana-BS444

Notes on things that are easy to get wrong (and were wrong in earlier
versions of this integration):

- The "trigger" command written to the CMD characteristic is NOT a fixed
  magic byte string. It is 0x02 followed by a live, little-endian, 4-byte
  timestamp using a 2010-based epoch (i.e. real Unix time minus
  EPOCH_2010_OFFSET seconds). A hardcoded capture of someone else's
  timestamp will not reliably trigger the scale.
- The scale computes body composition (fat/water/muscle/bone) internally
  and only ever transmits the final percentages - there is no raw
  bioimpedance value on this device, so no independent BIA formula should
  be applied to these numbers.
"""

from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TypeVar

# Seconds between the Unix epoch (1970-01-01) and the epoch this scale uses
# for its own timestamps (2010-01-01). Confirmed for BS410/BS444 by both
# reference implementations.
EPOCH_2010_OFFSET = 1262304000

# Flag/validity bytes at offset 0 of each packet type.
_WEIGHT_FLAG_KG = 0x1D
_WEIGHT_FLAG_LB = 0x3D
_BODY_FLAG_VALID = 0x6F
_PERSON_FLAG_VALID = 0x84

_LB_TO_KG = 0.45359237

# Person/user slot value meaning "no slot matched" in a Weight packet.
UNASSIGNED_SLOT = 0xFF


@dataclass
class WeightMeasurement:
    """A single decoded weight measurement."""

    slot: int | None  # 1..8, or None if the scale didn't attribute it to a user
    weight_kg: float
    timestamp: datetime


@dataclass
class BodyMeasurement:
    """A single decoded body-composition measurement."""

    slot: int | None  # 1..8, or None if the scale didn't attribute it to a user
    timestamp: datetime
    kcal: int
    fat_pct: float
    water_pct: float
    muscle_pct: float
    bone_pct: float


@dataclass
class PersonProfile:
    """A single decoded user-profile record for a scale slot."""

    slot: int
    male: bool
    age: int
    height_cm: int
    high_activity: bool


def _scale_timestamp_to_datetime(raw_seconds: int) -> datetime:
    """Convert the scale's 2010-epoch uint32 timestamp to a UTC datetime."""
    return datetime.fromtimestamp(raw_seconds + EPOCH_2010_OFFSET, tz=timezone.utc)


def parse_weight(data: bytes) -> WeightMeasurement | None:
    """Decode a Weight-characteristic (0x8A21) indication.

    Byte layout (19 bytes):
        0     flag: 0x1d=kg, 0x3d=lb
        1-2   weight, uint16 LE / 100
        3-4   padding (unused)
        5-8   timestamp, uint32 LE, 2010-epoch
        9-12  padding (unused)
        13    person slot 1..8, or 0xFF if unassigned
        14-18 reserved (unused)
    """
    if len(data) < 14:
        return None

    flag = data[0]
    if flag not in (_WEIGHT_FLAG_KG, _WEIGHT_FLAG_LB):
        return None

    (weight_raw,) = struct.unpack_from("<H", data, 1)
    weight_kg = weight_raw / 100.0
    if flag == _WEIGHT_FLAG_LB:
        weight_kg *= _LB_TO_KG

    (ts_raw,) = struct.unpack_from("<I", data, 5)
    timestamp = _scale_timestamp_to_datetime(ts_raw)

    slot_raw = data[13]
    slot = None if slot_raw == UNASSIGNED_SLOT else slot_raw

    return WeightMeasurement(slot=slot, weight_kg=weight_kg, timestamp=timestamp)


def parse_body(data: bytes) -> BodyMeasurement | None:
    """Decode a Body-composition-characteristic (0x8A22) indication.

    Byte layout:
        0     flag: fixed 0x6f
        1-4   timestamp, uint32 LE, 2010-epoch
        5     person slot 1..8, or 0xFF if unassigned (seen on real hardware
              alongside unassigned weight readings, though not spelled out
              in either reference source)
        6-7   kcal, uint16 LE (not masked)
        8-9   fat %,    (uint16 LE) & 0x0FFF, /10
        10-11 water %,  (uint16 LE) & 0x0FFF, /10
        12-13 muscle %, (uint16 LE) & 0x0FFF, /10
        14-15 bone %,   (uint16 LE) & 0x0FFF, /10
    """
    if len(data) < 16:
        return None

    if data[0] != _BODY_FLAG_VALID:
        return None

    (ts_raw,) = struct.unpack_from("<I", data, 1)
    timestamp = _scale_timestamp_to_datetime(ts_raw)

    slot_raw = data[5]
    slot = None if slot_raw == UNASSIGNED_SLOT else slot_raw
    (kcal,) = struct.unpack_from("<H", data, 6)
    fat_pct = _decode_masked_tenth(data, 8)
    water_pct = _decode_masked_tenth(data, 10)
    muscle_pct = _decode_masked_tenth(data, 12)
    bone_pct = _decode_masked_tenth(data, 14)

    return BodyMeasurement(
        slot=slot,
        timestamp=timestamp,
        kcal=kcal,
        fat_pct=fat_pct,
        water_pct=water_pct,
        muscle_pct=muscle_pct,
        bone_pct=bone_pct,
    )


def _decode_masked_tenth(data: bytes, offset: int) -> float:
    (raw,) = struct.unpack_from("<H", data, offset)
    return (raw & 0x0FFF) / 10.0


def parse_person(data: bytes) -> PersonProfile | None:
    """Decode a Person-characteristic (0x8A82) indication.

    This characteristic is undocumented by openScale (it enables
    indications but never parses the payload); the layout below comes from
    the weegschaal ESPHome component, which is the only known reference.

    Byte layout:
        0  flag: fixed 0x84
        1  padding (unused)
        2  person slot 1..8
        3  padding (unused)
        4  gender: 1=male, 2=female
        5  age, years
        6  height, cm
        7  padding (unused)
        8  activity: 0=normal, 3=high
    """
    if len(data) < 9:
        return None

    if data[0] != _PERSON_FLAG_VALID:
        return None

    slot = data[2]
    male = data[4] == 1
    age = data[5]
    height_cm = data[6]
    high_activity = data[8] == 3

    return PersonProfile(
        slot=slot,
        male=male,
        age=age,
        height_cm=height_cm,
        high_activity=high_activity,
    )


def build_trigger_command(now: datetime | None = None) -> bytearray:
    """Build the trigger command written to the CMD characteristic.

    The command is 0x02 followed by the current time as a little-endian
    uint32 in the scale's 2010-based epoch. The scale uses this value to
    sync its internal clock AND to trigger a full history dump; sending the
    correct current time is important so that stored measurements get
    timestamped accurately.
    """
    if now is None:
        unix_seconds = int(time.time())
    else:
        unix_seconds = int(now.timestamp())

    scale_seconds = unix_seconds - EPOCH_2010_OFFSET
    return bytearray([0x02]) + bytearray(struct.pack("<I", scale_seconds))


_TimestampedT = TypeVar("_TimestampedT", WeightMeasurement, BodyMeasurement)


def newer(current: _TimestampedT | None, candidate: _TimestampedT) -> _TimestampedT:
    """Return whichever of current/candidate has the later timestamp.

    On a real connection the scale replays its *entire* stored history each
    session (up to 30 measurements per user), not just the newest reading -
    and confirmed on real hardware, not necessarily in chronological order
    (wrap-around/reused history slots can surface stale entries anywhere in
    the sequence). Simply keeping "whichever packet arrived last" is not
    reliable; the one with the latest timestamp is.
    """
    if current is None or candidate.timestamp > current.timestamp:
        return candidate
    return current


def is_newer_timestamp(current: datetime | None, candidate: datetime) -> bool:
    """Return whether candidate is newer than current (None counts as oldest).

    Same idea as `newer()`, but for comparing a fresh reading's timestamp
    against one already persisted from a *previous* connection - a single
    BLE session can end up truncated (weak signal, a hard session timeout)
    before it replays the scale's actual latest reading, so a later poll
    must never be allowed to regress an already-known-newer value.
    """
    return current is None or candidate > current


def compute_bmi(weight_kg: float | None, height_cm: int | None) -> float | None:
    """Compute BMI locally. The scale never transmits this value itself."""
    if not weight_kg or not height_cm:
        return None
    height_m = height_cm / 100.0
    if height_m <= 0:
        return None
    return weight_kg / (height_m**2)


# Internal bookkeeping keys in a measurement record that hold a datetime and
# therefore need converting to/from a JSON-safe form when persisted across
# Home Assistant restarts (see coordinator.py's use of these).
_TIMESTAMP_KEYS = ("last_measured", "_weight_ts", "_body_ts")


def serialize_measurements(
    measurements: dict[int, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Convert a measurements dict into a JSON-serializable form for storage."""
    return {
        str(slot): {
            key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in record.items()
        }
        for slot, record in measurements.items()
    }


def deserialize_measurements(
    data: dict[str, dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    """Reverse of serialize_measurements(): restore a measurements dict."""
    return {
        int(slot_str): {
            key: (
                datetime.fromisoformat(value)
                if key in _TIMESTAMP_KEYS and isinstance(value, str)
                else value
            )
            for key, value in record.items()
        }
        for slot_str, record in data.items()
    }
