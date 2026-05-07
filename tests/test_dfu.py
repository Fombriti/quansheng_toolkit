"""
Tests for kradio.dfu — the K5/K1 bootloader protocol.

These exercise framing + parsing in isolation (no radio). They lock in
byte compatibility with the published K5/K1 references protocol.js, which is the
authoritative reference we ported from. A regression here means the
real bootloader will reject our packets.
"""
from __future__ import annotations

import struct

import pytest

from quansheng_toolkit.kradio import dfu


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

def test_baudrate_is_38400():
    # K5/K6/K1/K5V3 bootloaders all use 38400 (different from EEPROM
    # session — that one varies between 38400 and 115200 by firmware).
    assert dfu.DFU_BAUDRATE == 38400


def test_xor_key_matches_protocol():
    # Same 16-byte XOR key as the EEPROM packet protocol.
    assert dfu.XOR_KEY == bytes([
        0x16, 0x6c, 0x14, 0xe6, 0x2e, 0x91, 0x0d, 0x40,
        0x21, 0x35, 0xd5, 0x40, 0x13, 0x03, 0xe9, 0x80,
    ])


def test_message_types_match_published_protocol():
    # Numbers from the published K1 flash reference.
    assert dfu.MSG_NOTIFY_DEV_INFO == 0x0518
    assert dfu.MSG_NOTIFY_BL_VER   == 0x0530
    assert dfu.MSG_PROG_FW         == 0x0519
    assert dfu.MSG_PROG_FW_RESP    == 0x051A


# ---------------------------------------------------------------------------
# Framing — build + parse round-trip
# ---------------------------------------------------------------------------

def test_build_packet_starts_with_head_and_ends_with_tail():
    pkt = dfu.build_packet(dfu.MSG_NOTIFY_BL_VER, b"7.00")
    assert pkt[:2] == dfu.HEAD
    assert pkt[-2:] == dfu.TAIL


def test_build_packet_layout_for_known_message():
    # NOTIFY_BL_VER with 4-byte version: inner = msgType(2) + dataLen(2) + data(4) = 8 bytes
    # Total wire: HEAD(2) + msgLen(2) + obf(8 + 2 CRC) + TAIL(2) = 16 bytes.
    pkt = dfu.build_packet(dfu.MSG_NOTIFY_BL_VER, b"7.00")
    assert len(pkt) == 2 + 2 + 8 + 2 + 2
    msg_len = struct.unpack("<H", pkt[2:4])[0]
    assert msg_len == 8


def test_build_packet_pads_odd_inner_length_to_even():
    # data_len = 5 → inner = 4 + 5 = 9, padded to 10.
    pkt = dfu.build_packet(0x0530, b"hello")
    msg_len = struct.unpack("<H", pkt[2:4])[0]
    assert msg_len == 10  # padded


def test_round_trip_parse_recovers_msgtype_and_data():
    pkt = dfu.build_packet(dfu.MSG_PROG_FW_RESP, b"\x05\x00\x00\x00")
    msg = dfu.parse_packet(pkt)
    assert msg.msg_type == dfu.MSG_PROG_FW_RESP
    assert msg.data == b"\x05\x00\x00\x00"


def test_round_trip_with_long_payload():
    payload = bytes(range(256))                # 256 bytes
    pkt = dfu.build_packet(dfu.MSG_PROG_FW, payload)
    msg = dfu.parse_packet(pkt)
    assert msg.msg_type == dfu.MSG_PROG_FW
    assert msg.data == payload


def test_parse_rejects_bad_head():
    pkt = dfu.build_packet(0x0518, b"\x00" * 32)
    bad = b"\x12\x34" + pkt[2:]
    with pytest.raises(dfu.DfuError, match="head"):
        dfu.parse_packet(bad)


def test_parse_rejects_bad_tail():
    pkt = dfu.build_packet(0x0518, b"\x00" * 32)
    bad = pkt[:-2] + b"\x99\x99"
    with pytest.raises(dfu.DfuError, match="tail"):
        dfu.parse_packet(bad)


def test_parse_skips_crc_validation_by_default():
    # The radio's bootloader broadcasts a 0xFFFF "skip CRC" sentinel
    # rather than a real CRC, so default parsing must not error on
    # mismatch. the upstream K5/K1 tooling does the same.
    pkt = bytearray(dfu.build_packet(0x0518, b"\x00" * 32))
    msg_len = 4 + 32                                 # msgType+dataLen+data
    pkt[4 + msg_len] ^= 0xFF                         # flip a CRC byte only
    msg = dfu.parse_packet(bytes(pkt))               # must not raise
    assert msg.msg_type == 0x0518
    assert len(msg.data) == 32


