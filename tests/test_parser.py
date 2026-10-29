"""Unit tests for the pure BLE packet decoders in parser.py.

The "real capture" fixtures below are taken directly from the example byte
dumps documented in the ESPHome weegschaal component (the only reference
that documents the Person characteristic) and cross-checked by hand against
the byte tables in README.md. Weight and Body share the same timestamp and
user slot because they were captured in the same session.
"""

from datetime import datetime, timezone

import pytest

from conftest import load_component_module

parser = load_component_module("parser")

EPOCH_2010_OFFSET = parser.EPOCH_2010_OFFSET
build_trigger_command = parser.build_trigger_command
compute_bmi = parser.compute_bmi
deserialize_measurements = parser.deserialize_measurements
is_newer_timestamp = parser.is_newer_timestamp
newer = parser.newer
parse_body = parser.parse_body
parse_person = parser.parse_person
parse_weight = parser.parse_weight
serialize_measurements = parser.serialize_measurements

# Real capture: 1d 80 25 00 fe 80 0f f4 0d 6a 17 00 ff 01 09 00 00 00 00
# -> flag=kg, weight=96.00kg, slot=1
WEIGHT_KG_PACKET = bytes.fromhex("1d802500fe800ff40d6a1700ff01090000000000")[:19]

# Real capture: 6f 80 0f f4 0d 01 21 0c 57 f1 e3 f1 5b f1 23 f0 00 00 00
# -> flag=valid, slot=1, same timestamp as the weight packet above
BODY_PACKET = bytes.fromhex("6f800ff40d01210c57f1e3f15bf12300000000")[:19]

# Real capture: 84 53 02 80 01 34 b6 e0 00 ... (from a Person history dump)
# -> flag=valid, slot=2, male, age=52, height=182cm, activity=normal
PERSON_PACKET = bytes.fromhex("845302800134b6e0000000000000000000000000")


def test_parse_weight_kg_real_capture():
    result = parse_weight(WEIGHT_KG_PACKET)
    assert result is not None
    assert result.slot == 1
    assert result.weight_kg == pytest.approx(96.00)


def test_parse_weight_lb_converts_to_kg():
    # flag=0x3d (lb), weight raw 15000 -> 150.00 lb -> convert to kg
    data = (
        bytes([0x3D])
        + (15000).to_bytes(2, "little")  # offsets 1-2: weight
        + b"\x00" * 2  # offsets 3-4: pad
        + (0).to_bytes(4, "little")  # offsets 5-8: timestamp
        + b"\x00" * 4  # offsets 9-12: pad
        + bytes([5])  # offset 13: slot
        + b"\x00" * 5  # offsets 14-18: reserved
    )
    result = parse_weight(data)
    assert result is not None
    assert result.slot == 5
    assert result.weight_kg == pytest.approx(150 * 0.45359237)


def test_parse_weight_invalid_flag_returns_none():
    data = bytearray(WEIGHT_KG_PACKET)
    data[0] = 0x99
    assert parse_weight(bytes(data)) is None


def test_parse_weight_unassigned_slot_is_none():
    data = bytearray(WEIGHT_KG_PACKET)
    data[13] = 0xFF
    result = parse_weight(bytes(data))
    assert result is not None
    assert result.slot is None


def test_parse_weight_timestamp_uses_2010_epoch():
    result = parse_weight(WEIGHT_KG_PACKET)
    assert result is not None
    # bytes 5-8 decode to 234098560 in scale time
    expected = datetime.fromtimestamp(
        234098560 + EPOCH_2010_OFFSET, tz=timezone.utc
    )
    assert result.timestamp == expected


def test_parse_body_real_capture():
    result = parse_body(BODY_PACKET)
    assert result is not None
    assert result.slot == 1
    assert result.kcal == 3105
    assert result.fat_pct == pytest.approx(34.3)
    assert result.water_pct == pytest.approx(48.3)
    assert result.muscle_pct == pytest.approx(34.7)
    assert result.bone_pct == pytest.approx(3.5)


def test_parse_body_shares_timestamp_with_weight_packet():
    weight = parse_weight(WEIGHT_KG_PACKET)
    body = parse_body(BODY_PACKET)
    assert weight is not None
    assert body is not None
    assert weight.timestamp == body.timestamp


def test_parse_body_invalid_flag_returns_none():
    data = bytearray(BODY_PACKET)
    data[0] = 0x00
    assert parse_body(bytes(data)) is None


def test_parse_body_unassigned_slot_is_none():
    # Confirmed on real hardware: the scale can fail to recognize the user
    # and tag a body-composition reading with slot 0xFF too, not just weight.
    data = bytearray(BODY_PACKET)
    data[5] = 0xFF
    result = parse_body(bytes(data))
    assert result is not None
    assert result.slot is None


def test_parse_person_real_capture():
    result = parse_person(PERSON_PACKET)
    assert result is not None
    assert result.slot == 2
    assert result.male is True
    assert result.age == 52
    assert result.height_cm == 182
    assert result.high_activity is False


