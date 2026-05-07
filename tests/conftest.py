"""
Shared pytest fixtures for the kradio tests.

The sample EEPROM is generated PROGRAMMATICALLY rather than loaded from a
checked-in .bin so the test suite carries no user-specific data (channel
names, frequencies, callsigns) and works on a fresh CI clone where the
real radio dump is gitignored.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _synthesize_eeprom() -> bytes:
    """
    Build a 45456-byte EEPROM image populated with a small set of public,
    neutral test channels and minimal settings. Exercises the same decode
    paths the integration tests need without exposing any real user data.
    """
    from quansheng_toolkit.kradio import memory as mem
    from quansheng_toolkit.kradio import settings as setmod
    from quansheng_toolkit.kradio import protocol as proto

    img = bytearray(b"\xff" * proto.MEM_SIZE)

    # A few channels with neutral, well-known public frequencies.
    mem.patch_channel_in_image(
        img, idx=0, name="HAM-V", freq_hz=145_500_000, mode="FM", scanlist=1
    )
    mem.patch_channel_in_image(
        img, idx=1, name="HAM-U", freq_hz=433_500_000, mode="FM", scanlist=1
    )
    mem.patch_channel_in_image(
        img, idx=2, name="MAR-16", freq_hz=156_800_000, mode="FM", scanlist=2
    )
    mem.patch_channel_in_image(
        img, idx=3, name="PMR-1", freq_hz=446_006_250, mode="FM", scanlist=2
    )

    # Some settings so test_settings has non-uninitialised values to read.
    setmod.apply_setting(img, "squelch", "3")
    setmod.apply_setting(img, "vox_switch", "off")
    setmod.apply_setting(img, "tx_vfo", "A")
    setmod.apply_setting(img, "battery_type", "1400 mAh K1")

    # Firmware version string that lives at 0xA160 (matches what the radio
    # itself reports back via the hello response).
    fw = b"v5.4.0\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"
    img[0xA160:0xA160 + len(fw)] = fw

    # Boot channel = ch1 (0-based 0). Both the screen and MR pointers.
    struct.pack_into("<H", img, 0xA010, 0)
    struct.pack_into("<H", img, 0xA012, 0)
    struct.pack_into("<H", img, 0xA016, 0)
    struct.pack_into("<H", img, 0xA018, 0)

    return bytes(img)


def _synthesize_k1_stock_eeprom() -> bytes:
    """
    Build an 8 KB EEPROM image for the K5 V1 / K1 stock layout. Same
    privacy-safe philosophy as `_synthesize_eeprom()` — only public,
    neutral channels and no user-specific data.

    The image exercises the offsets the K5 V1 modules read from:
      0x0000  channel records + names + ch_attr
      0x0E70  user settings (squelch, vox, mic gain, ...)
      0x0EB0  boot logo lines
      0x1D00  start of calibration region (untouched: stays 0xFF)
    """
    from quansheng_toolkit.kradio import memory_uvk5_v1 as mm
    from quansheng_toolkit.kradio import settings_uvk5_v1 as smm

    img = bytearray(b"\xff" * mm.MEM_SIZE)

    mm.patch_channel_in_image(
        img, idx=0, name="HAM-V", freq_hz=145_500_000, mode="FM", scanlist=3
    )
    mm.patch_channel_in_image(
        img, idx=1, name="HAM-U", freq_hz=433_500_000, mode="FM", scanlist=1
    )
    mm.patch_channel_in_image(
        img, idx=2, name="MAR-16", freq_hz=156_800_000, mode="FM", scanlist=2
    )
    mm.patch_channel_in_image(
        img, idx=3, name="PMR-1", freq_hz=446_006_250, mode="FM", scanlist=0
    )

    smm.apply_setting(img, "squelch", "3")
    smm.apply_setting(img, "vox_switch", "off")
    smm.apply_setting(img, "mic_gain", "2")
    smm.apply_setting(img, "battery_type", "1600 mAh")
    smm.apply_setting(img, "channel_display_mode", "Channel Name")
    smm.apply_setting(img, "button_beep", "on")
    smm.apply_setting(img, "logo_line1", "TESTRADIO")
    smm.apply_setting(img, "logo_line2", "K1 STOCK")

    return bytes(img)


@pytest.fixture(scope="session")
def sample_eeprom_bytes() -> bytes:
    """A synthetic EEPROM dump (no user data) suitable for read-path tests."""
    return _synthesize_eeprom()


@pytest.fixture
def sample_eeprom(sample_eeprom_bytes) -> bytearray:
    """A mutable copy of the synthetic EEPROM (per-test, fresh)."""
    return bytearray(sample_eeprom_bytes)


@pytest.fixture(scope="session")
def k1_stock_eeprom_bytes() -> bytes:
    """A synthetic K5 V1 / K1 stock EEPROM (8 KB, no user data)."""
    return _synthesize_k1_stock_eeprom()


@pytest.fixture
def k1_stock_eeprom(k1_stock_eeprom_bytes) -> bytearray:
    """A mutable copy of the synthetic K1 stock EEPROM (per-test, fresh)."""
    return bytearray(k1_stock_eeprom_bytes)
