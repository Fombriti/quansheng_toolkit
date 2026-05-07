"""
Memory-layout tests for the K5 V1 / K1 stock module.

Locks in the byte offsets and bit packing observed on a real UV-K1
running firmware 7.03.01. Anything that changes here without an
explicit hardware-validation note is almost certainly a bug.
"""
from __future__ import annotations

import pytest

from quansheng_toolkit.kradio import memory_uvk5_v1 as mm


# ---------------------------------------------------------------------------
# Address arithmetic
# ---------------------------------------------------------------------------

def test_memory_constants():
    assert mm.MEM_SIZE == 0x2000
    assert mm.PROG_SIZE == 0x1D00
    assert mm.CAL_START == 0x1D00
    assert mm.NUM_CHANNELS == 200
    assert mm.CHANNELS_BASE == 0x0000
    assert mm.CHANNEL_SIZE == 0x10
    assert mm.CHANNEL_NAMES_BASE == 0x0F50
    assert mm.CH_ATTR_BASE == 0x0D60


def test_addr_helpers():
    assert mm.addr_channel(0) == 0x0000
    assert mm.addr_channel(199) == 0x0000 + 199 * 0x10
    assert mm.addr_channel_name(0) == 0x0F50
    assert mm.addr_channel_name(1) == 0x0F60
    assert mm.addr_ch_attr(0) == 0x0D60
    assert mm.addr_ch_attr(199) == 0x0D60 + 199
    # scanlist byte shares the same byte as compander+band on K5 V1
    assert mm.addr_scanlist_byte(5) == mm.addr_ch_attr(5)


# ---------------------------------------------------------------------------
# Scan-list bit packing — the bug we just fixed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sl_value, expected_high_bits", [
    (mm.SCAN_OFF,  0b00),  # both flags clear
    (mm.SCAN_SL1,  0b10),  # bit 7 (is_scanlist1) set
    (mm.SCAN_SL2,  0b01),  # bit 6 (is_scanlist2) set
    (mm.SCAN_BOTH, 0b11),
])
def test_patch_scanlist_only_touches_top_two_bits(sl_value, expected_high_bits):
    # Start with a byte that has compander=01, is_free=1, band=010 — all the
    # bits that are NOT the scanlist flags. Patching the scanlist must not
    # touch any of them.
    other_bits = 0b00_01_1_010
    new = mm.patch_scanlist(other_bits, sl_value)
    assert (new & 0x3F) == other_bits, "non-scanlist bits were modified"
    assert (new >> 6) & 0b11 == expected_high_bits


def test_patch_scanlist_rejects_out_of_range():
    with pytest.raises(ValueError):
        mm.patch_scanlist(0x00, 4)
    with pytest.raises(ValueError):
        mm.patch_scanlist(0x00, -1)


# ---------------------------------------------------------------------------
# parse_scanlist_spec — CSV "LISTA N" → 0..3 collapse
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("spec,expected", [
    (None,         mm.SCAN_OFF),
    ("",           mm.SCAN_OFF),
    ("OFF",        mm.SCAN_OFF),
    ("none",       mm.SCAN_OFF),
    ("-",          mm.SCAN_OFF),
    ("0",          mm.SCAN_OFF),
    ("1",          mm.SCAN_SL1),
    ("L1",         mm.SCAN_SL1),
    ("SL1",        mm.SCAN_SL1),
    ("2",          mm.SCAN_SL2),
    ("L2",         mm.SCAN_SL2),
    ("SL2",        mm.SCAN_SL2),
    ("3",          mm.SCAN_BOTH),
    ("ALL",        mm.SCAN_BOTH),
    ("BOTH",       mm.SCAN_BOTH),
    ("SL1+SL2",    mm.SCAN_BOTH),
    ("L1+L2",      mm.SCAN_BOTH),
    ("1+2",        mm.SCAN_BOTH),
    # Multi-list CSVs (LISTA 5 etc.) collapse: anything > 1 → SL2
    ("5",          mm.SCAN_SL2),
    ("L24",        mm.SCAN_SL2),
])
def test_parse_scanlist_spec(spec, expected):
    assert mm.parse_scanlist_spec(spec) == expected


