"""Unit tests for the serial protocol primitives (XOR, CRC, framing)."""
from __future__ import annotations

import pytest

from quansheng_toolkit.kradio import protocol as proto


class TestXorObfuscation:
    def test_xor_is_self_inverse(self):
        data = b"Hello, World! This is a test."
        assert proto.xor_payload(proto.xor_payload(data)) == data

    def test_xor_empty(self):
        assert proto.xor_payload(b"") == b""

    def test_xor_known_vector(self):
        # First 4 bytes of XOR_KEY are 0x16, 0x6C, 0x14, 0xE6
        data = b"\x00\x00\x00\x00"
        assert proto.xor_payload(data) == b"\x16\x6C\x14\xE6"


class TestCrc16Xmodem:
    def test_known_vector(self):
        # CRC-16/CCITT init=0 of "123456789" is 0x31C3.
        assert proto.crc16_xmodem(b"123456789") == 0x31C3

    def test_empty_input(self):
        assert proto.crc16_xmodem(b"") == 0


class TestFraming:
    def test_build_frame_layout(self):
        payload = b"\x14\x05\x04\x00\x6a\x39\x57\x64"
        frame = proto.build_frame(payload)
        # Frame layout: HEAD(2) + LEN(2) + xor(payload+crc)(len+2) + TAIL(2)
        assert frame[:2] == b"\xab\xcd"
        assert frame[-2:] == b"\xdc\xba"
        assert int.from_bytes(frame[2:4], "little") == len(payload)
        assert len(frame) == 8 + len(payload)

    def test_parse_frame_round_trip(self):
        payload = b"some payload \xff\x00\xab"
        frame = proto.build_frame(payload)
        # In `recv()` the receiver reads HEAD+LEN as the 4-byte "header",
        # then `body_len = header[2]` bytes for the obfuscated payload, then
        # 4 bytes of "footer" (= obfuscated CRC + TAIL). parse_frame returns
        # the de-obfuscated body, which is exactly the original payload.
        header = frame[:4]
        body_len = header[2]
        body = frame[4:4 + body_len]
        footer = frame[4 + body_len:]
        decoded = proto.parse_frame(header, body, footer)
        assert decoded == payload

    def test_parse_frame_bad_header(self):
        with pytest.raises(proto.RadioError):
            proto.parse_frame(b"\x00\x00\x00\x00", b"x", b"\x00\x00\xdc\xba")

    def test_parse_frame_bad_footer(self):
        with pytest.raises(proto.RadioError):
            proto.parse_frame(b"\xab\xcd\x01\x00", b"x", b"\x00\x00\x00\x00")
