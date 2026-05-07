"""
Settings region decoder + writable settings registry.

The settings region starts at 0xA000 and ends around 0xA170. This module
exposes:

  * a typed `Settings` snapshot decoded from a complete EEPROM image, and
  * a registry of named, writable settings — each entry knows how to read
    its current value, validate a new one, and patch the bytes back into
    an EEPROM image while preserving any neighbouring bitfields.

References (memory map authoritative source):
    f4hwn.fusion.chirp.v5.4.0.py — `MEM_FORMAT` + the matching `set_settings`
    UI bindings in the same file.

Only fields that are reasonably user-actionable are exposed in the registry.
Calibration, DTMF kill/revive codes and other dangerous values are NOT
included; they remain accessible via raw EEPROM reads.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Callable

from .memory import (
    SETTINGS_BASE,
    SETTINGS_END,
    NUM_LISTS,
)

# --- Enumerations and lookup tables (from CHIRP driver) -------------------

# Up to 4 power levels: USER, LOW1..LOW5, MID, HIGH (used in channel records).
POWER_LEVELS = ["USER", "LOW1", "LOW2", "LOW3", "LOW4", "LOW5", "MID", "HIGH"]

CHANNEL_DISPLAY_MODES = ["Frequency", "Channel No", "Channel Name", "Name + Freq"]
CROSSBAND_MODES = ["OFF", "Band A", "Band B"]
DUAL_WATCH_MODES = ["OFF", "Band A", "Band B"]
BATTERY_SAVE_MODES = ["OFF", "1:1", "1:2", "1:3", "1:4", "1:5"]
# Generated 5-second-step list followed by "Always On" — matches CHIRP driver.
BACKLIGHT_TIMES = (
    ["OFF"]
    + [f"{m} min : {s} sec" if m else f"{s} sec"
       for m in range(0, 5)
       for s in range(5, 60, 5)
       if not (m == 0 and s == 0)]
    + ["5 min", "Always On (ON)"]
)
BATTERY_TYPES = ["1600 mAh K5", "2200 mAh K5", "3500 mAh K5",
                 "1400 mAh K1", "2500 mAh K1"]
BATTERY_TEXT_LIST = ["NONE", "VOLTAGE", "PERCENT"]
BACKLIGHT_TX_RX = ["OFF", "TX", "RX", "TX/RX"]
ROGER_BEEP_MODES = ["OFF", "Roger", "MDC"]
SCAN_RESUME_MODES = ["TIME", "CARRIER", "STOP"]
SET_OFF_ON = ["OFF", "ON"]
TX_VFO_MODES = ["A", "B"]
NFM_MODES = ["NARROW", "NARROWER"]
VOICE_MODES = ["OFF", "Chinese", "English"]
ALARM_MODES = ["SITE", "TONE"]
WELCOME_MODES = [
    "ALL (msg + voltage + sound)",
    "SOUND only",
    "MESSAGE only",
    "VOLTAGE only",
    "NONE",
]
RX_MODES = ["MAIN ONLY", "DUAL RX RESPOND", "CROSS BAND", "MAIN TX DUAL RX"]
PTTID_MODES = ["OFF", "UP CODE", "DOWN CODE", "UP+DOWN CODE", "APOLLO QUINDAR"]
COMPANDER_MODES = ["OFF", "TX", "RX", "TX/RX"]

# F4HWN-custom enums
SET_PTT_MODES = ["CLASSIC", "ONEPUSH"]
SET_TOT_EOT_MODES = ["OFF", "SOUND", "VISUAL", "ALL"]
SET_LCK_MODES = ["KEYS", "KEYS+PTT"]
SET_MET_MODES = ["TINY", "CLASSIC"]
SET_RXA_FM_MODES = ["FLAT", "CLEAN", "MID", "BOOST", "MAX"]
SET_RXA_AM_MODES = ["SHARP", "STOCK", "OPEN"]
SET_KEY_MODES = ["MENU", "KEY_UP", "KEY_DOWN", "KEY_EXIT", "KEY_STAR"]
SET_NAV_MODES = ["LEFT/RIGHT (UV-K1)", "UP/DOWN (UV-K5 V3)"]
SET_LOW_MODES = ["< 20mW", "125mW", "250mW", "500mW", "1W", "2W", "5W"]

# TX frequency lock — coarse list of regional band restrictions.
FLOCK_MODES = [
    "DEFAULT+ (137-174, 400-470)",
    "FCC HAM (144-148, 420-450)",
    "CA HAM (144-148, 430-450)",
    "CE HAM (144-146, 430-440)",
    "GB HAM (144-148, 430-440)",
    "137-174, 400-430",
    "137-174, 400-438",
    "PMR 446",
    "GMRS FRS MURS",
    "DISABLE ALL",
    "UNLOCK ALL",
]

# Programmable side keys. Mirrors the driver's KEYACTIONS_LIST.
KEY_ACTIONS = [
    "NONE", "FLASHLIGHT", "POWER", "MONITOR", "SCAN", "VOX", "ALARM",
    "FM RADIO", "1750Hz", "LOCK KEYPAD", "VFO A / VFO B", "VFO / MEM",
    "MODE", "BL_MIN_TMP_OFF", "RX MODE", "MAIN ONLY", "PTT",
    "WIDE / NARROW", "BACKLIGHT", "MUTE", "RxA", "POWER HIGH",
    "REMOVE OFFSET",
]

MIC_GAIN_LEVELS = [
    "+1.5dB", "+4.0dB", "+8.0dB", "+12.0dB", "+16.0dB",
    "+20.0dB", "+24.0dB", "+28.0dB", "+31.5dB",
]


# --- Typed snapshot --------------------------------------------------------

@dataclass
class GeneralSettings:
    squelch: int               # 0..9
    max_talk_time: int         # 0..255 (minutes; 0 = off)
    mic_gain: int              # 0..8 (index into MIC_GAIN_LIST)
    vox_switch: bool
    vox_level: int             # 0..9
    backlight_min: int         # 0..15 brightness floor
    backlight_max: int         # 0..15 brightness peak
    backlight_time: str        # decoded label
    channel_display_mode: str
    dual_watch: str
    crossband: str
    battery_save: str
    battery_type: str
    roger_beep: str
    tx_vfo: str
    nfm_mode: str
    ste: bool                  # squelch tail elimination


@dataclass
class SessionState:
    """The current selected channels — overwritten by apply-full uploads."""
    screen_channel_a: int
    screen_channel_b: int
    mr_channel_a: int
    mr_channel_b: int
    freq_channel_a: int
    freq_channel_b: int
    noaa_channel_a: int
    noaa_channel_b: int


@dataclass
class FmRadio:
    """48 broadcast FM presets, frequencies in MHz (or None if empty)."""
    presets: list[float | None]


@dataclass
class KeyBindings:
    button_beep: bool
    keyM_longpress_action: int   # 0..N (semantics in driver KEYACTIONS_LIST)
    key1_short: int
    key1_long: int
    key2_short: int
    key2_long: int
    scan_resume_mode: str
    auto_keypad_lock: bool
    power_on_dispmode: int


@dataclass
class Logo:
    """Custom 2-line boot logo (16 chars each, ASCII)."""
    line1: str
    line2: str


@dataclass
class ScanPriority:
    enabled: bool
    default_list: int          # 1..24
    priority_ch1: int          # 0-based channel index
    priority_ch2: int
    call_channel: int


@dataclass
class BuildOptions:
    """Compile-time feature flags reported by the firmware."""
    dtmf_calling: bool
    pwron_password: bool
    tx_1750: bool
    alarm: bool
    vox: bool
    voice: bool
    noaa: bool
    fm_radio: bool
    rescue_ops: bool
    bandscope: bool
    am_fix: bool
    f4hwn_game: bool
    raw_demodulators: bool
    wide_rx: bool
    flashlight: bool


@dataclass
class Settings:
    firmware_version: str
    general: GeneralSettings
    session: SessionState
    fm_radio: FmRadio
    keys: KeyBindings
    logo: Logo
    scan_priority: ScanPriority
    build: BuildOptions


# --- Helpers ---------------------------------------------------------------

def _u16le(buf: bytes, addr: int) -> int:
    return struct.unpack_from("<H", buf, addr)[0]


def _ascii(buf: bytes, addr: int, length: int) -> str:
    raw = buf[addr:addr + length]
    out: list[str] = []
    for b in raw:
        if b == 0xFF or b == 0x00:
            break
        if 0x20 <= b <= 0x7E:
            out.append(chr(b))
    return "".join(out).rstrip()


def _enum(values: list[str], idx: int, default: str = "?") -> str:
    return values[idx] if 0 <= idx < len(values) else f"{default}({idx})"


# --- Decoder ---------------------------------------------------------------

def decode_settings(eeprom: bytes) -> Settings:
    """Decode the settings region from a complete EEPROM image."""
    if len(eeprom) < SETTINGS_END:
        raise ValueError(
            f"EEPROM image too short ({len(eeprom)} < {SETTINGS_END})"
        )

    # Firmware version string at 0xA160 (16 chars)
    fw = _ascii(eeprom, 0xA160, 16)

    # ---- 0xA000 block: general --------------------------------------------
    set_rxa = eeprom[0xA000]
    squelch = eeprom[0xA001]
    max_talk_time = eeprom[0xA002]
    # 0xA003 noaa_autoscan
    flags_a004 = eeprom[0xA004]
    vox_switch = bool(eeprom[0xA005])
    vox_level = eeprom[0xA006]
    mic_gain = eeprom[0xA007]
    bl_byte = eeprom[0xA008]
    backlight_min = bl_byte & 0x0F
    backlight_max = (bl_byte >> 4) & 0x0F
    channel_display_mode = eeprom[0xA009]
    crossband = eeprom[0xA00A]
    battery_save = eeprom[0xA00B]
    dual_watch = eeprom[0xA00C]
    backlight_time = eeprom[0xA00D]
    flags_a00e = eeprom[0xA00E]
    nfm_idx = (flags_a00e >> 1) & 0x03
    ste = bool(flags_a00e & 0x01)
    # 0xA00F current_state

    general = GeneralSettings(
        squelch=squelch,
        max_talk_time=max_talk_time,
        mic_gain=mic_gain,
        vox_switch=vox_switch,
        vox_level=vox_level,
        backlight_min=backlight_min,
        backlight_max=backlight_max,
        backlight_time=_enum(BACKLIGHT_TIMES, backlight_time),
        channel_display_mode=_enum(CHANNEL_DISPLAY_MODES, channel_display_mode),
        dual_watch=_enum(DUAL_WATCH_MODES, dual_watch),
        crossband=_enum(CROSSBAND_MODES, crossband),
        battery_save=_enum(BATTERY_SAVE_MODES, battery_save),
        battery_type=_enum(BATTERY_TYPES, eeprom[0xA0C4]),
        roger_beep=_enum(ROGER_BEEP_MODES, eeprom[0xA0C1]),
        tx_vfo=_enum(TX_VFO_MODES, eeprom[0xA0C3]),
        nfm_mode=_enum(NFM_MODES, nfm_idx),
        ste=ste,
    )

    # ---- 0xA010 block: session state --------------------------------------
    session = SessionState(
        screen_channel_a=_u16le(eeprom, 0xA010),
        mr_channel_a=_u16le(eeprom, 0xA012),
        freq_channel_a=_u16le(eeprom, 0xA014),
        screen_channel_b=_u16le(eeprom, 0xA016),
        mr_channel_b=_u16le(eeprom, 0xA018),
        freq_channel_b=_u16le(eeprom, 0xA01A),
        noaa_channel_a=_u16le(eeprom, 0xA01C),
        noaa_channel_b=_u16le(eeprom, 0xA01E),
    )

    # ---- 0xA028: 48 FM radio presets --------------------------------------
    fm_presets: list[float | None] = []
    for i in range(48):
        raw = _u16le(eeprom, 0xA028 + i * 2)
        if raw == 0xFFFF or raw == 0:
            fm_presets.append(None)
        else:
            # CHIRP stores as MHz*100 (e.g. 100.5 MHz = 10050)
            fm_presets.append(raw / 100.0)
    fm_radio = FmRadio(presets=fm_presets)

    # ---- 0xA0A8: keys ------------------------------------------------------
    a0a8 = eeprom[0xA0A8]
    keys = KeyBindings(
        button_beep=bool(a0a8 & 0x80),
        keyM_longpress_action=a0a8 & 0x7F,
        key1_short=eeprom[0xA0A9],
        key1_long=eeprom[0xA0AA],
        key2_short=eeprom[0xA0AB],
        key2_long=eeprom[0xA0AC],
        scan_resume_mode=_enum(SCAN_RESUME_MODES, eeprom[0xA0AD]),
        auto_keypad_lock=bool(eeprom[0xA0AE]),
        power_on_dispmode=eeprom[0xA0AF],
    )

    # ---- 0xA0C8: 2-line boot logo -----------------------------------------
    logo = Logo(
        line1=_ascii(eeprom, 0xA0C8, 16),
        line2=_ascii(eeprom, 0xA0D8, 16),
    )

    # ---- 0xA130: scan priority --------------------------------------------
    sp_byte = eeprom[0xA130]
    scan_prio = ScanPriority(
        enabled=bool(sp_byte & 0x01),
        default_list=(sp_byte >> 1) & 0x7F,
        priority_ch1=_u16le(eeprom, 0xA131),
        priority_ch2=_u16le(eeprom, 0xA133),
        call_channel=_u16le(eeprom, 0xA135),
    )

    # ---- 0xA158: build options --------------------------------------------
    b0 = eeprom[0xA158]
    b1 = eeprom[0xA159]
    build = BuildOptions(
        dtmf_calling=bool(b0 & 0x80),
        pwron_password=bool(b0 & 0x40),
        tx_1750=bool(b0 & 0x20),
        alarm=bool(b0 & 0x10),
        vox=bool(b0 & 0x08),
        voice=bool(b0 & 0x04),
        noaa=bool(b0 & 0x02),
        fm_radio=bool(b0 & 0x01),
        rescue_ops=bool(b1 & 0x40),
        bandscope=bool(b1 & 0x20),
        am_fix=bool(b1 & 0x10),
        f4hwn_game=bool(b1 & 0x08),
        raw_demodulators=bool(b1 & 0x04),
        wide_rx=bool(b1 & 0x02),
        flashlight=bool(b1 & 0x01),
    )

    return Settings(
        firmware_version=fw,
        general=general,
        session=session,
        fm_radio=fm_radio,
        keys=keys,
        logo=logo,
        scan_priority=scan_prio,
        build=build,
    )


# ============================================================ #
#  Writable settings registry                                  #
# ============================================================ #
#
# Each entry knows how to encode a Python value into one or more bytes of
# the EEPROM image, preserving bitfield neighbours. The encoder writes
# in-place into a `bytearray`.
#
# Supported value kinds:
#   "int"     numeric, range checked
#   "bool"    "on"/"off"/"true"/"false"/"1"/"0"
#   "enum"    one of `choices` (case-insensitive); also accepts an integer index
#   "str"     ASCII string padded with 0xFF to `length` bytes
#

@dataclass(frozen=True)
class SettingSpec:
    name: str
    description: str
    addr: int
    kind: str               # "int" / "u16le" / "bool" / "enum" / "str"
    # for int / u16le: (low, high). For enum: list of accepted strings.
    bounds: tuple[int, int] | list[str] | None = None
    # bitfield writers: bit_offset (0..7) + bit_width (1..8). Ignored for u16le.
    bit_offset: int = 0
    bit_width: int = 8
    # str storage size in bytes (the full field).
    length: int = 0
    # str maximum number of user-visible characters. Defaults to `length`
    # but can be smaller when the radio displays fewer chars than it
    # stores (typical for boot logo: 16-byte storage, 12 visible chars).
    display_length: int = 0
    # str_encoding selects how the value is laid out into the EEPROM
    # field. Three patterns are observed in the F4HWN driver:
    #   "ff"   — pad whole field with 0xFF (legacy default; works for
    #            simple terminator-aware fields)
    #   "null" — strip user input, then pad whole field with 0x00
    #            (DTMF up/down/kill/revive/local codes)
    #   "logo" — strip user input, pad visible region with 0x00, then
    #            append 0x00 + 0xFF*(length - display_length - 1)
    #            (boot welcome logo lines — the radio renders space
    #            characters as actual visible spaces, so padding must
    #            be NULL not 0x20)
    str_encoding: str = "ff"


def _encode_str_for(value: str, spec: SettingSpec) -> bytes:
    """
    Render a Python string into the radio's EEPROM byte layout for the
    given str-kind setting. Strips trailing whitespace/null/0xFF first,
    then applies the encoding pattern declared on the spec:

        "ff"   — pad with 0xFF (legacy default)
        "null" — pad with 0x00 across the full field
        "logo" — visible region 0x00-padded, then 0x00 + 0xFF*(N-1)
                 (matches CHIRP's logo_line1/logo_line2 write logic)
    """
    visible = spec.display_length or spec.length
    cleaned = value.rstrip(" \x00\xff").encode("ascii", errors="replace")
    cleaned = cleaned[:visible]
    if spec.str_encoding == "null":
        return cleaned.ljust(spec.length, b"\x00")
    if spec.str_encoding == "logo":
        body = cleaned.ljust(visible, b"\x00")
        # 1 byte 0x00 then 0xFF for the rest of the trailing region.
        trail_len = max(0, spec.length - visible)
        if trail_len == 0:
            return body
        return body + b"\x00" + b"\xFF" * (trail_len - 1)
    # default "ff"
    return cleaned.ljust(spec.length, b"\xFF")


def _patch_bitfield(image: bytearray, addr: int,
                    value: int, bit_offset: int, bit_width: int) -> None:
    """Patch `bit_width` bits at `bit_offset` in `image[addr]`."""
    if not (0 <= bit_offset <= 7 and 1 <= bit_width <= 8 and
            bit_offset + bit_width <= 8):
        raise ValueError("bad bitfield geometry")
    mask = ((1 << bit_width) - 1) << bit_offset
    image[addr] = (image[addr] & ~mask) | ((value << bit_offset) & mask)


def _parse_bool(s: str) -> bool:
    s = s.strip().lower()
    if s in ("1", "true", "yes", "on", "y"):
        return True
    if s in ("0", "false", "no", "off", "n"):
        return False
    raise ValueError(f"expected boolean, got {s!r}")


def _resolve_enum(s: str, choices: list[str]) -> int:
    s = s.strip()
    if s.isdigit():
        idx = int(s)
        if not 0 <= idx < len(choices):
            raise ValueError(f"index {idx} out of range for {choices}")
        return idx
    lower = [c.lower() for c in choices]
    target = s.lower()
    if target in lower:
        return lower.index(target)
    raise ValueError(f"invalid choice {s!r}; expected one of {choices}")


def _encode_fm_freq(value: str) -> int:
    """
    Parse an FM-broadcast preset frequency: '100.5', '100.5 MHz',
    '' / 'OFF' / 'NONE' (= unprogrammed → 0xFFFF). The radio stores the
    frequency as `MHz * 100` in a u16le.
    """
    s = (value or "").strip().upper().replace("MHZ", "").strip()
    if s in ("", "OFF", "NONE"):
        return 0xFFFF
    try:
        f = float(s)
    except ValueError:
        raise ValueError(f"FM preset must be a frequency in MHz, got {value!r}")
    if not 64.0 <= f <= 108.0:
        raise ValueError(f"FM preset {f} MHz out of FM band (64..108)")
    return int(round(f * 100)) & 0xFFFF


def _decode_fm_freq(raw: int) -> str:
    if raw == 0xFFFF or raw == 0:
        return ""
    return f"{raw / 100.0:.2f} MHz"


def apply_setting(image: bytearray, name: str, value: str) -> None:
    """
    Apply a single `key=value` write to an EEPROM image. Raises ValueError
    for unknown names, invalid values or out-of-range numbers.
    """
    spec = SETTINGS_REGISTRY.get(name)
    if spec is None:
        raise ValueError(
            f"unknown setting {name!r} (try `list-settings` to see options)"
        )
    if spec.kind == "fm_freq":
        raw = _encode_fm_freq(value)
        struct.pack_into("<H", image, spec.addr, raw)
        return
    if spec.kind == "int":
        n = int(value)
        lo, hi = spec.bounds  # type: ignore[misc]
        if not lo <= n <= hi:
            raise ValueError(f"{name}={n} out of range [{lo}..{hi}]")
        if spec.bit_width == 8:
            image[spec.addr] = n & 0xFF
        else:
            _patch_bitfield(image, spec.addr, n, spec.bit_offset, spec.bit_width)
    elif spec.kind == "u16le":
        n = int(value)
        lo, hi = spec.bounds  # type: ignore[misc]
        if not lo <= n <= hi:
            raise ValueError(f"{name}={n} out of range [{lo}..{hi}]")
        struct.pack_into("<H", image, spec.addr, n & 0xFFFF)
    elif spec.kind == "u32le":
        n = int(value)
        lo, hi = spec.bounds  # type: ignore[misc]
        if not lo <= n <= hi:
            raise ValueError(f"{name}={n} out of range [{lo}..{hi}]")
        struct.pack_into("<I", image, spec.addr, n & 0xFFFFFFFF)
    elif spec.kind == "bool":
        b = 1 if _parse_bool(value) else 0
        if spec.bit_width == 8:
            image[spec.addr] = b
        else:
            _patch_bitfield(image, spec.addr, b, spec.bit_offset, spec.bit_width)
    elif spec.kind == "enum":
        idx = _resolve_enum(value, list(spec.bounds))  # type: ignore[arg-type]
        if spec.bit_width == 8:
            image[spec.addr] = idx & 0xFF
        else:
            _patch_bitfield(image, spec.addr, idx, spec.bit_offset, spec.bit_width)
    elif spec.kind == "str":
        if not 0 < spec.length <= 32:
            raise ValueError(f"bad str length: {spec.length}")
        encoded = _encode_str_for(value, spec)
        image[spec.addr:spec.addr + spec.length] = encoded
    else:
        raise ValueError(f"unsupported kind: {spec.kind}")


SETTINGS_REGISTRY: dict[str, SettingSpec] = {
    s.name: s for s in [
        # --- 0xA000 block: general / audio / RF ----------------------------
        # 0xA000 packed: set_rxa_am:hi-nibble, set_rxa_fm:lo-nibble
        SettingSpec("set_rxa_am", "AM RX audio profile (SetRxA AM)",
                    addr=0xA000, kind="enum", bounds=SET_RXA_AM_MODES,
                    bit_offset=4, bit_width=4),
        SettingSpec("set_rxa_fm", "FM RX audio profile (SetRxA FM)",
                    addr=0xA000, kind="enum", bounds=SET_RXA_FM_MODES,
                    bit_offset=0, bit_width=4),
        SettingSpec("squelch", "Squelch level 0..9",
                    addr=0xA001, kind="int", bounds=(0, 9)),
        SettingSpec("max_talk_time", "Max TX timeout (TxTOut)",
                    addr=0xA002, kind="int", bounds=(0, 255)),
        SettingSpec("noaa_autoscan", "NOAA Autoscan (NOAA-S)",
                    addr=0xA003, kind="bool"),
        # 0xA004 packed: __UNUSED09:1, set_nav:1, set_key:4, set_menu_lock:1, key_lock:1
        SettingSpec("key_lock", "Keypad locked at boot",
                    addr=0xA004, kind="bool",
                    bit_offset=0, bit_width=1),
        SettingSpec("set_menu_lock", "Lock menu / RescueOps",
                    addr=0xA004, kind="bool",
                    bit_offset=1, bit_width=1),
        SettingSpec("set_key", "Custom side key (SetKEY)",
                    addr=0xA004, kind="enum", bounds=SET_KEY_MODES,
                    bit_offset=2, bit_width=4),
        SettingSpec("set_nav", "Navigation style (UV-K1 or UV-K5 V3)",
                    addr=0xA004, kind="enum", bounds=SET_NAV_MODES,
                    bit_offset=6, bit_width=1),
        SettingSpec("vox_switch", "VOX enable",
                    addr=0xA005, kind="bool"),
        SettingSpec("vox_level", "VOX sensitivity 0..9",
                    addr=0xA006, kind="int", bounds=(0, 9)),
        SettingSpec("mic_gain", "Mic gain 0..8",
                    addr=0xA007, kind="int", bounds=(0, 8)),

        # --- 0xA008 block: display / backlight / mode ----------------------
        SettingSpec("backlight_min", "Min backlight brightness 0..10",
                    addr=0xA008, kind="int", bounds=(0, 10),
                    bit_offset=0, bit_width=4),
        SettingSpec("backlight_max", "Max backlight brightness 0..10",
                    addr=0xA008, kind="int", bounds=(0, 10),
                    bit_offset=4, bit_width=4),
        SettingSpec("channel_display_mode", "Channel display style",
                    addr=0xA009, kind="enum", bounds=CHANNEL_DISPLAY_MODES),
        SettingSpec("crossband", "Crossband repeat",
                    addr=0xA00A, kind="enum", bounds=CROSSBAND_MODES),
        SettingSpec("battery_save", "Battery saver",
                    addr=0xA00B, kind="enum", bounds=BATTERY_SAVE_MODES),
        SettingSpec("dual_watch", "Dual watch",
                    addr=0xA00C, kind="enum", bounds=DUAL_WATCH_MODES),
        SettingSpec("backlight_time", "Backlight auto-off time",
                    addr=0xA00D, kind="enum", bounds=BACKLIGHT_TIMES),
        # 0xA00E packed: __UNUSED10:5, set_nfm:2, ste:1
        SettingSpec("ste", "Squelch tail elimination (STE)",
                    addr=0xA00E, kind="bool",
                    bit_offset=0, bit_width=1),
        SettingSpec("set_nfm", "NFM mode (SetNFM)",
                    addr=0xA00E, kind="enum", bounds=NFM_MODES,
                    bit_offset=1, bit_width=2),

        # --- 0xA0A8 block: keys ---------------------------------------------
        # 0xA0A8 packed: keyM_longpress_action:7, button_beep:1
        SettingSpec("button_beep", "Keypad beep (Beep)",
                    addr=0xA0A8, kind="bool",
                    bit_offset=7, bit_width=1),
        SettingSpec("keyM_longpress_action", "M long press action",
                    addr=0xA0A8, kind="enum", bounds=KEY_ACTIONS,
                    bit_offset=0, bit_width=7),
        SettingSpec("key1_shortpress_action", "Side key 1 short press",
                    addr=0xA0A9, kind="enum", bounds=KEY_ACTIONS),
        SettingSpec("key1_longpress_action", "Side key 1 long press",
                    addr=0xA0AA, kind="enum", bounds=KEY_ACTIONS),
        SettingSpec("key2_shortpress_action", "Side key 2 short press",
                    addr=0xA0AB, kind="enum", bounds=KEY_ACTIONS),
        SettingSpec("key2_longpress_action", "Side key 2 long press",
                    addr=0xA0AC, kind="enum", bounds=KEY_ACTIONS),
        SettingSpec("scan_resume_mode", "Scan resume mode (ScnRev)",
                    addr=0xA0AD, kind="enum", bounds=SCAN_RESUME_MODES),
        SettingSpec("auto_keypad_lock", "Auto keypad lock (KeyLck)",
                    addr=0xA0AE, kind="bool"),
        SettingSpec("welcome_mode", "Power-on display message (POnMsg)",
                    addr=0xA0AF, kind="enum", bounds=WELCOME_MODES),

        # --- 0xA0B8 block: voice -------------------------------------------
        SettingSpec("voice", "Voice prompts",
                    addr=0xA0B8, kind="enum", bounds=VOICE_MODES),

        # --- 0xA0C0 block: misc beeps + battery + tx vfo -------------------
        SettingSpec("alarm_mode", "Alarm mode",
                    addr=0xA0C0, kind="enum", bounds=ALARM_MODES),
        SettingSpec("roger_beep", "End-of-TX beep (Roger)",
                    addr=0xA0C1, kind="enum", bounds=ROGER_BEEP_MODES),
        SettingSpec("rp_ste", "Repeater STE delay (RP STE)",
                    addr=0xA0C2, kind="int", bounds=(0, 10)),
        SettingSpec("tx_vfo", "Main VFO for TX",
                    addr=0xA0C3, kind="enum", bounds=TX_VFO_MODES),
        SettingSpec("battery_type", "Battery type (BatTyp)",
                    addr=0xA0C4, kind="enum", bounds=BATTERY_TYPES),

        # --- 0xA0C8 / 0xA0D8 — boot logo --------------------------------
        # The EEPROM fields are 16 bytes wide but the radio's welcome
        # screen only renders the first 12 characters. We store all 16
        # bytes (so the trailing positions are always cleanly padded with
        # 0xFF and never carry over old garbage) but clamp user input to
        # 12 chars via `display_length`.
        # NB: requires welcome_mode (POnMsg) to be set to "ALL" (line 1 +
        # voltage + sound) or "MESSAGE only" (line 1 + line 2) — otherwise
        # the message is not displayed at boot at all.
        SettingSpec("logo_line1",
                    "Boot message line 1 (max 12 chars). "
                    "Visible only when POnMsg = MESSAGE or ALL.",
                    addr=0xA0C8, kind="str", length=16, display_length=12,
                    str_encoding="logo"),
        SettingSpec("logo_line2",
                    "Boot message line 2 (max 12 chars). "
                    "Visible only when POnMsg = MESSAGE.",
                    addr=0xA0D8, kind="str", length=16, display_length=12,
                    str_encoding="logo"),

        # --- 0xA150 block: TX freq lock + flags ----------------------------
        SettingSpec("int_flock", "TX frequency lock (F Lock)",
                    addr=0xA150, kind="enum", bounds=FLOCK_MODES),
        SettingSpec("int_350en", "Unlock 350-400 MHz RX (350 En)",
                    addr=0xA155, kind="bool"),
        SettingSpec("int_scren", "Spectrum mode (ScrEn)",
                    addr=0xA156, kind="bool"),
        # 0xA157 packed: backlight_on_TX_RX:2, AM_fix:1, mic_bar:1,
        #                battery_text:2, live_DTMF_decoder:1, __UNUSED:1
        SettingSpec("backlight_on_TX_RX", "Backlight on TX/RX (BLTxRx)",
                    addr=0xA157, kind="enum", bounds=BACKLIGHT_TX_RX,
                    bit_offset=6, bit_width=2),
        SettingSpec("AM_fix", "AM reception fix (AM Fix)",
                    addr=0xA157, kind="bool",
                    bit_offset=5, bit_width=1),
        SettingSpec("mic_bar", "Microphone level bar (MicBar)",
                    addr=0xA157, kind="bool",
                    bit_offset=4, bit_width=1),
        SettingSpec("battery_text", "Battery level display (BatTXT)",
                    addr=0xA157, kind="enum", bounds=BATTERY_TEXT_LIST,
                    bit_offset=2, bit_width=2),
        SettingSpec("live_DTMF_decoder", "Live DTMF decoder display",
                    addr=0xA157, kind="bool",
                    bit_offset=1, bit_width=1),

        # --- 0xA15C..0xA15F — F4HWN custom flags ---------------------------
        # 0xA15C packed: set_off_tmr:7, set_tmr:1
        SettingSpec("set_tmr", "Timer enable (SetTmr)",
                    addr=0xA15C, kind="bool",
                    bit_offset=7, bit_width=1),
        SettingSpec("set_off_tmr", "Off timer minutes (SetOff, 0=off)",
                    addr=0xA15C, kind="int", bounds=(0, 127),
                    bit_offset=0, bit_width=7),
        # 0xA15D packed: set_gui:1, set_met:1, set_lck:1, set_inv:1, set_contrast:4
        SettingSpec("set_contrast", "Contrast level (SetCtr)",
                    addr=0xA15D, kind="int", bounds=(0, 15),
                    bit_offset=0, bit_width=4),
        SettingSpec("set_inv", "Invert display (SetInv)",
                    addr=0xA15D, kind="bool",
                    bit_offset=4, bit_width=1),
        SettingSpec("set_lck", "PTT lock when keypad locked (SetLck)",
                    addr=0xA15D, kind="enum", bounds=SET_LCK_MODES,
                    bit_offset=5, bit_width=1),
        SettingSpec("set_met", "S-meter style (SetMet)",
                    addr=0xA15D, kind="enum", bounds=SET_MET_MODES,
                    bit_offset=6, bit_width=1),
        SettingSpec("set_gui", "Display text style (SetGui)",
                    addr=0xA15D, kind="bool",
                    bit_offset=7, bit_width=1),
        # 0xA15E packed: set_tot:4 hi, set_eot:4 lo
        SettingSpec("set_tot", "TX timeout indicator (SetTot)",
                    addr=0xA15E, kind="enum", bounds=SET_TOT_EOT_MODES,
                    bit_offset=4, bit_width=4),
        SettingSpec("set_eot", "End-of-TX indicator (SetEot)",
                    addr=0xA15E, kind="enum", bounds=SET_TOT_EOT_MODES,
                    bit_offset=0, bit_width=4),
        # 0xA15F packed: set_pwr:4 hi, set_ptt:4 lo
        SettingSpec("set_pwr", "Power level when USER selected (SetPwr)",
                    addr=0xA15F, kind="enum", bounds=SET_LOW_MODES,
                    bit_offset=4, bit_width=4),
        SettingSpec("set_ptt", "PTT operating mode (SetPtt)",
                    addr=0xA15F, kind="enum", bounds=SET_PTT_MODES,
                    bit_offset=0, bit_width=4),

        # --- 0xA014 / 0xA01A — VFO channel assignment ----------------------
        # ScreenChannel and FreqChannel pairs at 0xA010..0xA01F. The two
        # values that drive what each VFO is showing on boot are these:
        SettingSpec("VFO_A_chn", "VFO A active channel (1-based; 65535 = unset)",
                    addr=0xA012, kind="u16le", bounds=(0, 65535)),
        SettingSpec("VFO_B_chn", "VFO B active channel (1-based; 65535 = unset)",
                    addr=0xA018, kind="u16le", bounds=(0, 65535)),

        # --- 0xA130 sl struct: scan priority ------------------------------
        # 0xA130 packed: slPriorEnab:1, slDef:7
        SettingSpec("slPriorEnab", "Priority channel scan on List 1",
                    addr=0xA130, kind="bool",
                    bit_offset=0, bit_width=1),
        SettingSpec("slDef", "Default scan list (SList) 1..24",
                    addr=0xA130, kind="int", bounds=(1, 24),
                    bit_offset=1, bit_width=7),
        SettingSpec("slPriorCh1", "Priority channel 1 (List 1; 65535 = unset)",
                    addr=0xA131, kind="u16le", bounds=(0, 65535)),
        SettingSpec("slPriorCh2", "Priority channel 2 (List 1; 65535 = unset)",
                    addr=0xA133, kind="u16le", bounds=(0, 65535)),
        SettingSpec("call_channel", "One-key call channel (1 Call; 65535 = unset)",
                    addr=0xA135, kind="u16le", bounds=(0, 65535)),

        # --- 0xA0E8 dtmf struct (essential, non-dangerous fields) ----------
        SettingSpec("dtmf_side_tone", "DTMF side tone enable",
                    addr=0xA0E8, kind="bool"),
        SettingSpec("dtmf_decode_response", "DTMF decode response",
                    addr=0xA0EB, kind="enum",
                    bounds=["DO NOTHING", "RING", "REPLY", "BOTH"]),
        SettingSpec("dtmf_auto_reset_time", "DTMF auto reset time (5..60 sec)",
                    addr=0xA0EC, kind="int", bounds=(5, 60)),
        SettingSpec("dtmf_preload_time", "DTMF preload time (×10 ms, 30..300)",
                    addr=0xA0ED, kind="int", bounds=(30, 300)),
        SettingSpec("dtmf_first_code_persist_time",
                    "DTMF first code persist time (×10 ms, 3..100)",
                    addr=0xA0EE, kind="int", bounds=(3, 100)),
        SettingSpec("dtmf_hash_persist_time",
                    "DTMF #/* persist time (×10 ms, 3..100)",
                    addr=0xA0EF, kind="int", bounds=(3, 100)),
        SettingSpec("dtmf_code_persist_time",
                    "DTMF code persist time (×10 ms, 3..100)",
                    addr=0xA0F0, kind="int", bounds=(3, 100)),
        SettingSpec("dtmf_code_interval_time",
                    "DTMF code interval (×10 ms, 3..100)",
                    addr=0xA0F1, kind="int", bounds=(3, 100)),
        SettingSpec("dtmf_permit_remote_kill", "Permit DTMF remote kill",
                    addr=0xA0F2, kind="bool"),

        # --- 0xA152 — DTMF kill state -------------------------------------
        SettingSpec("int_KILLED", "DTMF Kill Lock (radio is currently killed)",
                    addr=0xA152, kind="bool"),

        # --- 0xA110 / 0xA120 — DTMF UP/DOWN codes (16 chars each) ---------
        # CHIRP convention: strip + pad full field with 0x00 (no 0xFF).
        SettingSpec("dtmf_up_code", "DTMF code at start of TX (UPCode)",
                    addr=0xA110, kind="str", length=16,
                    str_encoding="null"),
        SettingSpec("dtmf_down_code", "DTMF code at end of TX (DWCode)",
                    addr=0xA120, kind="str", length=16,
                    str_encoding="null"),

        # --- 0xA028 — 48 FM-broadcast presets (u16 each, MHz*100) -----------
        # Generated at module load so we don't repeat 48 SettingSpec lines.
        # See the loop right after this dict.
    ] + [
        SettingSpec(
            f"fm_preset_{i + 1:02d}",
            f"FM broadcast preset #{i + 1} (MHz, 64..108, OFF to clear)",
            addr=0xA028 + i * 2,
            kind="fm_freq",
        )
        for i in range(48)
    ]
}


def list_settings() -> list[SettingSpec]:
    """Return all registered writable settings, alphabetically by name."""
    return sorted(SETTINGS_REGISTRY.values(), key=lambda s: s.name)


def read_setting(image: bytes, name: str) -> Any:
    """
    Decode a single named setting straight from an EEPROM image, using its
    registry entry. Returns: int / bool / str / enum-label depending on kind.
    """
    spec = SETTINGS_REGISTRY.get(name)
    if spec is None:
        raise KeyError(name)
    if spec.kind == "fm_freq":
        return _decode_fm_freq(_u16le(image, spec.addr))
    if spec.kind == "str":
        return _ascii(image, spec.addr, spec.length)
    if spec.kind == "u16le":
        return _u16le(image, spec.addr)
    if spec.kind == "u32le":
        return struct.unpack_from("<I", image, spec.addr)[0]
    byte_val = image[spec.addr]
    if spec.bit_width == 8:
        raw = byte_val
    else:
        mask = (1 << spec.bit_width) - 1
        raw = (byte_val >> spec.bit_offset) & mask
    if spec.kind == "int":
        return raw
    if spec.kind == "bool":
        return bool(raw)
    if spec.kind == "enum":
        choices = list(spec.bounds)  # type: ignore[arg-type]
        return choices[raw] if 0 <= raw < len(choices) else f"?{raw}"
    raise ValueError(f"unsupported kind: {spec.kind}")
