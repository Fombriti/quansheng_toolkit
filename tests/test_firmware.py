"""
Tests for kradio.firmware — pure offline parsing of Quansheng .bin
firmware files. Locks in byte-for-byte compatibility with the protocol
documented in the published K5 protocol reference (the source we ported
from), so a careless edit of the XOR key can't silently break flashes.
"""
from __future__ import annotations

import struct

import pytest

from quansheng_toolkit.kradio import firmware as fw


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

def test_xor_key_size_and_known_bytes():
    # the upstream protocol.js defines this 128-byte array verbatim.
    assert len(fw.FIRMWARE_XOR_KEY) == 128
    # First and last bytes from the JS source.
    assert fw.FIRMWARE_XOR_KEY[0] == 0x47
    assert fw.FIRMWARE_XOR_KEY[-1] == 0x8B


def test_version_block_offsets_match_protocol_js():
    assert fw.VERSION_INFO_OFFSET == 0x2000
    assert fw.VERSION_INFO_LENGTH == 16


def test_max_firmware_size_matches_bootloader_cap():
    assert fw.MAX_FIRMWARE_SIZE == 0xF000


# ---------------------------------------------------------------------------
# CRC and XOR
# ---------------------------------------------------------------------------

def test_crc_known_vector_zero_input():
    # CRC-CCITT(XMODEM) of empty input is 0.
    assert fw.crc16_ccitt(b"") == 0


def test_crc_known_vector_123456789():
    # Standard XMODEM check string; CRC = 0x31C3.
    assert fw.crc16_ccitt(b"123456789") == 0x31C3


def test_crc_le_packs_low_byte_first():
    crc = fw.crc16_ccitt(b"hi")
    le = fw.crc16_ccitt_le(b"hi")
    assert le == bytes([crc & 0xFF, (crc >> 8) & 0xFF])


def test_xor_is_self_inverse():
    payload = bytes(range(200)) * 3            # 600 bytes
    once = fw.firmware_xor(payload)
    twice = fw.firmware_xor(once)
    assert twice == payload                    # XOR(XOR(x)) == x


def test_xor_first_byte_uses_key_first_byte():
    out = fw.firmware_xor(b"\x00")
    assert out == bytes([fw.FIRMWARE_XOR_KEY[0]])


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def test_raw_firmware_detected_when_stack_pointer_in_ram():
    # Reset vector with SP = 0x20003FF0 → falls inside the RAM range.
    raw = bytearray(0x4000)
    struct.pack_into("<I", raw, 0, 0x20003FF0)
    assert fw.is_raw_firmware(bytes(raw)) is True


def test_raw_firmware_rejected_when_first_4_bytes_random():
    raw = bytes([0x47, 0x22, 0xC0, 0x52]) + bytes(0x1FF0)  # XOR key prefix
    assert fw.is_raw_firmware(raw) is False


def test_raw_firmware_rejected_for_too_short_input():
    assert fw.is_raw_firmware(b"\x00\x00\x00\x20") is False


def test_has_crc_validation_true_when_appended():
    body = b"hello world"
    data = body + fw.crc16_ccitt_le(body)
    assert fw.has_crc_validation(data) is True


def test_has_crc_validation_false_on_random_tail():
    body = b"hello world"
    data = body + b"\x00\x00"
    assert fw.has_crc_validation(data) is False


def test_has_crc_validation_false_on_too_short_input():
    assert fw.has_crc_validation(b"x") is False


# ---------------------------------------------------------------------------
# unpack_firmware — the round-trip
# ---------------------------------------------------------------------------

def _build_packed_firmware_with_version(version_string: str = "TEST-FW v1.0",
                                         body_size: int = 0x4000,
                                         with_crc: bool = True) -> bytes:
    """
    Construct a synthetic 'packed' firmware file:
      * `body_size` bytes of payload (deterministic pattern)
      * a 16-byte version block embedded at offset 0x2000
      * XOR-encoded with the 128-byte key
      * CRC-CCITT-LE appended (when `with_crc=True`)
    Mirrors what Quansheng's official packing pipeline produces.
    """
    payload = bytearray(b"X" * body_size)
    version_bytes = version_string.encode("ascii").ljust(16, b"\x00")
    payload[0x2000:0x2010] = version_bytes
    encoded = fw.firmware_xor(bytes(payload))
    if with_crc:
        encoded += fw.crc16_ccitt_le(encoded)
    return encoded