def test_parse_scanlist_spec_rejects_garbage():
    with pytest.raises(ValueError):
        mm.parse_scanlist_spec("not-a-list")


# ---------------------------------------------------------------------------
# Channel encode / decode round-trip
# ---------------------------------------------------------------------------

def test_decode_channel_returns_empty_for_uninitialised_slot():
    blank = b"\xff" * mm.MEM_SIZE
    chs = mm.decode_all_channels(blank)
    assert len(chs) == mm.NUM_CHANNELS
    assert all(c.is_empty for c in chs)


def test_patch_then_decode_roundtrip():
    img = bytearray(b"\xff" * mm.MEM_SIZE)
    mm.patch_channel_in_image(
        img, idx=7, name="HAM-V",
        freq_hz=145_500_000, mode="FM", scanlist=mm.SCAN_BOTH,
    )
    chs = mm.decode_all_channels(bytes(img))
    ch = chs[7]
    assert not ch.is_empty
    assert ch.name == "HAM-V"
    assert ch.freq_hz == 145_500_000
    assert ch.mode == "FM"
    assert ch.scanlist == mm.SCAN_BOTH
    assert ch.scanlist_label == "SL1+SL2"


def test_patch_preserves_other_channels():
    img = bytearray(b"\xff" * mm.MEM_SIZE)
    mm.patch_channel_in_image(
        img, idx=0, name="HAM-V", freq_hz=145_500_000,
        mode="FM", scanlist=1
    )
    mm.patch_channel_in_image(
        img, idx=1, name="HAM-U", freq_hz=433_500_000,
        mode="FM", scanlist=2
    )
    chs = mm.decode_all_channels(bytes(img))
    assert chs[0].name == "HAM-V" and chs[0].scanlist == 1
    assert chs[1].name == "HAM-U" and chs[1].scanlist == 2
    assert all(c.is_empty for c in chs[2:])


def test_clear_channel_in_image_makes_slot_empty():
    img = bytearray(b"\xff" * mm.MEM_SIZE)
    mm.patch_channel_in_image(
        img, idx=3, name="X", freq_hz=145_000_000, mode="FM", scanlist=1
    )
    assert not mm.decode_all_channels(bytes(img))[3].is_empty
    mm.clear_channel_in_image(img, 3)
    assert mm.decode_all_channels(bytes(img))[3].is_empty
    # Surrounding slots stay empty.
    assert mm.decode_all_channels(bytes(img))[2].is_empty
    assert mm.decode_all_channels(bytes(img))[4].is_empty


def test_band_byte_is_set_from_freq():
    img = bytearray(b"\xff" * mm.MEM_SIZE)
    # Record was 0xFF (= is_free=1, band=0b111). Patching with a 2m freq
    # must clear is_free and set the band code matching that frequency.
    mm.patch_channel_in_image(
        img, idx=0, name="VHF", freq_hz=145_000_000, mode="FM", scanlist=0
    )
    attr = img[mm.addr_ch_attr(0)]
    # is_free bit (3) must be cleared on a populated slot
    assert attr & 0x08 == 0, "is_free should be 0 on a populated channel"
    # 2m frequencies → band 2 in the K5 V1 BANDS_MHZ table
    assert attr & 0x07 == 2


# ---------------------------------------------------------------------------
# Attribute-byte cleanliness regression (mirror of the F4HWN K1 fix)
# ---------------------------------------------------------------------------

