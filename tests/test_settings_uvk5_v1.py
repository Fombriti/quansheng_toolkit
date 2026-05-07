"""
Settings-registry tests for the K5 V1 / K1 stock module.

Locks in offsets and bit packings observed on a real UV-K1 (firmware
7.03.01). Anything that changes here without an explicit hardware-
validation note is almost certainly a bug.
"""
from __future__ import annotations

import pytest

from quansheng_toolkit.kradio import settings_uvk5_v1 as smm


# ---------------------------------------------------------------------------
# Spec sanity
# ---------------------------------------------------------------------------

def test_registry_has_expected_keys():
    keys = set(smm.SETTINGS_REGISTRY.keys())
    # Pick a few that the GUI / CSV importer specifically rely on.
    must_have = {
        "squelch", "vox_switch", "vox_level", "mic_gain",
        "channel_display_mode", "battery_save", "dual_watch",
        "backlight_min", "backlight_max", "backlight_time",
        "ste", "key_lock", "auto_keypad_lock",
        "button_beep", "keyM_longpress_action",
        "key1_shortpress_action", "key2_longpress_action",
        "scan_resume_mode", "alarm_mode", "roger_beep",
        "tx_vfo", "battery_type", "voice", "power_on_dispmode",
        "logo_line1", "logo_line2",
        "int_flock",
    }
    missing = must_have - keys
    assert not missing, f"missing keys: {missing}"


def test_list_settings_is_sorted():
    names = [s.name for s in smm.list_settings()]
    assert names == sorted(names)


# ---------------------------------------------------------------------------
# Offsets: address-perfect locks against the K5 V1 layout
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name, expected_addr", [
    ("call_channel",         0x0E70),
    ("squelch",              0x0E71),
    ("max_talk_time",        0x0E72),
    ("noaa_autoscan",        0x0E73),
    ("key_lock",             0x0E74),
    ("vox_switch",           0x0E75),
    ("vox_level",            0x0E76),
    ("mic_gain",             0x0E77),
    ("backlight_min",        0x0E78),
    ("backlight_max",        0x0E78),
    ("channel_display_mode", 0x0E79),
    ("crossband",            0x0E7A),
    ("battery_save",         0x0E7B),
    ("dual_watch",           0x0E7C),
    ("backlight_time",       0x0E7D),
    ("ste",                  0x0E7E),
    ("button_beep",          0x0E90),
    ("keyM_longpress_action", 0x0E90),
    ("scan_resume_mode",     0x0E95),
    ("auto_keypad_lock",     0x0E96),
    ("power_on_dispmode",    0x0E97),
    ("voice",                0x0EA0),
    ("alarm_mode",           0x0EA8),
    ("roger_beep",           0x0EA9),
    ("rp_ste",               0x0EAA),
    ("tx_vfo",               0x0EAB),
    ("battery_type",         0x0EAC),
    ("logo_line1",           0x0EB0),
    ("logo_line2",           0x0EC0),
    ("int_flock",            0x0F40),
])
def test_setting_addresses(name, expected_addr):
    assert smm.SETTINGS_REGISTRY[name].addr == expected_addr


# ---------------------------------------------------------------------------
# Bit packing — guards the Beep regression we just fixed
# ---------------------------------------------------------------------------

def test_button_beep_lives_at_bit_0():
    """
    CHIRP source `u8 keyM_longpress_action:7, button_beep:1` is MSB-first
    inside a single byte, so:
      * bits 7..1 → keyM_longpress_action
      * bit 0     → button_beep
    On a real K1 dump the byte at 0x0E90 is 0x01 and the radio menu shows
    Beep enabled. Anything else here is a regression.
    """
    spec = smm.SETTINGS_REGISTRY["button_beep"]
    assert spec.bit_offset == 0
    assert spec.bit_width == 1


def test_button_beep_decodes_real_radio_byte():
    img = bytearray(b"\x00" * 0x2000)
    img[0x0E90] = 0x01       # exact byte from the live K1 dump
    assert smm.read_setting(bytes(img), "button_beep") is True
    assert smm.read_setting(bytes(img), "keyM_longpress_action") == "NONE"


def test_button_beep_round_trip():
    img = bytearray(b"\x00" * 0x2000)
    smm.apply_setting(img, "button_beep", "on")
    assert img[0x0E90] & 0x01 == 1
    assert img[0x0E90] & 0xFE == 0, "writing beep changed keyM bits"
    smm.apply_setting(img, "button_beep", "off")
    assert img[0x0E90] & 0x01 == 0


