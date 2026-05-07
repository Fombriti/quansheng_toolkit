"""Unit tests for the settings decoder + writable settings registry."""
from __future__ import annotations

import pytest

from quansheng_toolkit.kradio import settings as setmod


class TestDecode:
    def test_decode_returns_settings(self, sample_eeprom_bytes):
        s = setmod.decode_settings(sample_eeprom_bytes)
        assert s.firmware_version  # the sample is from a flashed radio
        assert 0 <= s.general.squelch <= 9
        assert s.general.tx_vfo in ("A", "B")

    def test_too_short_image_rejected(self):
        with pytest.raises(ValueError):
            setmod.decode_settings(b"\x00" * 100)

    def test_session_state_pairs_make_sense(self, sample_eeprom_bytes):
        s = setmod.decode_settings(sample_eeprom_bytes)
        # ScreenChannel_A and MrChannel_A should match in normal MR mode.
        assert s.session.screen_channel_a == s.session.mr_channel_a


class TestRegistry:
    def test_registry_not_empty(self):
        specs = setmod.list_settings()
        assert len(specs) > 5

    def test_squelch_present(self):
        assert "squelch" in setmod.SETTINGS_REGISTRY

    def test_logo_lines_present(self):
        assert "logo_line1" in setmod.SETTINGS_REGISTRY
        assert "logo_line2" in setmod.SETTINGS_REGISTRY


class TestApplySetting:
    def test_squelch_int(self, sample_eeprom):
        setmod.apply_setting(sample_eeprom, "squelch", "5")
        s = setmod.decode_settings(bytes(sample_eeprom))
        assert s.general.squelch == 5

    def test_squelch_out_of_range(self, sample_eeprom):
        with pytest.raises(ValueError):
            setmod.apply_setting(sample_eeprom, "squelch", "99")

    def test_vox_switch_bool(self, sample_eeprom):
        setmod.apply_setting(sample_eeprom, "vox_switch", "on")
        s = setmod.decode_settings(bytes(sample_eeprom))
        assert s.general.vox_switch is True

        setmod.apply_setting(sample_eeprom, "vox_switch", "off")
        s = setmod.decode_settings(bytes(sample_eeprom))
        assert s.general.vox_switch is False

    def test_logo_line1_str(self, sample_eeprom):
        setmod.apply_setting(sample_eeprom, "logo_line1", "HAM RADIO")
        s = setmod.decode_settings(bytes(sample_eeprom))
        assert s.logo.line1 == "HAM RADIO"

    def test_logo_line_truncated_to_display_length(self, sample_eeprom):
        # Logo lines have display_length=12 to match what the radio shows.
        setmod.apply_setting(sample_eeprom, "logo_line2", "X" * 30)
        s = setmod.decode_settings(bytes(sample_eeprom))
        assert len(s.logo.line2) <= 12
        assert s.logo.line2 == "X" * 12

    def test_logo_line_uses_chirp_layout(self, sample_eeprom):
        # Verify the EXACT byte layout matches CHIRP's logo_line write
        # logic: visible region 0x00-padded, then 0x00 + 0xFF*3.
        setmod.apply_setting(sample_eeprom, "logo_line1", "MONSTER")
        # logo_line1 is at 0xA0C8, 16 bytes wide.
        bytes_written = bytes(sample_eeprom[0xA0C8:0xA0C8 + 16])
        expected = b"MONSTER" + b"\x00" * 5 + b"\x00" + b"\xFF" * 3
        assert bytes_written == expected

    def test_enum_by_name(self, sample_eeprom):
        setmod.apply_setting(sample_eeprom, "tx_vfo", "B")
        s = setmod.decode_settings(bytes(sample_eeprom))
        assert s.general.tx_vfo == "B"

    def test_enum_case_insensitive(self, sample_eeprom):
        setmod.apply_setting(sample_eeprom, "tx_vfo", "a")
        s = setmod.decode_settings(bytes(sample_eeprom))
        assert s.general.tx_vfo == "A"

    def test_unknown_setting_raises(self, sample_eeprom):
        with pytest.raises(ValueError):
            setmod.apply_setting(sample_eeprom, "no_such_setting", "1")

    def test_bitfield_neighbour_preserved(self, sample_eeprom):
        # backlight_min and backlight_max share byte 0xA008. Setting one
        # must not corrupt the other.
        s_before = setmod.decode_settings(bytes(sample_eeprom))
        max_before = s_before.general.backlight_max

        setmod.apply_setting(sample_eeprom, "backlight_min", "7")

        s_after = setmod.decode_settings(bytes(sample_eeprom))
        assert s_after.general.backlight_min == 7
        assert s_after.general.backlight_max == max_before


class TestFmPresets:
    """The F4HWN registry has 48 FM presets at 0xA028 (u16 / MHz*100)."""

    def test_count(self):
        keys = [k for k in setmod.SETTINGS_REGISTRY if k.startswith("fm_preset_")]
        assert len(keys) == 48

    def test_addresses_contiguous(self):
        addrs = sorted(setmod.SETTINGS_REGISTRY[f"fm_preset_{i:02d}"].addr
                       for i in range(1, 49))
        assert addrs == [0xA028 + i * 2 for i in range(48)]

    def test_round_trip(self, sample_eeprom):
        setmod.apply_setting(sample_eeprom, "fm_preset_01", "100.5")
        assert setmod.read_setting(bytes(sample_eeprom), "fm_preset_01") \
            == "100.50 MHz"
        setmod.apply_setting(sample_eeprom, "fm_preset_01", "OFF")
        assert setmod.read_setting(bytes(sample_eeprom), "fm_preset_01") == ""

    def test_byte_format_is_freq_times_100(self, sample_eeprom):
        setmod.apply_setting(sample_eeprom, "fm_preset_05", "100.50")
        addr = setmod.SETTINGS_REGISTRY["fm_preset_05"].addr
        raw = int.from_bytes(sample_eeprom[addr:addr + 2], "little")
        assert raw == 10050

    def test_out_of_band_rejected(self, sample_eeprom):
        with pytest.raises(ValueError):
            setmod.apply_setting(sample_eeprom, "fm_preset_05", "200")
