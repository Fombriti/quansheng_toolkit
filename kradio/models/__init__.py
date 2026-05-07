"""
Per-radio configuration profiles.

A `RadioProfile` bundles every difference between firmware-flavoured
versions of the K5/K1 family — EEPROM size, channel count, scan-list
count, the right memory-map module, and which settings to expose.

The serial protocol (`kradio.protocol`) is identical across all profiles:
XOR key, CRC, frame, hello, read/write commands. The radio MCU differs
between K5 V1/V2 (DP32G030) and K5 V3 / K1 (PY32F071) but the bridge
protocol is shared.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import ModuleType
from typing import Optional


@dataclass(frozen=True)
class RadioProfile:
    name: str
    description: str
    firmware_signatures: tuple[str, ...]   # any of these substrings → match
    mem_size: int
    prog_size: int
    cal_start: int
    num_channels: int
    num_scan_lists: int
    # Reference to the kradio memory module that decodes/encodes channels
    # and computes EEPROM offsets for THIS profile.
    memory_module: Optional[ModuleType] = None
    # Reference to the kradio settings module that exposes the typed
    # registry for this profile (or None if not yet ported).
    settings_module: Optional[ModuleType] = None
    # `verified` is True only for profiles that have been tested on real
    # hardware. Untested profiles are read-only by default.
    verified: bool = False
    # Free-form notes shown to the user when the profile is detected.
    notes: str = ""


# ---- Lazy imports to avoid circular dependencies -------------------------

def _f4hwn_memory():
    from .. import memory
    return memory


def _f4hwn_settings():
    from .. import settings
    return settings


def _uvk5_v1_memory():
    from .. import memory_uvk5_v1
    return memory_uvk5_v1


def _uvk5_v1_settings():
    from .. import settings_uvk5_v1
    return settings_uvk5_v1


# ---- Profile registry ----------------------------------------------------

F4HWN_FUSION_5X = RadioProfile(
    name="F4HWN Fusion 5.x",
    description="Quansheng UV-K1, UV-K1(8) v3 Mini Kong and UV-K5 V3 "
                "(PY32F071) running F4HWN Fusion 5.x firmware",
    firmware_signatures=("F4HWN",),
    mem_size=0xB190,
    prog_size=0xB000,
    cal_start=0xB000,
    num_channels=1024,
    num_scan_lists=24,
    memory_module=_f4hwn_memory(),
    settings_module=_f4hwn_settings(),
    verified=True,
    notes="Fully supported. Tested on UV-K1 hardware.",
)

# UV-K5 V1/V2 / K5(8) / K6 / 5R stock Quansheng firmware (8 KB EEPROM,
# 200 channels, 2 scan lists). Same protocol but very different memory
# layout from F4HWN. Detection prefixes match kk7ds CHIRP driver uvk5.py.
UVK5_STOCK = RadioProfile(
    name="UV-K5 stock (DP32G030)",
    description="Quansheng UV-K5 / K5(8) / K6 / 5R running stock Quansheng "
                "or early modded firmware (DP32G030 MCU, 8 KB EEPROM)",
    firmware_signatures=(
        "k5_2.01.", "2.01.", "3.00.", "4.00.", "5.00.",
        "7.00.",   # K5(8) / K6
        "1.02.",   # OEM variant (1o11)
    ),
    mem_size=0x2000,
    prog_size=0x1d00,
    # Calibration starts at 0x1E00, NOT 0x1D00. The 256 bytes between
    # 0x1D00..0x1E00 are reserved/unused (always 0xFF on factory) and
    # writing into them confuses the stock firmware's boot-time cal
    # validation (it zeroes the whole 0x1D00..0x2000 on next boot).
    # Source: the published K5 calibration reference CAL_START = 0x1E00.
    cal_start=0x1e00,
    num_channels=200,
    num_scan_lists=2,
    memory_module=_uvk5_v1_memory(),
    settings_module=_uvk5_v1_settings(),
    verified=True,
    notes="Tested on UV-K5(8) firmware 7.00.11. Read + write + settings "
          "(logo, channel display mode, channel record) AND calibration "
          "dump/restore at 0x1D00..0x2000 (768 bytes) all verified end-to-end. "
          "Same EEPROM layout as UV-K1 stock 7.03.x.",
)

# UV-K1 with STOCK Quansheng firmware (PY32F071 MCU). Confirmed on
# hardware 2026-05-01: a fresh dump from a K1 stock unit (firmware 7.03.01)
# decodes byte-perfect through the K5 V1 memory map; CHIRP-style sequential
# upload of 0x0000..0x1D00 was validated end-to-end (channels written,
# verified via re-read). The MCU differs from K5 V1 (PY32F071 vs DP32G030)
# but the EEPROM layout is the same.
UVK1_STOCK = RadioProfile(
    name="UV-K1 stock (PY32F071)",
    description="Quansheng UV-K1 with stock factory firmware. Memory map "
                "matches the legacy UV-K5 V1 layout (200 channels, 2 scan "
                "lists, 8 KB EEPROM). Read + channel writes verified.",
    firmware_signatures=("7.03.",),
    mem_size=0x2000,
    prog_size=0x1d00,
    # See UVK5_STOCK comment: cal is 0x1E00..0x2000 (512 B), not
    # 0x1D00..0x2000 (768 B). the upstream authoritative value.
    cal_start=0x1e00,
    num_channels=200,
    num_scan_lists=2,
    memory_module=_uvk5_v1_memory(),
    settings_module=_uvk5_v1_settings(),
    verified=True,
    notes="Tested on UV-K1 firmware 7.03.01. Read + channel-list write "
          "verified end-to-end. Calibration region is 0x1E00..0x2000 "
          "(512 bytes); the 256 bytes at 0x1D00..0x1E00 are reserved "
          "and must NOT be written. Full settings registry (channels "
          "+ DTMF + FM presets + password) ported.",
)


# Order matters: more specific signatures first.
ALL_PROFILES: tuple[RadioProfile, ...] = (
    F4HWN_FUSION_5X,
    UVK1_STOCK,        # 7.03. is K1-specific, check before generic K5 prefixes
    UVK5_STOCK,
)


DEFAULT_PROFILE = F4HWN_FUSION_5X


def select_profile(firmware_string: str) -> RadioProfile:
    """
    Pick the best-matching radio profile for a firmware version string.
    Falls back to DEFAULT_PROFILE if no signature matches. Use
    `is_recognized_firmware()` if you need to detect that fallback case.
    """
    if not firmware_string:
        return DEFAULT_PROFILE
    fw_upper = firmware_string.upper()
    for profile in ALL_PROFILES:
        for sig in profile.firmware_signatures:
            if sig.upper() in fw_upper:
                return profile
    return DEFAULT_PROFILE


def is_recognized_firmware(firmware_string: str) -> bool:
    """
    True if the firmware string matches any registered profile signature.
    False means the radio reported a firmware we have never seen — the
    decoder will still run with the DEFAULT profile but the result is
    almost certainly wrong, so callers should warn the user loudly.
    """
    if not firmware_string:
        return False
    fw_upper = firmware_string.upper()
    for profile in ALL_PROFILES:
        for sig in profile.firmware_signatures:
            if sig.upper() in fw_upper:
                return True
    return False