def test_keyM_action_round_trip_preserves_beep():
    img = bytearray(b"\x00" * 0x2000)
    smm.apply_setting(img, "button_beep", "on")
    smm.apply_setting(img, "keyM_longpress_action", "MONITOR")
    assert smm.read_setting(bytes(img), "button_beep") is True
    assert smm.read_setting(bytes(img), "keyM_longpress_action") == "MONITOR"


def test_backlight_min_max_share_byte_without_clobber():
    img = bytearray(b"\x00" * 0x2000)
    smm.apply_setting(img, "backlight_min", "3")
    smm.apply_setting(img, "backlight_max", "12")
    assert smm.read_setting(bytes(img), "backlight_min") == 3
    assert smm.read_setting(bytes(img), "backlight_max") == 12
    # Re-reads must remain stable.
    smm.apply_setting(img, "backlight_min", "5")
    assert smm.read_setting(bytes(img), "backlight_min") == 5
    assert smm.read_setting(bytes(img), "backlight_max") == 12


# ---------------------------------------------------------------------------
# String fields: logo encoding (CHIRP "logo" pattern) is tricky
# ---------------------------------------------------------------------------

def test_logo_lines_use_chirp_logo_encoding():
    img = bytearray(b"\xff" * 0x2000)
    smm.apply_setting(img, "logo_line1", "HELLO K1")
    line = bytes(img[0x0EB0:0x0EC0])
    # 8 ASCII bytes ("HELLO K1"), then four 0x00 (null pad up to display
    # length 12), then 0x00 + 0xFF*3 trail (CHIRP "logo" pattern).
    assert line[:8] == b"HELLO K1"
    assert line[8:12] == b"\x00\x00\x00\x00"
    assert line[12] == 0x00
    assert line[13:16] == b"\xff\xff\xff"


def test_logo_decodes_back_cleanly():
    img = bytearray(b"\xff" * 0x2000)
    smm.apply_setting(img, "logo_line2", "TESTRADIO")
    assert smm.read_setting(bytes(img), "logo_line2") == "TESTRADIO"


# ---------------------------------------------------------------------------
# Enums: bounds + round-trip
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("key, value, expected_byte_value", [
    ("channel_display_mode", "Frequency",   0),
    ("channel_display_mode", "Channel No",  1),
    ("channel_display_mode", "Channel Name", 2),
    ("crossband",            "Band A",      1),
    ("dual_watch",           "Band B",      2),
    ("battery_save",         "1:4",         4),
    ("scan_resume_mode",     "CARRIER",     1),
    ("tx_vfo",               "B",           1),
])
def test_enum_round_trip(key, value, expected_byte_value):
    img = bytearray(b"\x00" * 0x2000)
    smm.apply_setting(img, key, value)
    spec = smm.SETTINGS_REGISTRY[key]
    if spec.bit_width == 8:
        assert img[spec.addr] == expected_byte_value
    else:
        mask = (1 << spec.bit_width) - 1
        raw = (img[spec.addr] >> spec.bit_offset) & mask
        assert raw == expected_byte_value
    assert smm.read_setting(bytes(img), key) == value


def test_int_out_of_range_raises():
    img = bytearray(b"\x00" * 0x2000)
    with pytest.raises(ValueError):
        smm.apply_setting(img, "squelch", "99")  # bounds (0, 9)


def test_unknown_setting_raises():
    img = bytearray(b"\x00" * 0x2000)
    with pytest.raises(ValueError):
        smm.apply_setting(img, "definitely_not_a_setting", "x")


# ---------------------------------------------------------------------------
# Synthetic EEPROM smoke test — exercises the full decode path
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# DTMF block at 0x0ED0..0x0F18
# ---------------------------------------------------------------------------

def test_dtmf_offsets_match_kk7ds_layout():
    expected = {
        "dtmf_side_tone":               0x0ED0,
        "dtmf_decode_response":         0x0ED3,
        "dtmf_auto_reset_time":         0x0ED4,
        "dtmf_preload_time":            0x0ED5,
        "dtmf_first_code_persist_time": 0x0ED6,
        "dtmf_hash_persist_time":       0x0ED7,
        "dtmf_code_persist_time":       0x0ED8,
        "dtmf_code_interval_time":      0x0ED9,
        "dtmf_permit_remote_kill":      0x0EDA,
        "dtmf_up_code":                 0x0EF8,
        "dtmf_down_code":               0x0F08,
    }
    for key, addr in expected.items():
        assert smm.SETTINGS_REGISTRY[key].addr == addr