def test_parse_person_female_and_high_activity():
    data = bytearray(PERSON_PACKET)
    data[4] = 2  # female
    data[8] = 3  # high activity
    result = parse_person(bytes(data))
    assert result is not None
    assert result.male is False
    assert result.high_activity is True


def test_parse_person_invalid_flag_returns_none():
    data = bytearray(PERSON_PACKET)
    data[0] = 0x00
    assert parse_person(bytes(data)) is None


def test_build_trigger_command_layout():
    now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    command = build_trigger_command(now)
    assert command[0] == 0x02
    assert len(command) == 5
    scale_seconds = int(now.timestamp()) - EPOCH_2010_OFFSET
    assert command[1:] == bytearray(scale_seconds.to_bytes(4, "little"))


def test_build_trigger_command_default_uses_current_time():
    # Default (no `now`) must encode a recent timestamp so the scale's clock
    # syncs correctly. A value near zero would reset it to 2010.
    import time as _time
    before = int(_time.time()) - EPOCH_2010_OFFSET
    command = build_trigger_command()
    after = int(_time.time()) - EPOCH_2010_OFFSET
    ts = int.from_bytes(command[1:], "little")
    assert before <= ts <= after


def test_compute_bmi():
    assert compute_bmi(96.0, 182) == pytest.approx(96.0 / (1.82**2))
    assert compute_bmi(None, 182) is None
    assert compute_bmi(80.0, None) is None
    assert compute_bmi(80.0, 0) is None


def _weight_at(day: int, kg: float):
    ts = datetime(2024, 1, day, tzinfo=timezone.utc)
    return parser.WeightMeasurement(slot=1, weight_kg=kg, timestamp=ts)


def test_newer_picks_later_timestamp_regardless_of_arrival_order():
    # A real connection replays the scale's whole history, not necessarily
    # in chronological order (confirmed on real hardware) - `newer` is what
    # a session uses to keep the actual latest reading per slot instead of
    # whichever packet happened to arrive last.
    day1, day5, day3 = _weight_at(1, 70.0), _weight_at(5, 71.0), _weight_at(3, 72.0)

    current = None
    for candidate in (day1, day5, day3):  # day5 arrives before day3
        current = newer(current, candidate)

    assert current is day5


def test_newer_keeps_current_when_candidate_is_older():
    later, earlier = _weight_at(10, 70.0), _weight_at(2, 65.0)
    assert newer(later, earlier) is later


def test_newer_with_no_current_returns_candidate():
    only = _weight_at(1, 80.0)
    assert newer(None, only) is only


def test_is_newer_timestamp():
    day1 = datetime(2024, 1, 1, tzinfo=timezone.utc)
    day5 = datetime(2024, 1, 5, tzinfo=timezone.utc)

    assert is_newer_timestamp(None, day1) is True
    assert is_newer_timestamp(day1, day5) is True
    assert is_newer_timestamp(day5, day1) is False
    assert is_newer_timestamp(day1, day1) is False


def test_serialize_measurements_is_json_safe():
    ts = datetime(2024, 1, 15, 8, 30, tzinfo=timezone.utc)
    measurements = {
        1: {"weight": 65.8, "last_measured": ts, "_weight_ts": ts, "male": True, "age": 39}
    }

    serialized = serialize_measurements(measurements)

    assert serialized == {
        "1": {
            "weight": 65.8,
            "last_measured": ts.isoformat(),
            "_weight_ts": ts.isoformat(),
            "male": True,
            "age": 39,
        }
    }
    # Confirm it's genuinely JSON-serializable (datetimes would raise here).
    import json

    json.dumps(serialized)


def test_serialize_then_deserialize_measurements_round_trips():
    ts = datetime(2024, 1, 15, 8, 30, tzinfo=timezone.utc)
    original = {
        0: {"weight": 70.1, "last_measured": ts, "_weight_ts": ts},
        1: {
            "weight": 65.8,
            "bmi": 21.5,
            "last_measured": ts,
            "_weight_ts": ts,
            "_body_ts": ts,
            "male": True,
            "female": False,
            "age": 39,
            "size": 175,
            "high_activity": True,
        },
    }

    restored = deserialize_measurements(serialize_measurements(original))

    assert restored == original


def test_merge_across_sessions_never_regresses_to_an_older_reading():
    # A cross-session equivalent of test_newer_picks_later_timestamp_...:
    # a later, truncated connection must not overwrite an already-known
    # newer weight with a stale one it happened to catch instead.
    stored_ts = datetime(2024, 1, 10, tzinfo=timezone.utc)
    stale_candidate_ts = datetime(2024, 1, 3, tzinfo=timezone.utc)
    fresh_candidate_ts = datetime(2024, 1, 15, tzinfo=timezone.utc)

    assert is_newer_timestamp(stored_ts, stale_candidate_ts) is False
    assert is_newer_timestamp(stored_ts, fresh_candidate_ts) is True
