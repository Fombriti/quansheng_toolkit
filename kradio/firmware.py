"""
Quansheng firmware (.bin) file parser. Offline, zero hardware risk —
just inspects what's inside a firmware file before any flash decision.

Quansheng firmware files come in three observed flavours:

  1. **Packed + CRC** — the official Quansheng download format. The
     last 2 bytes are a CRC-CCITT-LE over the rest. The body is then
     XOR-ed with a fixed 128-byte key. After decoding, a 16-byte
     "version info" block sits at offset 0x2000 (just past the 8 KB
     mark inside the firmware blob).

  2. **Packed, no CRC** — what some custom-firmware GitHub releases
     ship. Same XOR key + version block at 0x2000, but no trailing CRC.

  3. **Raw ARM binary** — the unprotected firmware as it sits in MCU
     flash. The first 4 bytes are the Cortex-M reset stack pointer,
     which always lives in the chip's RAM range (0x20000000-0x20010000).
     Detect by looking at those bytes; no XOR needed.

Decoding strategy is the same as the upstream K5/K1 tooling' `unpackFirmware()`
(the published K5 protocol reference) — port-for-port equivalent so
the bytes line up with what the bootloader expects to receive.

Reference: (MIT-licensed code
forked from the Joaquim K5 editor — protocol authoritatively
documented in their `protocol.js`).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


# --- Constants -------------------------------------------------------------

# 128-byte XOR key applied to the *whole* firmware blob (different from the
# 16-byte EEPROM/protocol XOR key). Matches the upstream K5/K1 tooling' `firmwareXor`
# array in protocol.js.
FIRMWARE_XOR_KEY = bytes([
    0x47, 0x22, 0xc0, 0x52, 0x5d, 0x57, 0x48, 0x94, 0xb1, 0x60, 0x60, 0xdb,
    0x6f, 0xe3, 0x4c, 0x7c, 0xd8, 0x4a, 0xd6, 0x8b, 0x30, 0xec, 0x25, 0xe0,
    0x4c, 0xd9, 0x00, 0x7f, 0xbf, 0xe3, 0x54, 0x05, 0xe9, 0x3a, 0x97, 0x6b,
    0xb0, 0x6e, 0x0c, 0xfb, 0xb1, 0x1a, 0xe2, 0xc9, 0xc1, 0x56, 0x47, 0xe9,
    0xba, 0xf1, 0x42, 0xb6, 0x67, 0x5f, 0x0f, 0x96, 0xf7, 0xc9, 0x3c, 0x84,
    0x1b, 0x26, 0xe1, 0x4e, 0x3b, 0x6f, 0x66, 0xe6, 0xa0, 0x6a, 0xb0, 0xbf,
    0xc6, 0xa5, 0x70, 0x3a, 0xba, 0x18, 0x9e, 0x27, 0x1a, 0x53, 0x5b, 0x71,
    0xb1, 0x94, 0x1e, 0x18, 0xf2, 0xd6, 0x81, 0x02, 0x22, 0xfd, 0x5a, 0x28,
    0x91, 0xdb, 0xba, 0x5d, 0x64, 0xc6, 0xfe, 0x86, 0x83, 0x9c, 0x50, 0x1c,
    0x73, 0x03, 0x11, 0xd6, 0xaf, 0x30, 0xf4, 0x2c, 0x77, 0xb2, 0x7d, 0xbb,
    0x3f, 0x29, 0x28, 0x57, 0x22, 0xd6, 0x92, 0x8b,
])

# Position of the 16-byte version info block inside the *decoded* firmware.
VERSION_INFO_OFFSET = 0x2000
VERSION_INFO_LENGTH = 16

# Maximum firmware size accepted by the older K5/K6 bootloader (the upstream K5/K1 tooling
# enforces this in flashGenerateCommand). The K1 / K5 V3 bootloader has a
# higher cap (typically up to ~80 KB) since it pages addresses differently.
# `fits_bootloader` is therefore informational — if a file exceeds 0xF000 it
# can still flash on a K1/K5V3, just not on a K5/K6.
MAX_FIRMWARE_SIZE_K5_K6 = 0xF000
# Conservative ceiling that even the K1/K5V3 bootloader's u16 page count
# implies (65535 pages × 256 = 16 MB — way more than any practical flash).
MAX_FIRMWARE_SIZE_K1_K5V3 = 0x18000   # 96 KB, observed F4HWN files are ~80 KB

# Backwards-compatible alias for callers that just want "the smaller cap".
MAX_FIRMWARE_SIZE = MAX_FIRMWARE_SIZE_K5_K6

# Cortex-M RAM range. The first 4 bytes of a raw firmware are the reset
# vector's stack pointer, which always points into RAM.
ARM_RAM_LO = 0x20000000
ARM_RAM_HI = 0x20010000


# --- CRC-16-CCITT (xmodem variant: poly 0x1021, init 0x0000) ---------------

def crc16_ccitt(data: bytes, crc: int = 0) -> int:
    """Same CRC the radio's serial protocol uses (poly 0x1021, init 0)."""
    poly = 0x1021
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ poly) if (crc & 0x8000) else (crc << 1)
            crc &= 0xFFFF
    return crc


def crc16_ccitt_le(data: bytes) -> bytes:
    """Little-endian 2-byte CRC, as appended by Quansheng official files."""
    crc = crc16_ccitt(data)
    return bytes([crc & 0xFF, (crc >> 8) & 0xFF])


# --- XOR ------------------------------------------------------------------

