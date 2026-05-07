"""Tests for kradio.display_mirror — protocol parser + framebuffer logic.

The hardware-side keepalive thread isn't exercised here (no radio in
CI). We pin down the wire format and the bit-packing so a bad refactor
can't silently corrupt the live mirror.
"""
from __future__ import annotations

import pytest

from quansheng_toolkit.kradio import display_mirror as dm


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

def test_display_dimensions():
    assert dm.DISPLAY_WIDTH == 128
    assert dm.DISPLAY_HEIGHT == 64
    assert dm.FRAMEBUFFER_SIZE == 1024
    assert dm.KEEPALIVE_PACKET == bytes([0x55, 0xAA, 0x00, 0x00])
    assert dm.FRAME_MAGIC == bytes([0xAA, 0x55])
    assert dm.FRAME_TYPE_FULL == 0x01
    assert dm.FRAME_TYPE_DIFF == 0x02


# ---------------------------------------------------------------------------
# parse_frames — wire format
# ---------------------------------------------------------------------------

def _build_full_frame(payload: bytes, *, leading_ff: bool = False, trailer: int = 0x00) -> bytes:
    """Helper: assemble one wire-format full frame with a 1024-byte payload."""
    out = bytearray()
    if leading_ff:
        out.append(0xFF)
    out += dm.FRAME_MAGIC
    out.append(dm.FRAME_TYPE_FULL)
    out.append((len(payload) >> 8) & 0xFF)
    out.append(len(payload) & 0xFF)
    out += payload
    out.append(trailer)
    return bytes(out)


def _build_diff_frame(chunks: list[tuple[int, bytes]], *, trailer: int = 0x00) -> bytes:
    payload = bytearray()
    for idx, data in chunks:
        assert len(data) == 8
        payload.append(idx)
        payload += data
    out = bytearray()
    out += dm.FRAME_MAGIC
    out.append(dm.FRAME_TYPE_DIFF)
    out.append((len(payload) >> 8) & 0xFF)
    out.append(len(payload) & 0xFF)
    out += payload
    out.append(trailer)
    return bytes(out)


def test_parse_one_full_frame():
    payload = bytes(range(256)) * 4   # 1024 bytes
    raw = _build_full_frame(payload)
    frames, leftover = dm.parse_frames(bytearray(raw))
    assert len(frames) == 1
    assert frames[0].type_ == dm.FRAME_TYPE_FULL
    assert frames[0].payload == payload
    assert leftover == bytearray()


def test_parse_with_leading_FF_sync_byte():
    payload = b"\x00" * 1024
    raw = _build_full_frame(payload, leading_ff=True)
    frames, leftover = dm.parse_frames(bytearray(raw))
    assert len(frames) == 1
    assert frames[0].type_ == dm.FRAME_TYPE_FULL


def test_parse_two_consecutive_frames():
    p1 = b"\xAA" * 1024
    p2 = b"\x55" * 1024
    raw = _build_full_frame(p1) + _build_full_frame(p2)
    frames, leftover = dm.parse_frames(bytearray(raw))
    assert len(frames) == 2
    assert frames[0].payload == p1
    assert frames[1].payload == p2
    assert leftover == bytearray()


def test_parse_partial_frame_returns_leftover():
    raw = _build_full_frame(b"\x42" * 1024)
    # Truncate the payload halfway — parser should not consume.
    truncated = raw[:500]
    frames, leftover = dm.parse_frames(bytearray(truncated))
    assert frames == []
    assert leftover == bytearray(truncated)


def test_parse_skips_garbage_until_magic():
    junk = b"\x12\x34\x56\x78"
    payload = b"\x77" * 1024
    raw = junk + _build_full_frame(payload)
    frames, leftover = dm.parse_frames(bytearray(raw))
    assert len(frames) == 1
    assert frames[0].payload == payload


def test_parse_diff_frame():
    chunks = [
        (0, b"\xAA" * 8),    # bytes 0..7
        (3, b"\x55" * 8),    # bytes 24..31
    ]
    raw = _build_diff_frame(chunks)
    frames, leftover = dm.parse_frames(bytearray(raw))
    assert len(frames) == 1
    assert frames[0].type_ == dm.FRAME_TYPE_DIFF
    # Payload is the chunks back-to-back (idx + 8 data bytes each).
    assert frames[0].payload[0] == 0
    assert frames[0].payload[1:9] == b"\xAA" * 8
    assert frames[0].payload[9] == 3
    assert frames[0].payload[10:18] == b"\x55" * 8


# ---------------------------------------------------------------------------
# apply_frame — full and diff updates
# ---------------------------------------------------------------------------

