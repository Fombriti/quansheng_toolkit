"""
Settings registry for UV-K5 V1 / UV-K1 / UV-K6 running STOCK Quansheng
firmware. Mirrors the layout used by the kk7ds CHIRP driver
(`chirp/drivers/uvk5.py`) and the legacy F4HWN v4.3 driver, both of which
keep the user-settings region at 0x0E70..0x0EFF.

This is a *separate* module from `kradio.settings` (which targets F4HWN
Fusion 5.x at 0xA000..0xA170) because the two layouts have nothing in
common — different addresses, different bit packing, even different
enumerations. Forcing them through one global SETTINGS_REGISTRY would
mean the GUI silently writes wrong bytes whenever the wrong profile is
active. Instead, each profile points at its own settings module.

Status: experimental. Field offsets verified against a real UV-K1 EEPROM
dump (firmware 7.03.01). Write paths reuse the byte-encoding helpers
from `kradio.settings` so the SettingSpec semantics match exactly.
"""
from __future__ import annotations

import struct
from typing import Any

from .settings import (
    SettingSpec,
    _ascii,
    _decode_fm_freq,
    _encode_fm_freq,
    _encode_str_for,
    _parse_bool,
    _patch_bitfield,
    _resolve_enum,
    _u16le,
)


# --- Enumerations (stock/kk7ds names) -------------------------------------

# Stock firmware exposes 3 channel-display modes; F4HWN added "Name + Freq".
CHANNEL_DISPLAY_MODES = ["Frequency", "Channel No", "Channel Name"]
CROSSBAND_MODES = ["OFF", "Band A", "Band B"]
DUAL_WATCH_MODES = ["OFF", "Band A", "Band B"]
BATTERY_SAVE_MODES = ["OFF", "1:1", "1:2", "1:3", "1:4"]
# Stock backlight time list (8 entries — kk7ds CHIRP).
BACKLIGHT_TIMES = [
    "OFF", "5 sec", "10 sec", "20 sec",
    "1 min", "2 min", "4 min", "Always On",
]
BATTERY_TYPES = ["1600 mAh", "2200 mAh", "3500 mAh"]
ROGER_BEEP_MODES = ["OFF", "Roger", "MDC"]
SCAN_RESUME_MODES = ["TIME", "CARRIER", "STOP"]
TX_VFO_MODES = ["A", "B"]
NFM_MODES = ["NARROW", "NARROWER"]
VOICE_MODES = ["OFF", "Chinese", "English"]
ALARM_MODES = ["SITE", "TONE"]
SET_OFF_ON = ["OFF", "ON"]
PWRONDISP_LIST = ["Full Screen", "Welcome Message", "Voltage", "None"]

# Stock side-key actions (subset of F4HWN's list — newer F4HWN-specific
# entries are at higher indices and shouldn't be set on stock firmware).
KEY_ACTIONS_STOCK = [
    "NONE", "FLASHLIGHT", "POWER", "MONITOR", "SCAN", "VOX", "ALARM",
    "FM RADIO", "1750Hz", "LOCK KEYPAD", "VFO A / VFO B", "VFO / MEM",
]


# --- Writable registry -----------------------------------------------------
# Address comments use the canonical 0xExx hex for stock K5 V1.