def firmware_xor(data: bytes) -> bytes:
    """Apply the 128-byte firmware XOR key (XOR is its own inverse)."""
    out = bytearray(data)
    for i, b in enumerate(out):
        out[i] = b ^ FIRMWARE_XOR_KEY[i % len(FIRMWARE_XOR_KEY)]
    return bytes(out)


# --- Format detection -----------------------------------------------------

def is_raw_firmware(data: bytes) -> bool:
    """
    True when the file looks like a raw Cortex-M firmware (unencrypted ARM
    binary). Heuristic: first 4 bytes form a u32 LE that lands in the
    chip's RAM range, which is what the reset vector's initial stack
    pointer always does.
    """
    if len(data) < 8:
        return False
    sp = struct.unpack_from("<I", data, 0)[0]
    return ARM_RAM_LO <= sp <= ARM_RAM_HI


def has_crc_validation(data: bytes) -> bool:
    """True when the trailing 2 bytes match a CRC-CCITT-LE over the rest."""
    if len(data) < 4:
        return False
    expected = crc16_ccitt_le(data[:-2])
    return data[-2:] == expected


# --- Public API -----------------------------------------------------------

@dataclass(frozen=True)
class FirmwareInfo:
    """Read-only summary of a parsed firmware file."""
    path: Path
    size_bytes: int
    is_raw: bool
    has_crc: bool
    crc_valid: bool
    version_string: str
    decoded_size: int          # size after CRC strip + version block removal
    fits_k5_k6: bool           # decoded_size <= MAX_FIRMWARE_SIZE_K5_K6 (60 KB)
    fits_k1_k5v3: bool         # decoded_size <= MAX_FIRMWARE_SIZE_K1_K5V3 (96 KB)

    @property
    def fits_bootloader(self) -> bool:
        """Backwards-compatible: True if it fits the smaller K5/K6 cap."""
        return self.fits_k5_k6


def unpack_firmware_version(encoded: bytes) -> bytes:
    """
    Return the 16 raw bytes of the version block (still as bytes — the
    caller decides on encoding). Handles all three file flavours.
    """
    if is_raw_firmware(encoded):
        return encoded[VERSION_INFO_OFFSET:VERSION_INFO_OFFSET + VERSION_INFO_LENGTH]

    if has_crc_validation(encoded):
        decoded = firmware_xor(encoded[:-2])  # strip trailing CRC
    else:
        decoded = firmware_xor(encoded)

    if len(decoded) < VERSION_INFO_OFFSET + VERSION_INFO_LENGTH:
        raise ValueError(
            f"firmware too short to contain version info "
            f"(got {len(decoded)} bytes, need at least "
            f"{VERSION_INFO_OFFSET + VERSION_INFO_LENGTH})"
        )
    return decoded[VERSION_INFO_OFFSET:VERSION_INFO_OFFSET + VERSION_INFO_LENGTH]


def unpack_firmware(encoded: bytes) -> bytes:
    """
    Return the firmware bytes ready to be sent to the bootloader. For a
    packed file this means: optionally strip CRC, decrypt with the XOR
    key, then remove the 16-byte version block sitting at 0x2000. Raw
    files are returned untouched (the bootloader accepts them as-is).
    """
    if is_raw_firmware(encoded):
        return encoded

    if has_crc_validation(encoded):
        calculated = crc16_ccitt_le(encoded[:-2])
        if calculated != encoded[-2:]:
            raise ValueError("firmware CRC check failed")
        decoded = firmware_xor(encoded[:-2])
    else:
        decoded = firmware_xor(encoded)

    if len(decoded) < VERSION_INFO_OFFSET + VERSION_INFO_LENGTH:
        raise ValueError(
            f"firmware too short ({len(decoded)} bytes); cannot strip "
            f"version block at 0x{VERSION_INFO_OFFSET:04X}"
        )
    # Remove the version block — the bootloader doesn't want it.
    return (decoded[:VERSION_INFO_OFFSET]
            + decoded[VERSION_INFO_OFFSET + VERSION_INFO_LENGTH:])


def _ascii_decode(raw: bytes) -> str:
    """Decode the version block to a clean ASCII string (or '?' on garbage)."""
    text = raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")
    return text if text and all(0x20 <= ord(c) < 0x7F for c in text) else "?"


def parse_firmware_file(path: Path) -> FirmwareInfo:
    """
    Inspect a firmware file. Doesn't talk to a radio, doesn't write
    anything — pure offline parsing for sanity-checking a download
    before flashing it.
    """
    data = Path(path).read_bytes()
    raw = is_raw_firmware(data)
    has_crc = has_crc_validation(data)

    try:
        version_bytes = unpack_firmware_version(data)
        version_str = _ascii_decode(version_bytes)
    except ValueError:
        version_str = "(invalid)"

    try:
        decoded = unpack_firmware(data)
        decoded_size = len(decoded)
        fits_k5 = decoded_size <= MAX_FIRMWARE_SIZE_K5_K6
        fits_k1 = decoded_size <= MAX_FIRMWARE_SIZE_K1_K5V3
    except ValueError:
        decoded_size = 0
        fits_k5 = False
        fits_k1 = False

    return FirmwareInfo(
        path=Path(path),
        size_bytes=len(data),
        is_raw=raw,
        has_crc=has_crc,
        crc_valid=has_crc,   # if has_crc was True, validation passed
        version_string=version_str,
        decoded_size=decoded_size,
        fits_k5_k6=fits_k5,
        fits_k1_k5v3=fits_k1,
    )