def test_dtmf_codes_use_null_padding():
    img = bytearray(b"\xff" * 0x2000)
    smm.apply_setting(img, "dtmf_up_code", "1234")
    raw = bytes(img[0x0EF8:0x0F08])
    assert raw[:4] == b"1234"
    assert raw[4:] == b"\x00" * 12  # null pad, not 0xFF


def test_dtmf_decode_response_round_trip():
    img = bytearray(b"\x00" * 0x2000)
    smm.apply_setting(img, "dtmf_decode_response", "REPLY")
    assert smm.read_setting(bytes(img), "dtmf_decode_response") == "REPLY"
    assert img[0x0ED3] == 2


# ---------------------------------------------------------------------------
# Power-on password (u32le at 0x0E98)
# ---------------------------------------------------------------------------

def test_pwron_password_is_u32_at_0xE98():
    spec = smm.SETTINGS_REGISTRY["pwron_password"]
    assert spec.kind == "u32le"
    assert spec.addr == 0x0E98


def test_pwron_password_round_trip():
    img = bytearray(b"\xff" * 0x2000)
    smm.apply_setting(img, "pwron_password", "12345678")
    assert smm.read_setting(bytes(img), "pwron_password") == 12345678
    raw = int.from_bytes(img[0x0E98:0x0E9C], "little")
    assert raw == 12345678


def test_pwron_password_clear_with_zero():
    img = bytearray(b"\xff" * 0x2000)
    smm.apply_setting(img, "pwron_password", "0")
    assert smm.read_setting(bytes(img), "pwron_password") == 0


def test_pwron_password_rejects_too_large():
    img = bytearray(b"\x00" * 0x2000)
    with pytest.raises(ValueError):
        smm.apply_setting(img, "pwron_password", "100000000")


# ---------------------------------------------------------------------------
# FM presets — fm_freq kind
# ---------------------------------------------------------------------------

def test_fm_preset_count_matches_k5v1_layout():
    fm_keys = [k for k in smm.SETTINGS_REGISTRY if k.startswith("fm_preset_")]
    assert len(fm_keys) == 20  # K5 V1 stock has 20 presets at 0x0E40


def test_fm_preset_addresses_are_contiguous_u16():
    addrs = sorted(smm.SETTINGS_REGISTRY[f"fm_preset_{i:02d}"].addr
                   for i in range(1, 21))
    # First slot at 0x0E40, then +2 for each subsequent.
    expected = [0x0E40 + i * 2 for i in range(20)]
    assert addrs == expected


def test_fm_preset_round_trip():
    img = bytearray(b"\xff" * 0x2000)
    smm.apply_setting(img, "fm_preset_01", "100.5 MHz")
    smm.apply_setting(img, "fm_preset_02", "88.0")
    smm.apply_setting(img, "fm_preset_03", "OFF")
    assert smm.read_setting(bytes(img), "fm_preset_01") == "100.50 MHz"
    assert smm.read_setting(bytes(img), "fm_preset_02") == "88.00 MHz"
    assert smm.read_setting(bytes(img), "fm_preset_03") == ""


def test_fm_preset_rejects_out_of_band():
    img = bytearray(b"\xff" * 0x2000)
    with pytest.raises(ValueError):
        smm.apply_setting(img, "fm_preset_01", "150 MHz")


def test_fm_preset_byte_format_is_freq_times_100():
    img = bytearray(b"\x00" * 0x2000)
    smm.apply_setting(img, "fm_preset_01", "100.50")
    raw = int.from_bytes(img[0x0E40:0x0E42], "little")
    assert raw == 10050


def test_synthetic_fixture_decodes_consistently(k1_stock_eeprom_bytes):
    assert smm.read_setting(k1_stock_eeprom_bytes, "squelch") == 3
    assert smm.read_setting(k1_stock_eeprom_bytes, "vox_switch") is False
    assert smm.read_setting(k1_stock_eeprom_bytes, "mic_gain") == 2
    assert smm.read_setting(k1_stock_eeprom_bytes, "battery_type") == "1600 mAh"
    assert smm.read_setting(
        k1_stock_eeprom_bytes, "channel_display_mode") == "Channel Name"
    assert smm.read_setting(k1_stock_eeprom_bytes, "button_beep") is True
    assert smm.read_setting(k1_stock_eeprom_bytes, "logo_line1") == "TESTRADIO"
    assert smm.read_setting(k1_stock_eeprom_bytes, "logo_line2") == "K1 STOCK"