def test_apply_full_frame_replaces_buffer():
    fb = bytearray(b"\x00" * 1024)
    payload = bytes(range(256)) * 4
    fr = dm.ParsedFrame(type_=dm.FRAME_TYPE_FULL, payload=payload, trailer=0)
    assert dm.apply_frame(fb, fr) is True
    assert bytes(fb) == payload


def test_apply_full_frame_wrong_size_rejected():
    fb = bytearray(b"\x42" * 1024)
    fr = dm.ParsedFrame(
        type_=dm.FRAME_TYPE_FULL, payload=b"\x00" * 100, trailer=0
    )
    assert dm.apply_frame(fb, fr) is False
    assert bytes(fb) == b"\x42" * 1024  # unchanged


def test_apply_diff_frame_patches_8byte_groups():
    fb = bytearray(b"\x00" * 1024)
    # Replace byte group 5 (bytes 40..47) with all 0xFF.
    payload = bytes([5]) + b"\xFF" * 8
    fr = dm.ParsedFrame(type_=dm.FRAME_TYPE_DIFF, payload=payload, trailer=0)
    assert dm.apply_frame(fb, fr) is True
    assert fb[:40] == b"\x00" * 40
    assert fb[40:48] == b"\xFF" * 8
    assert fb[48:] == b"\x00" * (1024 - 48)


def test_apply_diff_frame_multiple_chunks():
    fb = bytearray(b"\x00" * 1024)
    payload = (
        bytes([0]) + b"\xAA" * 8 +     # bytes 0..7
        bytes([10]) + b"\x55" * 8      # bytes 80..87
    )
    fr = dm.ParsedFrame(type_=dm.FRAME_TYPE_DIFF, payload=payload, trailer=0)
    assert dm.apply_frame(fb, fr) is True
    assert fb[0:8] == b"\xAA" * 8
    assert fb[80:88] == b"\x55" * 8


def test_apply_unknown_frame_type_returns_false():
    fb = bytearray(b"\x42" * 1024)
    fr = dm.ParsedFrame(type_=0xEE, payload=b"\x00" * 16, trailer=0)
    assert dm.apply_frame(fb, fr) is False


# ---------------------------------------------------------------------------
# framebuffer_to_pixels — bit layout
# ---------------------------------------------------------------------------

def test_pixels_all_off():
    fb = b"\x00" * 1024
    pixels = dm.framebuffer_to_pixels(fb)
    assert len(pixels) == 64
    assert all(len(row) == 128 for row in pixels)
    assert all(not p for row in pixels for p in row)


def test_pixels_all_on():
    fb = b"\xFF" * 1024
    pixels = dm.framebuffer_to_pixels(fb)
    assert all(p for row in pixels for p in row)


def test_pixels_first_byte_holds_pixels_0_to_7_of_row_0():
    # Byte 0 = 0b00000001 → only pixel (y=0, x=0) is on (bit 0 of byte 0).
    fb = bytearray(b"\x00" * 1024)
    fb[0] = 0b0000_0001
    pixels = dm.framebuffer_to_pixels(bytes(fb))
    assert pixels[0][0] is True
    assert pixels[0][1] is False
    assert pixels[0][7] is False


def test_pixels_row_continues_across_byte_boundary():
    # Bit 8 = byte 1, bit 0 → pixel (y=0, x=8).
    fb = bytearray(b"\x00" * 1024)
    fb[1] = 0b0000_0001
    pixels = dm.framebuffer_to_pixels(bytes(fb))
    assert pixels[0][8] is True
    assert pixels[0][7] is False


def test_pixels_row_wraps_after_128_pixels():
    # Bit 128 = first pixel of row 1 = byte 16 bit 0.
    fb = bytearray(b"\x00" * 1024)
    fb[16] = 0b0000_0001
    pixels = dm.framebuffer_to_pixels(bytes(fb))
    assert pixels[0][127] is False
    assert pixels[1][0] is True


# ---------------------------------------------------------------------------
# round-trip parse → apply → render
# ---------------------------------------------------------------------------

def test_full_frame_round_trip_to_pixels():
    # Build a known framebuffer and parse it back through the full chain.
    payload = bytearray(b"\x00" * 1024)
    payload[0] = 0xFF       # row 0, pixels 0..7 all on
    payload[16] = 0x80      # row 1, pixel 7 on
    raw = _build_full_frame(bytes(payload))

    frames, _ = dm.parse_frames(bytearray(raw))
    assert len(frames) == 1
    fb = bytearray(b"\x00" * 1024)
    assert dm.apply_frame(fb, frames[0]) is True

    pixels = dm.framebuffer_to_pixels(bytes(fb))
    for x in range(8):
        assert pixels[0][x] is True
    assert pixels[1][7] is True
    assert pixels[1][6] is False