def test_compander_bits_cleared_when_writing_full_channel_from_FF():
    """A full channel write on an uninitialised slot (0xFF) must produce
    clean attribute bits — not inherit `compander=11` from the prior byte.

    The K5 V1 / K1 stock attribute byte packs:
      bit 7   : scanlist1
      bit 6   : scanlist2
      bits 5-4: compander (00=OFF, 01=TX, 10=RX, 11=RX+TX)
      bit 3   : is_free
      bits 2-0: band

    With the previous OR-onto-existing implementation, a fresh slot
    (0xFF) ended up with compander=11 (RX+TX compression on every newly
    written channel) — same code shape that broke V/M on the K1 F4HWN
    radio. This regression locks in the clean encoding.
    """
    img = bytearray(b"\xff" * mm.MEM_SIZE)
    mm.patch_channel_in_image(
        img, idx=10, name="TST", freq_hz=145_500_000, mode="FM",
        scanlist=mm.SCAN_SL1,
    )
    attr = img[mm.addr_ch_attr(10)]
    # Compander bits (4-5) must be 0 on a fresh write.
    assert (attr >> 4) & 0x03 == 0, (
        f"compander dirty: attr=0x{attr:02x} (bits 4-5 = "
        f"{(attr >> 4) & 0x03})"
    )
    # Sanity: is_free must be 0, band correct, scanlist applied.
    assert attr & 0x08 == 0, "is_free not cleared"
    assert attr & 0x07 == 2, "band code wrong for 145.5 MHz"
    assert (attr >> 7) & 0x01 == 1, "scanlist1 not set for SCAN_SL1"


def test_compander_bits_cleared_when_writing_over_stale_byte():
    """Same regression with a non-0xFF prior byte: even when the slot
    was previously populated with compander=11, a fresh full write
    must produce a clean attribute, not OR onto the stale bits.
    """
    img = bytearray(b"\xff" * mm.MEM_SIZE)
    img[mm.addr_ch_attr(15)] = 0b00110000  # compander=11, everything else 0
    mm.patch_channel_in_image(
        img, idx=15, name="TST2", freq_hz=433_500_000, mode="FM",
        scanlist=mm.SCAN_OFF,
    )
    attr = img[mm.addr_ch_attr(15)]
    assert (attr >> 4) & 0x03 == 0, (
        f"compander still dirty after full write: 0x{attr:02x}"
    )
    # 433.5 MHz → BAND6 (band code 5) per K5 V1 freq_to_band.
    assert attr & 0x07 == 5
    assert attr & 0x08 == 0


def test_scanlist_only_patch_still_preserves_other_bits():
    """Counter-test: `patch_scanlist` (used when only the scanlist
    changes — see workflow.patch_scanlist_byte and
    test_k5v1_preserves_other_bits) intentionally preserves the existing
    band / compander / is_free bits. Make sure the new clean-encoding
    behaviour above didn't accidentally break that path.
    """
    img = bytearray(b"\xff" * mm.MEM_SIZE)
    # Stage a fully-configured byte: band=2, is_free=0, compander=11,
    # scanlist1=1.
    img[mm.addr_ch_attr(20)] = 0b10110010
    before = img[mm.addr_ch_attr(20)]

    new_attr = mm.patch_scanlist(before, mm.SCAN_SL2)
    img[mm.addr_ch_attr(20)] = new_attr

    after = img[mm.addr_ch_attr(20)]
    # Low 6 bits unchanged (band / is_free / compander preserved).
    assert (after & 0x3F) == (before & 0x3F)
    # Bit 7 = scanlist1 (cleared), bit 6 = scanlist2 (set).
    assert (after >> 7) & 0x01 == 0, "scanlist1 should be cleared for SCAN_SL2"
    assert (after >> 6) & 0x01 == 1, "scanlist2 should be set for SCAN_SL2"


# ---------------------------------------------------------------------------
# Synthetic EEPROM round-trip via fixture (also exercises decode_all_channels)
# ---------------------------------------------------------------------------

def test_fixture_decodes_expected_channels(k1_stock_eeprom_bytes):
    chs = mm.decode_all_channels(k1_stock_eeprom_bytes)
    by_name = {c.name: c for c in chs if not c.is_empty}
    assert {"HAM-V", "HAM-U", "MAR-16", "PMR-1"} <= set(by_name)
    assert by_name["HAM-V"].freq_hz == 145_500_000
    assert by_name["HAM-V"].scanlist == mm.SCAN_BOTH
    assert by_name["HAM-U"].scanlist == mm.SCAN_SL1
    assert by_name["MAR-16"].scanlist == mm.SCAN_SL2
    assert by_name["PMR-1"].scanlist == mm.SCAN_OFF
