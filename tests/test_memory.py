"""Unit tests for the EEPROM memory map and channel decoder/encoder."""
from __future__ import annotations

import pytest

from quansheng_toolkit.kradio import memory as mem


class TestScanlistEncoding:
    @pytest.mark.parametrize("text,expected", [
        ("OFF", 0),
        ("off", 0),
        ("ALL", 25),
        ("all", 25),
        ("1", 1),
        ("24", 24),
        ("L1", 1),
        ("L24", 24),
        ("list 5", 5),
        ("LIST 12", 12),
        ("  3  ", 3),
        ("-", 0),
        ("", 0),
    ])
    def test_parse_valid(self, text, expected):
        assert mem.parse_scanlist_spec(text) == expected

    @pytest.mark.parametrize("bad", ["25", "26", "0", "999", "L0", "L25", "abc"])
    def test_parse_invalid(self, bad):
        with pytest.raises(ValueError):
            mem.parse_scanlist_spec(bad)

    def test_label_round_trip(self):
        for v in range(0, 26):
            label = mem.scanlist_label(v)
            # 'OFF', 'L1'..'L24', 'ALL' must all parse back to v.
            assert mem.parse_scanlist_spec(label) == v


class TestNameCoding:
    def test_encode_pads_to_16(self):
        b = mem.encode_name("FI-TWR")
        assert len(b) == 16
        assert b == b"FI-TWR" + b"\xFF" * 10

    def test_encode_truncates(self):
        b = mem.encode_name("THIS_IS_A_VERY_LONG_NAME")
        assert len(b) == 16
        # Only the first 10 chars are kept (CHANNEL_NAME_MAX)
        assert b.startswith(b"THIS_IS_A_")

    def test_decode_stops_at_ff(self):
        raw = b"NAME\xFF" + b"\x00" * 11
        assert mem._decode_name(raw) == "NAME"

    def test_decode_stops_at_null(self):
        raw = b"NAME\x00" + b"\xFF" * 11
        assert mem._decode_name(raw) == "NAME"


class TestAddressHelpers:
    def test_addr_channel(self):
        assert mem.addr_channel(0) == 0x0000
        assert mem.addr_channel(1) == 0x0010
        assert mem.addr_channel(255) == 0x0FF0

    def test_addr_channel_name(self):
        assert mem.addr_channel_name(0) == 0x4000
        assert mem.addr_channel_name(1) == 0x4010

    def test_addr_ch_attr(self):
        assert mem.addr_ch_attr(0) == 0x8000
        assert mem.addr_ch_attr(16) == 0x8020

    def test_addr_scanlist_byte(self):
        # The scanlist byte is the second byte of each ch_attr entry.
        assert mem.addr_scanlist_byte(0) == 0x8001
        assert mem.addr_scanlist_byte(16) == 0x8021


class TestFreqToBand:
    @pytest.mark.parametrize("mhz,expected_band", [
        (50.0, 0),       # low band
        (109.0, 1),      # interior of band 1
        (118.3, 1),      # aviation low
        (137.0, 2),
        (145.5, 2),      # 2m amateur
        (180.0, 3),
        (350.0, 4),
        (433.5, 5),      # 70cm amateur
        (470.0, 6),
        (1200.0, 6),
    ])
    def test_band_lookup(self, mhz, expected_band):
        assert mem.freq_to_band(int(mhz * 1_000_000)) == expected_band


class TestChannelDecode:
    def test_eeprom_size(self, sample_eeprom_bytes):
        # The fixture is a real radio dump; its size must match MEM_SIZE.
        assert len(sample_eeprom_bytes) == 0xB190

    def test_decode_all_channels_count(self, sample_eeprom_bytes):
        channels = mem.decode_all_channels(sample_eeprom_bytes)
        assert len(channels) == mem.NUM_CHANNELS

    def test_some_channels_are_configured(self, sample_eeprom_bytes):
        channels = mem.decode_all_channels(sample_eeprom_bytes)
        configured = [c for c in channels if not c.is_empty]
        # The fixture is from a populated radio.
        assert len(configured) > 0

    def test_known_channel_synthetic(self, sample_eeprom_bytes):
        # The synthetic fixture seeds slot 0 with HAM-V at 145.500 MHz FM.
        ch = mem.decode_all_channels(sample_eeprom_bytes)[0]
        assert ch.name == "HAM-V"
        assert ch.freq_mhz == 145.5
        assert ch.mode == "FM"