def test_packed_with_crc_round_trip():
    encoded = _build_packed_firmware_with_version("TEST-FW v1.0", body_size=0x4000)
    decoded = fw.unpack_firmware(encoded)
    # Decoded length = body_size minus the 16-byte version block.
    assert len(decoded) == 0x4000 - 16
    # Body bytes (everything except the version block) are still the
    # deterministic 'X' pattern we put in.
    assert decoded[:0x2000] == b"X" * 0x2000
    assert decoded[0x2000:] == b"X" * (0x4000 - 0x2000 - 16)


def test_packed_without_crc_round_trip():
    encoded = _build_packed_firmware_with_version("CUSTOM v0.99",
                                                  body_size=0x4000,
                                                  with_crc=False)
    decoded = fw.unpack_firmware(encoded)
    assert len(decoded) == 0x4000 - 16


def test_unpack_firmware_version_returns_16_bytes():
    encoded = _build_packed_firmware_with_version("v1.2.3", body_size=0x4000)
    raw = fw.unpack_firmware_version(encoded)
    assert len(raw) == 16
    assert raw.startswith(b"v1.2.3")


def test_unpack_corrupted_tail_falls_back_to_no_crc_branch():
    # has_crc_validation only returns True when the trailing 2 bytes
    # form a valid CRC over the rest. A tampered tail simply causes
    # the file to be treated as "packed, no CRC" — same behaviour as
    # the JS reference. We assert no exception is raised; the result
    # might be garbage but the API doesn't lie.
    encoded = _build_packed_firmware_with_version("X", body_size=0x4000)
    tampered = encoded[:-2] + b"\x00\x00"
    assert fw.has_crc_validation(tampered) is False
    fw.unpack_firmware(tampered)  # must not raise


def test_unpack_raw_firmware_passthrough():
    raw = bytearray(0x4000)
    struct.pack_into("<I", raw, 0, 0x20003FF0)    # valid stack pointer
    out = fw.unpack_firmware(bytes(raw))
    assert out == bytes(raw)


# ---------------------------------------------------------------------------
# parse_firmware_file — the public summary
# ---------------------------------------------------------------------------

def test_parse_packed_with_crc(tmp_path):
    p = tmp_path / "fw.bin"
    p.write_bytes(_build_packed_firmware_with_version("TESTv1", 0x4000))
    info = fw.parse_firmware_file(p)
    assert info.size_bytes == 0x4000 + 2
    assert info.is_raw is False
    assert info.has_crc is True
    assert info.crc_valid is True
    assert info.version_string.startswith("TESTv1")
    assert info.decoded_size == 0x4000 - 16
    assert info.fits_bootloader is True


def test_parse_raw_firmware(tmp_path):
    raw = bytearray(0x4000)
    struct.pack_into("<I", raw, 0, 0x20003FF0)
    raw[0x2000:0x2010] = b"RAW v0.0\x00\x00\x00\x00\x00\x00\x00\x00"
    p = tmp_path / "raw.bin"
    p.write_bytes(bytes(raw))
    info = fw.parse_firmware_file(p)
    assert info.is_raw is True
    assert info.has_crc is False
    assert info.version_string.startswith("RAW v0.0")


def test_parse_too_large_for_bootloader(tmp_path):
    # 75 KB packed file — decoded > 0xF000 (60 KB) so doesn't fit K5/K6,
    # but still ≤ 0x18000 (96 KB) so OK for K1/K5 V3.
    body_size = 0x12000
    p = tmp_path / "huge.bin"
    p.write_bytes(_build_packed_firmware_with_version("BIG", body_size))
    info = fw.parse_firmware_file(p)
    assert info.fits_k5_k6 is False
    assert info.fits_k1_k5v3 is True


def test_parse_too_large_for_any_bootloader(tmp_path):
    body_size = 0x20000  # 128 KB — way beyond every cap
    p = tmp_path / "monster.bin"
    p.write_bytes(_build_packed_firmware_with_version("HUGE", body_size))
    info = fw.parse_firmware_file(p)
    assert info.fits_k5_k6 is False
    assert info.fits_k1_k5v3 is False