def test_parse_with_verify_crc_rejects_bad_crc():
    pkt = bytearray(dfu.build_packet(0x0518, b"\x00" * 32))
    msg_len = 4 + 32
    pkt[4 + msg_len] ^= 0xFF                         # flip a CRC byte
    with pytest.raises(dfu.DfuError, match="CRC"):
        dfu.parse_packet(bytes(pkt), verify_crc=True)


# ---------------------------------------------------------------------------
# Stream parser — multiple packets in a single buffer
# ---------------------------------------------------------------------------

def test_iter_packets_yields_two_packets_back_to_back():
    a = dfu.build_packet(dfu.MSG_NOTIFY_DEV_INFO, b"\x01" * 32)
    b = dfu.build_packet(dfu.MSG_NOTIFY_DEV_INFO, b"\x02" * 32)
    buf = bytearray(a + b)
    msgs = list(dfu.iter_packets(buf))
    assert len(msgs) == 2
    assert msgs[0].data == b"\x01" * 32
    assert msgs[1].data == b"\x02" * 32
    assert len(buf) == 0


def test_iter_packets_holds_back_partial_packet():
    a = dfu.build_packet(dfu.MSG_NOTIFY_DEV_INFO, b"\x01" * 32)
    b = dfu.build_packet(dfu.MSG_NOTIFY_DEV_INFO, b"\x02" * 32)
    buf = bytearray(a + b[:-3])               # last packet truncated
    msgs = list(dfu.iter_packets(buf))
    assert len(msgs) == 1
    # Buffer keeps the partial second packet for the next round.
    assert len(buf) == len(b) - 3


def test_iter_packets_skips_garbage_before_head():
    pkt = dfu.build_packet(dfu.MSG_NOTIFY_DEV_INFO, b"\xAA" * 32)
    buf = bytearray(b"\x00\xFF\x12\x99" + pkt)   # noise prefix
    msgs = list(dfu.iter_packets(buf))
    assert len(msgs) == 1
    assert msgs[0].data == b"\xAA" * 32


# ---------------------------------------------------------------------------
# Bootloader → model resolution
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ver, expected", [
    ("5.00.01", "UV-K5 V2"),
    ("2.00.06", "UV-K5 V1"),
    # 7.00.07 ships on BOTH UV-K5 V3 and UV-K1(8) v3 Mini Kong (same
    # MCU + bootloader binary), so identify_model returns a combined
    # label rather than misclassifying a Mini Kong as a V3.
    ("7.00.07", "UV-K5 V3 / UV-K1(8) v3"),
    ("7.02.02", "UV-K1"),
    ("7.03.01", "UV-K1"),
    ("99.99.99", "unknown"),
    ("",        "unknown"),
])
def test_identify_model(ver, expected):
    assert dfu.identify_model(ver) == expected


def test_blocked_for_k1_family_set():
    # These two bootloaders MUST refuse K1/K5V3 firmware.
    assert "5.00.01" in dfu.BLOCKED_FOR_K1_FAMILY
    assert "2.00.06" in dfu.BLOCKED_FOR_K1_FAMILY


# ---------------------------------------------------------------------------
# Version string decode
# ---------------------------------------------------------------------------

def test_decode_bl_version_strips_trailing_nulls():
    raw = b"7.00.07\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    assert dfu._decode_bl_version(raw) == "7.00.07"


def test_decode_bl_version_no_nulls():
    raw = b"1234567890123456"  # exactly 16, no nulls
    assert dfu._decode_bl_version(raw) == "1234567890123456"


# ---------------------------------------------------------------------------
# Phase 3 — anti-brick flash safety gate
# ---------------------------------------------------------------------------

def test_target_constants_match_allowlist_keys():
    assert dfu.FLASH_TARGET_K5_K6 == "k5_k6"
    assert dfu.FLASH_TARGET_K5_V3 == "k5_v3"
    assert dfu.FLASH_TARGET_K1    == "k1"
    assert set(dfu.ALLOWED_BOOTLOADERS_BY_TARGET) == {
        "k5_k6", "k5_v3", "k1",
    }


def test_target_for_bootloader_recognises_known_versions():
    assert dfu.target_for_bootloader("5.00.01") == "k5_k6"
    assert dfu.target_for_bootloader("2.00.06") == "k5_k6"
    assert dfu.target_for_bootloader("7.00.07") == "k5_v3"
    assert dfu.target_for_bootloader("7.03.01") == "k1"
    assert dfu.target_for_bootloader("99.99.99") is None


