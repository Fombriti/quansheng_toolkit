"""
Memory map for the original Quansheng UV-K5 / UV-K5(8) / K6 / K5 V3 / UV-K1
running STOCK Quansheng firmware (or the early IJV / 1o11 variants).

Adapted from the kk7ds CHIRP driver `chirp/drivers/uvk5.py` (GPL-3) which
is the community reference for this layout. The serial protocol is
identical to F4HWN; only the EEPROM map differs:

    0x0000  channel[214]       16 bytes/record   (200 MR + 14 VFO)
    0x0d60  ch_attr[200]       1  byte/record    (compander+band+scanlist)
    0x0e40  fm_freq[20]        2  bytes/preset   (FM radio presets)
    0x0e70  settings           squelch, vox, …
    0x0eee  settings_ext       (variant-dependent)
    0x0ed0  dtmf
    0x0f18  scan_lists[2]
    0x0f30  reserved
    0x0f40  flock + tx range flags
    0x0f50  channelname[200]   16 bytes/name
    0x1d00  calibration        DO NOT WRITE without explicit calibration mode
    0x2000  end of EEPROM (8 KB)

Status: EXPERIMENTAL. Read paths are believed correct (matches kk7ds).
Write paths are NOT yet exposed via the GUI; the active profile is
`verified=False`, which gates every upload.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass


# --- Channel records -------------------------------------------------------

CHANNELS_BASE = 0x0000
CHANNEL_SIZE = 0x10
NUM_CHANNELS = 200          # MR memory channels (slots 1..200)
NUM_VFO_CHANNELS = 14       # 201..214 — VFO memory
NUM_TOTAL_RECORDS = 214

# Channel name region.
CHANNEL_NAMES_BASE = 0x0F50
CHANNEL_NAME_SIZE = 0x10
CHANNEL_NAME_MAX = 10

# Per-channel attributes: 1 byte at 0xD60 + index. Layout:
#   bit 7..4 : scanlist + flags  (scanlist1, scanlist2, compander, band)
#   per kk7ds: u8 is_scanlist1:1, is_scanlist2:1, compander:2, is_free:1, band:3
CH_ATTR_BASE = 0x0D60
CH_ATTR_SIZE = 0x01
NUM_CH_ATTR = 200

# FM broadcast presets.
FM_BASE = 0x0E40
FM_COUNT = 20

# Scan list defaults.
SCAN_LISTS_BASE = 0x0F18

# General settings region.
SETTINGS_BASE = 0x0E70

# Calibration region — DO NOT WRITE.
CAL_START = 0x1D00
MEM_SIZE = 0x2000
PROG_SIZE = 0x1D00


# --- Scan list encoding ---------------------------------------------------
# Stock firmware uses TWO scan-list flags per channel:
#   is_scanlist1 (bit 7) and is_scanlist2 (bit 6).
# Combinations: OFF / SL1 / SL2 / SL1+SL2 (== "ALL" semantics).

SCAN_OFF = 0
SCAN_SL1 = 1
SCAN_SL2 = 2
SCAN_BOTH = 3

SCAN_LIST_LABELS = ["OFF", "SL1", "SL2", "SL1+SL2"]


def scanlist_label(value: int) -> str:
    if 0 <= value <= 3:
        return SCAN_LIST_LABELS[value]
    if value == 0xFF:
        return "uninit"
    return f"?{value:02x}"


# --- Channel decoder -----------------------------------------------------

MODE_TABLE = ["FM", "NFM", "AM"]   # original firmware: 3 modes

# Stock K5 V1 / K1 only exposes 2 power levels in the channel byte
# (LOW / HIGH) but the encoding uses the same byte 0x0C bits 2..4 as
# F4HWN. We ship the full 8-entry table so the GUI's combobox shows
# the same labels — radios with stricter firmware will simply ignore
# values outside their supported range.
POWER_LEVELS = ["USER", "LOW1", "LOW2", "LOW3", "LOW4", "LOW5", "MID", "HIGH"]
STEPS_KHZ = [2.5, 5.0, 6.25, 10.0, 12.5, 25.0, 8.33, 50.0]
DUPLEX_LABELS = ["", "+", "-"]


@dataclass
class Channel:
    index: int
    name: str
    freq_hz: int
    mode: str
    scanlist: int        # 0..3 derived from is_scanlist1/2 bits
    band: int
    raw_record: bytes
    raw_attr: bytes
    # Tones — same byte layout as F4HWN (rxcode, txcode, codeflags at
    # offsets 0x08..0x0A inside the channel record).
    rx_tmode: int = 0
    rx_tone_label: str = ""
    tx_tmode: int = 0
    tx_tone_label: str = ""
    # Same channel-record extra fields as F4HWN — bytes 0x0B / 0x0C / 0x0E
    # have identical packing on stock K5 V1 / K1.
    duplex: str = ""
    offset_hz: int = 0
    tx_power: int = 0
    step_idx: int = 0
    busy_lockout: bool = False
    tx_lock: bool = False
    freq_reverse: bool = False

    @property
    def is_empty(self) -> bool:
        return self.freq_hz == 0

    @property
    def freq_mhz(self) -> float:
        return self.freq_hz / 1_000_000.0

    @property
    def scanlist_label(self) -> str:
        return scanlist_label(self.scanlist)


def _decode_name(raw: bytes) -> str:
    name: list[str] = []
    for b in raw[:CHANNEL_NAME_MAX]:
        if b == 0xFF or b == 0x00:
            break
        if 0x20 <= b <= 0x7E:
            name.append(chr(b))
        else:
            break
    return "".join(name).rstrip()


def _decode_record(idx: int, rec: bytes, name_raw: bytes, attr: bytes) -> Channel:
    from . import tones

    if len(rec) != CHANNEL_SIZE:
        raise ValueError(f"channel record len={len(rec)} (expected {CHANNEL_SIZE})")
    if len(attr) != CH_ATTR_SIZE:
        raise ValueError(f"ch_attr len={len(attr)} (expected {CH_ATTR_SIZE})")

    freq = struct.unpack("<I", rec[0:4])[0]
    if freq == 0xFFFFFFFF:
        freq = 0
    freq_hz = freq * 10

    # Bytes 4..7: |offset|/10 (LE u32). Sign comes from byte 0x0B low nibble.
    offset_raw = struct.unpack("<I", rec[4:8])[0]
    offset_hz = (offset_raw if offset_raw != 0xFFFFFFFF else 0) * 10

    # Modulation in stock firmware (different layout from F4HWN).
    # rec[0x0B] holds modulation in bits 4..7, offset_dir in 0..3.
    modulation = (rec[0x0B] >> 4) & 0x0F
    offset_dir = rec[0x0B] & 0x0F
    duplex = DUPLEX_LABELS[offset_dir] if offset_dir < len(DUPLEX_LABELS) else ""
    if duplex == "":
        offset_hz = 0

    # byte 0x0C packs flags + power + bandwidth + reverse (same as F4HWN).
    byte_0c = rec[0x0C]
    bandwidth = (byte_0c >> 1) & 0x01
    tx_power = (byte_0c >> 2) & 0x07
    busy_lockout = bool(byte_0c & 0x20)
    tx_lock = bool(byte_0c & 0x40)
    freq_reverse = bool(byte_0c & 0x01)

    # Map combined index → label. Stock has a smaller mode table than F4HWN.
    mode_idx = modulation * 2 + bandwidth
    mode = MODE_TABLE[mode_idx] if 0 <= mode_idx < len(MODE_TABLE) else "?"

    step_idx = rec[0x0E] if rec[0x0E] < len(STEPS_KHZ) else 0

    # Tones (same byte layout as F4HWN: rxcode @0x08, txcode @0x09,
    # txcodeflag:4 / rxcodeflag:4 packed at @0x0A).
    rx_code = rec[0x08]
    tx_code = rec[0x09]
    flags = rec[0x0A]
    tx_flag = (flags >> 4) & 0x0F
    rx_flag = flags & 0x0F
    rx_tmode, rx_label = tones.decode_tone(rx_code, rx_flag)
    tx_tmode, tx_label = tones.decode_tone(tx_code, tx_flag)

    # Attribute byte: bit7 is_scanlist1, bit6 is_scanlist2,
    #                 bit5..4 compander, bit3 is_free, bits 2..0 band.
    sl1 = (attr[0] >> 7) & 0x01
    sl2 = (attr[0] >> 6) & 0x01
    sl = (sl1 << 0) | (sl2 << 1)   # 0..3 packed
    band = attr[0] & 0x07

    name = _decode_name(name_raw) if freq_hz else ""

    return Channel(
        index=idx,
        name=name,
        freq_hz=freq_hz,
        mode=mode,
        scanlist=sl,
        band=band,
        raw_record=bytes(rec),
        raw_attr=bytes(attr),
        rx_tmode=rx_tmode,
        rx_tone_label=rx_label,
        tx_tmode=tx_tmode,
        tx_tone_label=tx_label,
        duplex=duplex,
        offset_hz=offset_hz,
        tx_power=tx_power,
        step_idx=step_idx,
        busy_lockout=busy_lockout,
        tx_lock=tx_lock,
        freq_reverse=freq_reverse,
    )


def decode_all_channels(eeprom: bytes) -> list[Channel]:
    out: list[Channel] = []
    for i in range(NUM_CHANNELS):
        rec = eeprom[CHANNELS_BASE + i * CHANNEL_SIZE:
                     CHANNELS_BASE + (i + 1) * CHANNEL_SIZE]
        name_raw = eeprom[CHANNEL_NAMES_BASE + i * CHANNEL_NAME_SIZE:
                          CHANNEL_NAMES_BASE + (i + 1) * CHANNEL_NAME_SIZE]
        attr = eeprom[CH_ATTR_BASE + i * CH_ATTR_SIZE:
                      CH_ATTR_BASE + (i + 1) * CH_ATTR_SIZE]
        out.append(_decode_record(i, rec, name_raw, attr))
    return out


# --- Address helpers -----------------------------------------------------

def addr_channel(idx: int) -> int:
    return CHANNELS_BASE + idx * CHANNEL_SIZE


def addr_channel_name(idx: int) -> int:
    return CHANNEL_NAMES_BASE + idx * CHANNEL_NAME_SIZE


def addr_ch_attr(idx: int) -> int:
    return CH_ATTR_BASE + idx * CH_ATTR_SIZE


def addr_scanlist_byte(idx: int) -> int:
    """The "scan-list flags" live in the same byte as compander+band."""
    return CH_ATTR_BASE + idx * CH_ATTR_SIZE


def patch_scanlist(attr_byte: int, sl_value: int) -> int:
    """Replace the scanlist1/scanlist2 flags inside an existing attr byte."""
    if not 0 <= sl_value <= 3:
        raise ValueError(f"sl_value must be 0..3, got {sl_value}")
    sl1 = sl_value & 0x01
    sl2 = (sl_value >> 1) & 0x01
    return (attr_byte & 0x3F) | (sl1 << 7) | (sl2 << 6)


def parse_scanlist_spec(spec: str) -> int:
    """
    Convert a user-supplied scan-list string into the K5 V1 attr value
    (0..3). Accepts OFF / ALL / NONE / '-' / numeric 0..3 / SL1 / SL2 /
    SL1+SL2 / list-style "1+2".

    F4HWN supports up to 24 lists so its `parse_scanlist_spec` accepts
    L1..L24; K5 V1 only has SL1, SL2 and SL1+SL2, so anything beyond list
    2 is collapsed (any list > 1 is treated as "secondary" → SL2 unless
    paired with 1 → SL1+SL2).
    """
    if spec is None:
        return SCAN_OFF
    s = str(spec).strip().upper().replace(" ", "")
    if s in ("", "OFF", "NONE", "-", "0"):
        return SCAN_OFF
    if s in ("ALL", "BOTH", "SL1+SL2", "L1+L2", "1+2", "3"):
        return SCAN_BOTH
    # "1+2" already handled above; handle other plus-joined forms by
    # collapsing list IDs to SL1/SL2 buckets (1 → SL1, ≥2 → SL2).
    if "+" in s:
        parts = [_strip_list_prefix(p) for p in s.split("+") if p]
        has_sl1 = any(p == "1" for p in parts)
        has_sl2 = any(p.isdigit() and int(p) >= 2 for p in parts)
        if has_sl1 and has_sl2:
            return SCAN_BOTH
        if has_sl1:
            return SCAN_SL1
        if has_sl2:
            return SCAN_SL2
        return SCAN_OFF
    digits = _strip_list_prefix(s)
    if digits.isdigit():
        n = int(digits)
        if n == 0:
            return SCAN_OFF
        if n == 1:
            return SCAN_SL1
        # Anything 2..24 collapses into SL2 — caller chose to use a
        # multi-list CSV on a 2-list radio; keep them in the secondary
        # list rather than dropping the assignment silently.
        return SCAN_SL2
    raise ValueError(f"unrecognized scanlist spec: {spec!r}")


def _strip_list_prefix(s: str) -> str:
    """`SL1` / `L24` → `1` / `24`. Used to normalise list IDs."""
    s = s.strip().upper()
    if s.startswith("SL"):
        return s[2:]
    if s.startswith("L"):
        return s[1:]
    return s


# --- Frequency band detection (mirrors F4HWN BANDS_WIDE) ------------------

BANDS_MHZ = [
    (0,    18.0,    108.0),
    (1,    108.0,   136.9999),
    (2,    137.0,   173.9999),
    (3,    174.0,   349.9999),
    (4,    350.0,   399.9999),
    (5,    400.0,   469.9999),
    (6,    470.0,   1300.0),
]


def freq_to_band(freq_hz: int) -> int:
    mhz = freq_hz / 1_000_000.0
    for code, lo, hi in BANDS_MHZ:
        if lo <= mhz <= hi:
            return code
    return 6


# --- Channel encoder (write side) ----------------------------------------

# Modulation byte layout in the K5 V1 channel record matches F4HWN's:
#   byte 0x0B: high nibble = modulation, low nibble = offsetDir
#   byte 0x0C: bit 1 = bandwidth, bits 4..2 = txpower, etc.
# But the encoder MUST stay separate so future profile divergence is easy.

def encode_channel_record(
    *,
    freq_hz: int,
    mode: str,
    offset_hz: int = 0,
    duplex: str = "",
    tx_power: int = 2,
    tuning_step_idx: int = 0,
    busy_ch_lockout: bool = False,
    tx_lock: bool = False,
    freq_reverse: bool = False,
    keep_record: bytes | None = None,
) -> bytes:
    """Produce a 16-byte channel record for the K5 V1 stock layout."""
    if mode not in MODE_TABLE:
        raise ValueError(f"unknown mode {mode!r}; expected one of {MODE_TABLE}")
    mode_idx = MODE_TABLE.index(mode)
    modulation = mode_idx // 2
    bandwidth = mode_idx & 0x01

    if duplex == "+":
        offset_dir = 1
    elif duplex == "-":
        offset_dir = 2
    else:
        offset_dir = 0

    rec = bytearray(keep_record if keep_record else b"\x00" * CHANNEL_SIZE)
    if len(rec) != CHANNEL_SIZE:
        raise ValueError(f"keep_record length must be {CHANNEL_SIZE}")

    struct.pack_into("<I", rec, 0, freq_hz // 10)
    struct.pack_into("<I", rec, 4, abs(offset_hz) // 10)
    rec[0x0B] = ((modulation & 0x0F) << 4) | (offset_dir & 0x0F)
    pwr = max(0, min(7, tx_power))
    rec[0x0C] = (
        (1 if tx_lock else 0) << 6
        | (1 if busy_ch_lockout else 0) << 5
        | (pwr & 0x07) << 2
        | (bandwidth & 0x01) << 1
        | (1 if freq_reverse else 0)
    )
    rec[0x0E] = max(0, min(255, tuning_step_idx))
    return bytes(rec)


def encode_name(name: str) -> bytes:
    """
    Encode a channel name into 16 bytes. Stock firmware uses left-justify
    + 0x00 NULL fill (no 0xFF padding inside the visible region) — same
    pattern CHIRP applies for stock K5.
    """
    s = name.strip()[:CHANNEL_NAME_MAX]
    return s.encode("ascii", errors="replace").ljust(CHANNEL_NAME_SIZE, b"\x00")


def patch_channel_tones(image: bytearray, idx: int, *,
                        rx_tone: str | None = None,
                        tx_tone: str | None = None) -> None:
    """
    Update the CTCSS / DTCS tone fields of channel `idx` (K5 V1 layout —
    same byte offsets as F4HWN). None leaves a direction unchanged.
    """
    from . import tones as _tones
    if not 0 <= idx < NUM_CHANNELS:
        raise ValueError(f"channel index out of range: {idx}")
    rec_addr = addr_channel(idx)
    if rx_tone is not None:
        rx_code, rx_flag = _tones.encode_tone(rx_tone)
        image[rec_addr + 0x08] = rx_code & 0xFF
        cur = image[rec_addr + 0x0A]
        image[rec_addr + 0x0A] = (cur & 0xF0) | (rx_flag & 0x0F)
    if tx_tone is not None:
        tx_code, tx_flag = _tones.encode_tone(tx_tone)
        image[rec_addr + 0x09] = tx_code & 0xFF
        cur = image[rec_addr + 0x0A]
        image[rec_addr + 0x0A] = (cur & 0x0F) | ((tx_flag & 0x0F) << 4)


def patch_channel_in_image(
    image: bytearray,
    *,
    idx: int,
    name: str | None = None,
    freq_hz: int | None = None,
    mode: str | None = None,
    scanlist: int | None = None,    # 0=OFF, 1=SL1, 2=SL2, 3=SL1+SL2
    band: int | None = None,
    rx_tone: str | None = None,
    tx_tone: str | None = None,
    keep_existing_codes: bool = True,
    **encode_kwargs,
) -> None:
    """
    Patch a single channel slot in a K5 V1 EEPROM image. Any field left
    as None is preserved.
    """
    if not 0 <= idx < NUM_CHANNELS:
        raise ValueError(f"channel index out of range: {idx}")

    rec_addr = addr_channel(idx)
    cur_rec = bytes(image[rec_addr:rec_addr + CHANNEL_SIZE])

    if freq_hz is not None or mode is not None:
        new_rec = encode_channel_record(
            freq_hz=freq_hz if freq_hz is not None else
                struct.unpack("<I", cur_rec[0:4])[0] * 10,
            mode=mode if mode is not None else MODE_TABLE[
                ((cur_rec[0x0B] >> 4) & 0x0F) * 2
                + ((cur_rec[0x0C] >> 1) & 0x01)
            ],
            keep_record=cur_rec if keep_existing_codes else None,
            **encode_kwargs,
        )
        image[rec_addr:rec_addr + CHANNEL_SIZE] = new_rec

    if rx_tone is not None or tx_tone is not None:
        patch_channel_tones(image, idx, rx_tone=rx_tone, tx_tone=tx_tone)

    if name is not None:
        nm_addr = addr_channel_name(idx)
        image[nm_addr:nm_addr + CHANNEL_NAME_SIZE] = encode_name(name)

    # ch_attr: byte = [is_scanlist1:1, is_scanlist2:1, compander:2,
    #                  is_free:1, band:3]
    #
    # CRITICAL: when the prior byte is 0xFF (uninitialised flash) the
    # compander bits inherit '11' (= RX+TX compression on a fresh
    # channel) — this is the same OR-instead-of-overwrite pattern that
    # caused the F4HWN K1 V/M bug fixed in v0.2.0. The K5 V1 stock
    # 'exclude'-equivalent (is_free, bit 3) IS already cleared
    # explicitly, so visibility isn't broken — but compander left
    # dirty degrades audio. When we have a band (i.e. caller
    # supplied freq/mode), clear compander too. The scanlist-only
    # patch path goes through `patch_scanlist_byte` and intentionally
    # preserves the other bits (see test_k5v1_preserves_other_bits).
    attr_addr = addr_ch_attr(idx)
    cur_attr = image[attr_addr]
    if band is None and freq_hz is not None:
        band = freq_to_band(freq_hz)
    if band is not None:
        # Full channel write — start from a clean state.
        cur_attr = (
            (band & 0x07)               # bits 0-2 band
            # bit 3 is_free → 0 (slot now used)
            # bits 4-5 compander → 0 (OFF)
            # bits 6-7 scanlist → set below if scanlist supplied,
            #                     else default to OFF
            | (patch_scanlist(0, scanlist) & 0xC0
               if scanlist is not None else 0)
        )
        scanlist = None  # already applied
    if scanlist is not None:
        cur_attr = patch_scanlist(cur_attr, scanlist)
    image[attr_addr] = cur_attr


def clear_channel_in_image(image: bytearray, idx: int) -> None:
    """Mark a channel slot as empty (record + name + attr all 0xFF)."""
    if not 0 <= idx < NUM_CHANNELS:
        raise ValueError(f"channel index out of range: {idx}")
    rec_addr = addr_channel(idx)
    image[rec_addr:rec_addr + CHANNEL_SIZE] = b"\xFF" * CHANNEL_SIZE
    nm_addr = addr_channel_name(idx)
    image[nm_addr:nm_addr + CHANNEL_NAME_SIZE] = b"\xFF" * CHANNEL_NAME_SIZE
    image[addr_ch_attr(idx)] = 0xFF