class TestChannelEncoderRoundTrip:
    def test_fm_channel(self, sample_eeprom):
        mem.patch_channel_in_image(
            sample_eeprom, idx=500, name="TEST", freq_hz=145_500_000,
            mode="FM", scanlist=3,
        )
        decoded = mem.decode_all_channels(bytes(sample_eeprom))[500]
        assert decoded.name == "TEST"
        assert decoded.freq_mhz == 145.5
        assert decoded.mode == "FM"
        assert decoded.scanlist == 3
        assert decoded.band == 2

    def test_am_aviation_channel(self, sample_eeprom):
        mem.patch_channel_in_image(
            sample_eeprom, idx=501, name="AVI", freq_hz=120_500_000,
            mode="AM", scanlist=1,
        )
        decoded = mem.decode_all_channels(bytes(sample_eeprom))[501]
        assert decoded.mode == "AM"
        assert decoded.band == 1

    def test_clear_channel(self, sample_eeprom):
        mem.clear_channel_in_image(sample_eeprom, 502)
        decoded = mem.decode_all_channels(bytes(sample_eeprom))[502]
        assert decoded.is_empty

    def test_patch_only_scanlist_preserves_record(self, sample_eeprom):
        before = mem.decode_all_channels(bytes(sample_eeprom))[0]
        mem.patch_channel_in_image(sample_eeprom, idx=0, scanlist=5)
        after = mem.decode_all_channels(bytes(sample_eeprom))[0]
        # Frequency, name and mode are preserved when only scanlist changes.
        assert after.freq_hz == before.freq_hz
        assert after.name == before.name
        assert after.mode == before.mode
        assert after.scanlist == 5


# ---------------------------------------------------------------------------
# Attribute-encoding regression for UV-K1(8) v3 / F4HWN
# ---------------------------------------------------------------------------

class TestChannelAttributeBitClean:
    """Regression for the K1(8) v3 'V/M does nothing' bug.

    F4HWN K1's `ChannelAttributes_t` packs band+compander+exclude in
    byte 0 and a scanlist bitmask in byte 1. Bit 7 of byte 0 is the
    `exclude` flag — when set, the firmware hides the slot from MR
    mode (V/M won't switch into channels, ChName/ChDel return NULL).

    The previous implementation OR'd the new band onto whatever bits
    were already in the EEPROM byte. When the prior byte was 0xFF
    (uninitialised flash) or 0x07 (the firmware's empty-slot marker)
    the exclude bit stayed set, so writing a channel produced a
    "ghost" slot the radio refused to surface.

    Now `patch_channel_in_image` always overwrites both attribute
    bytes from scratch.
    """

    def test_band_overwrites_exclude_bit_when_prior_byte_is_FF(self, sample_eeprom):
        # Simulate uninitialised flash: pre-set the attr bytes to 0xFF 0xFF.
        addr = mem.addr_ch_attr(600)
        sample_eeprom[addr]     = 0xFF
        sample_eeprom[addr + 1] = 0xFF

        mem.patch_channel_in_image(
            sample_eeprom, idx=600, name="TEST", freq_hz=146_500_000,
            mode="FM", scanlist=1,
        )

        attr0 = sample_eeprom[addr]
        attr1 = sample_eeprom[addr + 1]

        # Band must be 2 (BAND3 137-174 MHz), and exclude (bit 7),
        # compander (bits 3-4) and unused (bits 5-6) must all be 0.
        assert attr0 & 0x07 == 2,           f"band wrong: 0x{attr0:02x}"
        assert attr0 & 0x80 == 0,           f"exclude bit still set: 0x{attr0:02x}"
        assert attr0 & 0x18 == 0,           f"compander bits dirty: 0x{attr0:02x}"
        assert attr0 & 0x60 == 0,           f"unused bits dirty: 0x{attr0:02x}"
        assert attr1 == 0x01,               f"scanlist wrong: 0x{attr1:02x}"

    def test_band_overwrites_when_prior_byte_is_legacy_07(self, sample_eeprom):
        # Some other tools' default channel profile writes 0x07 0x00
        # as the empty-slot marker for every untouched slot.
        addr = mem.addr_ch_attr(700)
        sample_eeprom[addr]     = 0x07
        sample_eeprom[addr + 1] = 0x00

        mem.patch_channel_in_image(
            sample_eeprom, idx=700, name="UHF", freq_hz=433_500_000,
            mode="FM", scanlist=2,
        )

        attr0 = sample_eeprom[addr]
        # 433.5 MHz → BAND6 (band code 5)
        assert attr0 & 0x07 == 5
        assert attr0 & 0x80 == 0  # exclude must be cleared
        assert sample_eeprom[addr + 1] == 0x02

    def test_clear_channel_writes_f4hwn_empty_marker(self, sample_eeprom):
        mem.clear_channel_in_image(sample_eeprom, 800)
        addr = mem.addr_ch_attr(800)
        # F4HWN's "empty slot" marker is band=7 + scanlist=0,
        # i.e. byte 0 = 0x07, byte 1 = 0x00.
        assert sample_eeprom[addr]     == 0x07
        assert sample_eeprom[addr + 1] == 0x00