# ---- Safe combinations: each bootloader matched to its own target ----

@pytest.mark.parametrize("target, bl", [
    ("k5_k6", "5.00.01"),
    ("k5_k6", "2.00.06"),
    ("k5_v3", "7.00.07"),
    ("k1",    "7.02.02"),
    ("k1",    "7.03.01"),
    ("k1",    "7.03.02"),
    ("k1",    "7.03.03"),
])
def test_assert_safe_passes_for_matching_bootloader(target, bl):
    # Should not raise.
    dfu.assert_safe_to_flash(bl, target)


# ---- The brick-cases: each is explicitly listed in the upstream docs ----

@pytest.mark.parametrize("target, bl, reason_substring", [
    # The two bootloaders the upstream README explicitly blocks on the
    # K1 flash page — flashing K1 firmware onto these bricks the radio.
    ("k1",    "5.00.01", "BRICK"),    # UV-K5 V2 receiving K1 firmware
    ("k1",    "2.00.06", "BRICK"),    # UV-K5 V1 receiving K1 firmware
    # Other obvious mismatches:
    ("k5_v3", "5.00.01", "BRICK"),
    ("k5_v3", "7.03.01", "BRICK"),
    ("k5_k6", "7.00.07", "BRICK"),
    ("k5_k6", "7.03.01", "BRICK"),
])
def test_assert_safe_blocks_known_brick_combinations(target, bl, reason_substring):
    with pytest.raises(dfu.DfuError, match=reason_substring):
        dfu.assert_safe_to_flash(bl, target)


def test_assert_safe_refuses_unknown_bootloader():
    with pytest.raises(dfu.DfuError, match="unknown bootloader"):
        dfu.assert_safe_to_flash("99.99.99", "k1")


def test_assert_safe_refuses_empty_bootloader():
    with pytest.raises(dfu.DfuError, match="no bootloader version"):
        dfu.assert_safe_to_flash("", "k1")


def test_assert_safe_rejects_unknown_target_name():
    # A typo or missing constant must NOT silently allow the flash.
    with pytest.raises(dfu.DfuError, match="unknown firmware_target"):
        dfu.assert_safe_to_flash("7.03.01", "k1stock")  # not a target name


def test_allowlists_have_no_overlap():
    """Each bootloader version must belong to exactly one target — otherwise
    the gate becomes ambiguous and the brick-protection guarantees weaken."""
    seen = {}
    for target, allowed in dfu.ALLOWED_BOOTLOADERS_BY_TARGET.items():
        for bl in allowed:
            assert bl not in seen, (
                f"bootloader {bl} appears in both {seen[bl]} and {target}"
            )
            seen[bl] = target


# ---------------------------------------------------------------------------
# Phase 4 — flash write payload builder
# ---------------------------------------------------------------------------

def test_flash_page_size_is_256():
    assert dfu.FLASH_PAGE_SIZE == 256


def test_build_prog_fw_message_layout():
    page = bytes(range(256))
    inner = dfu._build_prog_fw_message(
        timestamp=0xDEADBEEF, page_index=7, page_count=200, page_data=page,
    )
    # Header: 4 bytes timestamp + 2 bytes page_idx + 2 bytes page_count
    #         + 4 bytes padding/reserved
    assert struct.unpack_from("<I", inner, 0)[0] == 0xDEADBEEF
    assert struct.unpack_from("<H", inner, 4)[0] == 7
    assert struct.unpack_from("<H", inner, 6)[0] == 200
    assert inner[8:12] == b"\x00\x00\x00\x00"
    # Page data starts at offset 12 inside the inner.
    assert inner[12:12 + 256] == page
    assert len(inner) == 12 + 256


def test_build_prog_fw_message_pads_short_chunk():
    # Last page of a non-aligned firmware: data shorter than 256 bytes.
    short = b"AB"
    inner = dfu._build_prog_fw_message(0, 0, 1, short)
    assert inner[12:14] == b"AB"
    assert inner[14:12 + 256] == b"\x00" * (256 - 2)


def test_build_prog_fw_message_rejects_oversize_chunk():
    with pytest.raises(ValueError):
        dfu._build_prog_fw_message(0, 0, 1, b"\x00" * 300)


# Live-protocol smoke tests for flash_firmware would need a fake serial
# port + a state machine that responds with PROG_FW_RESP — out of scope
# for unit tests. The page-builder coverage above + the bench validation
# on real hardware (see Phase 4 walkthrough in the README) are how we
# trust the loop.
