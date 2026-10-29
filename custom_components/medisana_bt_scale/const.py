"""Constants for the Medisana BT Scale integration."""

DOMAIN = "medisana_bt_scale"

# BLE UUIDs for Medisana BT Scale.
# Cross-verified against:
# - https://github.com/bwynants/weegschaal (ESPHome component)
# - https://github.com/oliexdev/openScale/wiki/Medisana-BS444
SERVICE_UUID = "000078b2-0000-1000-8000-00805f9b34fb"
CHAR_WEIGHT_UUID = "00008a21-0000-1000-8000-00805f9b34fb"  # Weight measurement (indicate)
CHAR_BODY_UUID = "00008a22-0000-1000-8000-00805f9b34fb"    # Body composition (indicate)
CHAR_COMMAND_UUID = "00008a81-0000-1000-8000-00805f9b34fb" # Command register (write)
CHAR_PERSON_UUID = "00008a82-0000-1000-8000-00805f9b34fb"  # Person/user profile (indicate)
#                                                             openScale calls this
#                                                             "Custom5" and never reads
#                                                             it; only weegschaal
#                                                             documents its contents.

# Discovery heuristics. The scale's advertised name is an opaque numeric
# string (confirmed on real hardware, e.g. "013198000000000000"), not
# something containing "BS4" as earlier assumed. The leading 6 digits are a
# real, documented family prefix (per openScale's own name-based heuristic
# for choosing 2010-epoch vs Unix-epoch timestamp decoding): "013197",
# "013198", and "0202B6" all identify BS410/BS440/BS444-family scales,
# which use the 2010-epoch this integration assumes. BS430 advertises under
# a different prefix ("0203B*") and uses raw Unix timestamps instead - not
# supported here.
#
# MAC OUI matching only works on real Home Assistant hosts (Linux/BlueZ),
# where bleak reports the scale's actual Bluetooth MAC address. On macOS,
# CoreBluetooth hands bleak a per-app pseudo-UUID instead of a real MAC
# address, so this check never matches there - the name-prefix check is
# what actually works for local testing on a Mac (see real_test.py).
MEDISANA_MANUFACTURER_ID = 0x00DC
MEDISANA_MAC_OUI = "E4:54:EB"
MEDISANA_NAME_PREFIXES = ("013197", "013198", "0202B6")

# User configuration
MAX_USERS = 8
CONF_SLOT_MAP = "slot_map"
SLOT_NOT_ASSIGNED = "not_assigned"

# Confirmed on real hardware: the scale sometimes fails to recognize which
# user stepped on it (e.g. after a firmware auto-detection miss) and tags
# the reading with slot 0xFF instead of a real 1..8 slot. Rather than
# silently dropping that data, it's routed to this synthetic "debug" slot
# so it's still visible in Home Assistant. It's never offered as a mapping
# target in the config/options flow - it's always just itself.
DEBUG_SLOT = 0
DEBUG_SLOT_NAME = "Medisana BT Scale Unrecognized"

# Timing tuning for an active-connect session: how long to wait with no new
# indications before assuming the scale's history dump is finished, and the
# hard cap for the whole session regardless. A full history dump is up to
# 30 measurements per user (weight + body pair each, confirmed on real
# hardware with ~24 of them) - some margin over what's been observed.
QUIET_PERIOD_SECONDS = 4.0
SESSION_TIMEOUT_SECONDS = 30.0
PROBE_TIMEOUT_SECONDS = 8.0
# The BS444 uses a two-phase BLE protocol: phase 1 (~10 s) ends with the
# scale disconnecting, then it re-advertises immediately with the stored
# measurement (phase 2). A non-zero cooldown here would block that second
# critical connection, so we set it to zero and rely on the session lock
# in _async_poll_scale to prevent truly concurrent connections.
POLL_COOLDOWN_SECONDS = 0.0

# Measurement keys
ATTR_WEIGHT = "weight"
ATTR_BMI = "bmi"
ATTR_FAT = "fat"
ATTR_TBW = "tbw"  # Total Body Water
ATTR_MUSCLE = "muscle"
ATTR_BONE = "bone"
ATTR_KCAL = "kcal"
ATTR_AGE = "age"
ATTR_SIZE = "size"
ATTR_MALE = "male"
ATTR_FEMALE = "female"
ATTR_HIGH_ACTIVITY = "high_activity"