SETTINGS_REGISTRY: dict[str, SettingSpec] = {
    s.name: s for s in [
        # --- 0x0E70 block: general / audio / RF ----------------------------
        SettingSpec("call_channel", "1-CALL channel index (0-based)",
                    addr=0x0E70, kind="int", bounds=(0, 199)),
        SettingSpec("squelch", "Squelch level 0..9",
                    addr=0x0E71, kind="int", bounds=(0, 9)),
        SettingSpec("max_talk_time", "Max TX timeout (TxTOut)",
                    addr=0x0E72, kind="int", bounds=(0, 255)),
        SettingSpec("noaa_autoscan", "NOAA Autoscan",
                    addr=0x0E73, kind="bool"),
        # 0x0E74 packed: key_lock:1 (bit 0), set_menu_lock:1 (bit 1)
        SettingSpec("key_lock", "Keypad locked at boot",
                    addr=0x0E74, kind="bool",
                    bit_offset=0, bit_width=1),
        SettingSpec("vox_switch", "VOX enable",
                    addr=0x0E75, kind="bool"),
        SettingSpec("vox_level", "VOX sensitivity 0..9",
                    addr=0x0E76, kind="int", bounds=(0, 9)),
        SettingSpec("mic_gain", "Mic gain 0..4",
                    addr=0x0E77, kind="int", bounds=(0, 4)),

        # --- 0x0E78: backlight + display -----------------------------------
        SettingSpec("backlight_min", "Min backlight brightness 0..15",
                    addr=0x0E78, kind="int", bounds=(0, 15),
                    bit_offset=0, bit_width=4),
        SettingSpec("backlight_max", "Max backlight brightness 0..15",
                    addr=0x0E78, kind="int", bounds=(0, 15),
                    bit_offset=4, bit_width=4),
        SettingSpec("channel_display_mode", "Channel display style",
                    addr=0x0E79, kind="enum", bounds=CHANNEL_DISPLAY_MODES),
        SettingSpec("crossband", "Crossband repeat",
                    addr=0x0E7A, kind="enum", bounds=CROSSBAND_MODES),
        SettingSpec("battery_save", "Battery saver",
                    addr=0x0E7B, kind="enum", bounds=BATTERY_SAVE_MODES),
        SettingSpec("dual_watch", "Dual watch",
                    addr=0x0E7C, kind="enum", bounds=DUAL_WATCH_MODES),
        SettingSpec("backlight_time", "Backlight auto-off time",
                    addr=0x0E7D, kind="enum", bounds=BACKLIGHT_TIMES),
        # 0x0E7E packed: ste:1 (bit 0), set_nfm:2 (bits 1..2)
        SettingSpec("ste", "Squelch tail elimination (STE)",
                    addr=0x0E7E, kind="bool",
                    bit_offset=0, bit_width=1),

        # --- 0x0E90 block: keys --------------------------------------------
        # CHIRP MSB-first packing (`u8 keyM:7, beep:1`):
        #   - keyM_longpress_action occupies bits 7..1 (high 7)
        #   - button_beep occupies bit 0
        # Verified against a real K1 dump where 0x0E90 = 0x01 (beep=ON,
        # keyM=NONE) and the radio menu shows Beep enabled.
        SettingSpec("button_beep", "Keypad beep",
                    addr=0x0E90, kind="bool",
                    bit_offset=0, bit_width=1),
        SettingSpec("keyM_longpress_action", "M long press action",
                    addr=0x0E90, kind="enum", bounds=KEY_ACTIONS_STOCK,
                    bit_offset=1, bit_width=7),
        SettingSpec("key1_shortpress_action", "Side key 1 short press",
                    addr=0x0E91, kind="enum", bounds=KEY_ACTIONS_STOCK),
        SettingSpec("key1_longpress_action", "Side key 1 long press",
                    addr=0x0E92, kind="enum", bounds=KEY_ACTIONS_STOCK),
        SettingSpec("key2_shortpress_action", "Side key 2 short press",
                    addr=0x0E93, kind="enum", bounds=KEY_ACTIONS_STOCK),
        SettingSpec("key2_longpress_action", "Side key 2 long press",
                    addr=0x0E94, kind="enum", bounds=KEY_ACTIONS_STOCK),
        SettingSpec("scan_resume_mode", "Scan resume behaviour",
                    addr=0x0E95, kind="enum", bounds=SCAN_RESUME_MODES),
        SettingSpec("auto_keypad_lock", "Auto keypad lock",
                    addr=0x0E96, kind="bool"),
        SettingSpec("power_on_dispmode", "Splash screen at boot",
                    addr=0x0E97, kind="enum", bounds=PWRONDISP_LIST),

        # --- 0x0EA0 block: voice -------------------------------------------
        SettingSpec("voice", "Voice prompt language",
                    addr=0x0EA0, kind="enum", bounds=VOICE_MODES),
        # NOTE: stock K5 V1 keeps S-meter calibration (s0_level / s9_level)
        # inside the calibration region at 0x1D00+, NOT in user settings
        # — they're intentionally not exposed here.

        # --- 0x0EA8 block: alarms / roger / battery / TX_VFO ---------------
        SettingSpec("alarm_mode", "Alarm mode",
                    addr=0x0EA8, kind="enum", bounds=ALARM_MODES),
        SettingSpec("roger_beep", "Roger beep style",
                    addr=0x0EA9, kind="enum", bounds=ROGER_BEEP_MODES),
        SettingSpec("rp_ste", "Repeater STE delay 0..10",
                    addr=0x0EAA, kind="int", bounds=(0, 10)),
        SettingSpec("tx_vfo", "Default TX VFO",
                    addr=0x0EAB, kind="enum", bounds=TX_VFO_MODES),
        SettingSpec("battery_type", "Battery model",
                    addr=0x0EAC, kind="enum", bounds=BATTERY_TYPES),

        # --- 0x0EB0 block: 2-line boot logo --------------------------------
        # Stock firmware uses the SAME "logo" encoding as F4HWN: visible
        # region 0x00-padded, then 0x00 + 0xFF*(N-1) trail. This avoids the
        # spaces-on-LCD bug we hit on F4HWN.
        SettingSpec("logo_line1", "Boot logo line 1 (12 visible chars)",
                    addr=0x0EB0, kind="str", length=16, display_length=12,
                    str_encoding="logo"),
        SettingSpec("logo_line2", "Boot logo line 2 (12 visible chars)",
                    addr=0x0EC0, kind="str", length=16, display_length=12,
                    str_encoding="logo"),

        # --- 0x0E98: power-on password (4-byte u32 LE) ---------------------
        # The radio compares the entered code against this u32 at boot when
        # `pwron_password` is enabled in build options. 0xFFFFFFFF means
        # "unset". Treat as a u32 0..99999999 (8-digit numeric code).
        SettingSpec("pwron_password",
                    "Power-on password (8-digit numeric, 0 to clear)",
                    addr=0x0E98, kind="u32le", bounds=(0, 99_999_999)),

        # --- 0x0ED0 — DTMF struct (subset of safe-to-edit fields) ----------
        SettingSpec("dtmf_side_tone", "DTMF side tone enable",
                    addr=0x0ED0, kind="bool"),
        SettingSpec("dtmf_decode_response", "DTMF decode response",
                    addr=0x0ED3, kind="enum",
                    bounds=["DO NOTHING", "RING", "REPLY", "BOTH"]),
        SettingSpec("dtmf_auto_reset_time", "DTMF auto reset time (5..60 sec)",
                    addr=0x0ED4, kind="int", bounds=(5, 60)),
        SettingSpec("dtmf_preload_time", "DTMF preload time (×10 ms, 30..300)",
                    addr=0x0ED5, kind="int", bounds=(30, 300)),
        SettingSpec("dtmf_first_code_persist_time",
                    "DTMF first code persist time (×10 ms, 3..100)",
                    addr=0x0ED6, kind="int", bounds=(3, 100)),
        SettingSpec("dtmf_hash_persist_time",
                    "DTMF #/* persist time (×10 ms, 3..100)",
                    addr=0x0ED7, kind="int", bounds=(3, 100)),
        SettingSpec("dtmf_code_persist_time",
                    "DTMF code persist time (×10 ms, 3..100)",
                    addr=0x0ED8, kind="int", bounds=(3, 100)),
        SettingSpec("dtmf_code_interval_time",
                    "DTMF code interval (×10 ms, 3..100)",
                    addr=0x0ED9, kind="int", bounds=(3, 100)),
        SettingSpec("dtmf_permit_remote_kill", "Permit DTMF remote kill",
                    addr=0x0EDA, kind="bool"),

        # --- 0x0EF8 / 0x0F08 — DTMF UP/DOWN codes (16 chars each) ----------
        # Same null-padding encoding as F4HWN.
        SettingSpec("dtmf_up_code", "DTMF code at start of TX (UPCode)",
                    addr=0x0EF8, kind="str", length=16,
                    str_encoding="null"),
        SettingSpec("dtmf_down_code", "DTMF code at end of TX (DWCode)",
                    addr=0x0F08, kind="str", length=16,
                    str_encoding="null"),

        # --- 0x0F40: TX freq lock ------------------------------------------
        SettingSpec("int_flock", "TX frequency lock",
                    addr=0x0F40, kind="int", bounds=(0, 10)),

        # --- 0x0E40 — 20 FM-broadcast presets (u16 each, MHz*100) ----------
    ] + [
        SettingSpec(
            f"fm_preset_{i + 1:02d}",
            f"FM broadcast preset #{i + 1} (MHz, 64..108, OFF to clear)",
            addr=0x0E40 + i * 2,
            kind="fm_freq",
        )
        for i in range(20)
    ]
}


def list_settings() -> list[SettingSpec]:
    return sorted(SETTINGS_REGISTRY.values(), key=lambda s: s.name)


def read_setting(image: bytes, name: str) -> Any:
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


def apply_setting(image: bytearray, name: str, value: str) -> None:
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
