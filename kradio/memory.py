"""
EEPROM layout, channel records and scan-list encoding for radios running
F4HWN Fusion 5.x (UV-K1 and UV-K5 V3 share the same memory map).

Layout (extracted from f4hwn.fusion.chirp.v5.4.0.py):

    0x0000  channel[1024]      16 bytes/record  (channel records)
    0x4000  channelname[1024]  16 bytes/name    (ASCII, 0xFF/0x00 terminator)
    0x8000  ch_attr[1031]      2  bytes/record  (compander+band, scanlist)
    0x880E  listname[24]       4  bytes/name    (scan list display names)
    0x9000  vfo_channel[14]    16 bytes/record  (the live VFOs)
    0xA000  settings           configuration (squelch, vox, keys, dtmf, ...)
    0xB000  calibration        DO NOT WRITE without explicit calibration mode
    0xB190  end of EEPROM
"""
from __future__ import annotations

import struct
from dataclasses import dataclass


# --- Channel records -------------------------------------------------------

CHANNELS_BASE = 0x0000
CHANNEL_SIZE = 0x10
NUM_CHANNELS = 1024

CHANNEL_NAMES_BASE = 0x4000
CHANNEL_NAME_SIZE = 0x10
CHANNEL_NAME_MAX = 10  # CHIRP exposes 10 visible characters

# --- Channel attributes (the per-channel scanlist + band byte pair) -------

CH_ATTR_BASE = 0x8000
CH_ATTR_SIZE = 0x02
NUM_CH_ATTR = 1031  # 1024 channels + 7 entries used for VFO pairs

# --- Scan list display names ----------------------------------------------

LISTNAME_BASE = 0x880E
LISTNAME_SIZE = 0x04
NUM_LISTS = 24

# --- VFO channels ---------------------------------------------------------

VFO_BASE = 0x9000
VFO_COUNT = 14

# --- Settings region -----------------------------------------------------
# Detailed offsets live in `settings.py`; the only thing we need here is the
# upper bound so the writer knows it must not touch calibration.

SETTINGS_BASE = 0xA000
SETTINGS_END = 0xA170      # last settings byte (version[16] ends here)

# --- Scan list encoding ---------------------------------------------------
# The scanlist byte uses 5 bits of meaning:
#   0       = OFF (no list)
#   1..24   = scan list 1..24
#   25      = ALL (always scanned)

SCAN_OFF = 0
SCAN_ALL = 25
SCAN_LIST_MIN = 1
SCAN_LIST_MAX = 24

SCAN_LIST_LABELS = ["OFF"] + [f"L{i}" for i in range(1, 25)] + ["ALL"]


def scanlist_label(value: int) -> str:
    """Human-readable name for a raw scanlist byte."""
    if 0 <= value <= 25:
        return SCAN_LIST_LABELS[value]
    if value == 0xFF:
        return "uninit"
    return f"?{value:02x}"


def parse_scanlist_spec(spec: str) -> int:
    """
    Convert a user-supplied string into the raw scanlist byte.
    Accepts: OFF, ALL, NONE, '-', numbers 1..24, L1..L24, 'list 12'.
    """
    if spec is None:
        raise ValueError("empty scanlist spec")
    s = str(spec).strip().upper()
    if s in ("", "OFF", "NONE", "-"):
        return SCAN_OFF
    if s == "ALL":
        return SCAN_ALL
    if s.startswith("LIST"):
        s = s.removeprefix("LIST").strip()
    elif s.startswith("L") and len(s) > 1 and s[1:].isdigit():
        s = s[1:]
    if s.isdigit():
        n = int(s)
        if SCAN_LIST_MIN <= n <= SCAN_LIST_MAX:
            return n
    raise ValueError(
        f"invalid scanlist value: {spec!r} (use OFF, ALL or 1..24)"
    )


# --- Channel record decoder ----------------------------------------------

# `valid_modes` ordering from the CHIRP driver:
#   ["FM", "NFM", "AM", "NAM", "USB"]
# Encoding: index = modulation*2 + bandwidth
MODE_TABLE = ["FM", "NFM", "AM", "NAM", "USB"]

# Per-channel TX power levels (byte 0x0C bits 2..4, 0..7).
# Labels are CHIRP-style and match the F4HWN settings registry.
POWER_LEVELS = ["USER", "LOW1", "LOW2", "LOW3", "LOW4", "LOW5", "MID", "HIGH"]

# Per-channel tuning step in kHz (byte 0x0E indexes this table).
# Order matches kk7ds CHIRP `STEP_LIST` for the K5 family.
STEPS_KHZ = [2.5, 5.0, 6.25, 10.0, 12.5, 25.0, 8.33, 50.0]

# Duplex/offset direction labels for byte 0x0B low nibble.
DUPLEX_LABELS = ["", "+", "-"]   # 0 = simplex, 1 = +shift, 2 = -shift


@dataclass
class Channel:
    index: int           # 0-based; users see index+1
    name: str
    freq_hz: int         # 0 means empty/unprogrammed
    mode: str
    scanlist: int        # raw byte value
    band: int            # 0..6 derived from frequency
    raw_record: bytes    # 16 raw bytes
    raw_attr: bytes      # 2 raw bytes (compander+band, scanlist)
    # Tone state — see kradio.tones for byte semantics.
    rx_tmode: int = 0    # TMODE_NONE / TMODE_TONE / TMODE_DTCS / TMODE_RDCS
    rx_tone_label: str = ""
    tx_tmode: int = 0
    tx_tone_label: str = ""
    # Extra channel-record fields (decoded for the GUI grid columns).
    duplex: str = ""             # "" / "+" / "-"
    offset_hz: int = 0           # 0 when duplex == ""
    tx_power: int = 0            # 0..7, indexes POWER_LEVELS
    step_idx: int = 0            # 0..7, indexes STEPS_KHZ
    busy_lockout: bool = False   # byte 0x0C bit 5
    tx_lock: bool = False        # byte 0x0C bit 6
    freq_reverse: bool = False   # byte 0x0C bit 0

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
    """Channel name: ASCII, terminated by 0xFF or 0x00."""
    name: list[str] = []
    for b in raw[:CHANNEL_NAME_MAX]:
        if b == 0xFF or b == 0x00:
            break
        if 0x20 <= b <= 0x7E:
            name.append(chr(b))
        else:
            break
    return "".join(name).rstrip()


def encode_name(name: str) -> bytes:
    """Encode a channel name into 16 bytes padded with 0xFF."""
    s = name.strip()[:CHANNEL_NAME_MAX]
    return s.encode("ascii", errors="replace").ljust(CHANNEL_NAME_SIZE, b"\xFF")


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

    # Bytes 4..7: signed offset stored as |offset|/10 LE u32 (no sign bit;
    # sign comes from offsetDir nibble of byte 0x0B).
    offset_raw = struct.unpack("<I", rec[4:8])[0]
    offset_hz = (offset_raw if offset_raw != 0xFFFFFFFF else 0) * 10

    # byte 0x0B: high nibble = modulation, low nibble = offsetDir
    modulation = (rec[0x0B] >> 4) & 0x0F
    offset_dir = rec[0x0B] & 0x0F           # 0=none, 1=+shift, 2=-shift
    duplex = DUPLEX_LABELS[offset_dir] if offset_dir < len(DUPLEX_LABELS) else ""
    if duplex == "":
        offset_hz = 0                        # ignore stale offset on simplex

    # byte 0x0C packs flags + power + bandwidth + reverse.
    byte_0c = rec[0x0C]
    bandwidth = (byte_0c >> 1) & 0x01
    tx_power = (byte_0c >> 2) & 0x07
    busy_lockout = bool(byte_0c & 0x20)
    tx_lock = bool(byte_0c & 0x40)
    freq_reverse = bool(byte_0c & 0x01)

    mode_idx = modulation * 2 + bandwidth
    mode = MODE_TABLE[mode_idx] if 0 <= mode_idx < len(MODE_TABLE) else "?"

    # byte 0x0E: tuning step index
    step_idx = rec[0x0E] if rec[0x0E] < len(STEPS_KHZ) else 0

    # Tones (bytes 0x08..0x0A; see kradio.tones for the bit layout).
    rx_code = rec[0x08]
    tx_code = rec[0x09]
    flags = rec[0x0A]
    tx_flag = (flags >> 4) & 0x0F
    rx_flag = flags & 0x0F
    rx_tmode, rx_label = tones.decode_tone(rx_code, rx_flag)
    tx_tmode, tx_label = tones.decode_tone(tx_code, tx_flag)

    # ch_attr byte 0: [unused:3, compander:2, band:3]
    # ch_attr byte 1: scanlist
    band = attr[0] & 0x07
    scn = attr[1]

    name = _decode_name(name_raw) if freq_hz else ""

    return Channel(
        index=idx,
        name=name,
        freq_hz=freq_hz,
        mode=mode,
        scanlist=scn,
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
    """Decode all 1024 channel slots from a complete EEPROM image."""
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


def decode_listnames(eeprom: bytes) -> list[str]:
    """Decode the 24 scan-list display names (4 chars each)."""
    names: list[str] = []
    for i in range(NUM_LISTS):
        raw = eeprom[LISTNAME_BASE + i * LISTNAME_SIZE:
                     LISTNAME_BASE + (i + 1) * LISTNAME_SIZE]
        chars: list[str] = []
        for b in raw:
            if b == 0xFF or b == 0x00:
                break
            if 0x20 <= b <= 0x7E:
                chars.append(chr(b))
            else:
                break
        names.append("".join(chars).strip())
    return names


# --- Address helpers ------------------------------------------------------

def addr_channel(idx: int) -> int:
    return CHANNELS_BASE + idx * CHANNEL_SIZE


def addr_channel_name(idx: int) -> int:
    return CHANNEL_NAMES_BASE + idx * CHANNEL_NAME_SIZE


def addr_ch_attr(idx: int) -> int:
    return CH_ATTR_BASE + idx * CH_ATTR_SIZE


def addr_scanlist_byte(idx: int) -> int:
    """Address of the scanlist byte (offset +1 inside the ch_attr entry)."""
    return CH_ATTR_BASE + idx * CH_ATTR_SIZE + 1


def patch_scanlist(attr_bytes: bytes, new_scanlist: int) -> bytes:
    """Return the 2 ch_attr bytes with the scanlist replaced."""
    if not 0 <= new_scanlist <= SCAN_ALL:
        raise ValueError(f"scanlist must be 0..25, got {new_scanlist}")
    return bytes([attr_bytes[0], new_scanlist & 0xFF])


# --- Frequency band detection ---------------------------------------------
# Used to derive the `band` value (0..6) stored in ch_attr byte 0 from a
# frequency. Boundaries match the F4HWN driver's BANDS_WIDE table.

BANDS_WIDE_MHZ = [
    (0,    18.0,    108.0),
    (1,    108.0,   136.9999),
    (2,    137.0,   173.9999),
    (3,    174.0,   349.9999),
    (4,    350.0,   399.9999),
    (5,    400.0,   469.9999),
    (6,    470.0,   1300.0),
]


def freq_to_band(freq_hz: int) -> int:
    """Map a frequency in Hz to the band code stored in ch_attr."""
    mhz = freq_hz / 1_000_000.0
    for code, lo, hi in BANDS_WIDE_MHZ:
        if lo <= mhz <= hi:
            return code
    return 6  # default to highest band on out-of-range


# --- Channel encoder (writes back into an EEPROM image) -------------------

def encode_channel_record(
    *,
    freq_hz: int,
    mode: str,
    offset_hz: int = 0,
    duplex: str = "",          # "", "+", "-"
    tx_power: int = 2,         # 0..7 index into UVK5_POWER_LEVELS
    tuning_step_idx: int = 0,  # index into STEPS table (CHIRP)
    busy_ch_lockout: bool = False,
    tx_lock: bool = False,
    freq_reverse: bool = False,
    keep_record: bytes | None = None,
) -> bytes:
    """
    Produce a 16-byte channel record encoded for the F4HWN Fusion 5.x
    layout. Optional fields not exposed here (tone codes, DTMF) are taken
    from `keep_record` when supplied — useful when patching an existing
    channel without resetting CTCSS / DTCS configuration.
    """
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

    # Start either from the existing record (preserving rxcode/txcode/etc.)
    # or from a clean slate.
    rec = bytearray(keep_record if keep_record else b"\x00" * CHANNEL_SIZE)
    if len(rec) != CHANNEL_SIZE:
        raise ValueError(f"keep_record length must be {CHANNEL_SIZE}")

    struct.pack_into("<I", rec, 0, freq_hz // 10)
    struct.pack_into("<I", rec, 4, abs(offset_hz) // 10)
    # 0x0B: modulation high nibble, offsetDir low nibble
    rec[0x0B] = ((modulation & 0x0F) << 4) | (offset_dir & 0x0F)
    # 0x0C: __UNUSED01:1, txLock:1, busyChLockout:1, txpower:3, bandwidth:1, freq_reverse:1
    pwr = max(0, min(7, tx_power))
    byte_0c = (
        (1 if tx_lock else 0) << 6
        | (1 if busy_ch_lockout else 0) << 5
        | (pwr & 0x07) << 2
        | (bandwidth & 0x01) << 1
        | (1 if freq_reverse else 0)
    )
    rec[0x0C] = byte_0c
    rec[0x0E] = max(0, min(255, tuning_step_idx))
    return bytes(rec)


def patch_channel_tones(image: bytearray, idx: int, *,
                        rx_tone: str | None = None,
                        tx_tone: str | None = None) -> None:
    """
    Update the CTCSS / DTCS tone fields of channel `idx`. Each argument
    accepts the same forms as `kradio.tones.encode_tone`. Passing None
    leaves that direction unchanged.
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
    scanlist: int | None = None,
    band: int | None = None,
    rx_tone: str | None = None,
    tx_tone: str | None = None,
    keep_existing_codes: bool = True,
    **encode_kwargs,
) -> None:
    """
    Patch channel `idx` (0-based) in an EEPROM image. Any field left as
    None is preserved. When `freq_hz` is provided the band byte is
    re-derived automatically (override with explicit `band`).

    `rx_tone` / `tx_tone` accept the strings parsed by
    `kradio.tones.encode_tone` (e.g. "88.5", "D023N", "OFF").
    """
    if not 0 <= idx < NUM_CHANNELS:
        raise ValueError(f"channel index out of range: {idx}")

    # Channel record (0x0000)
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

    # Tones — written AFTER the record encode so they survive a
    # freq/mode patch that would otherwise blow them away.
    if rx_tone is not None or tx_tone is not None:
        patch_channel_tones(image, idx, rx_tone=rx_tone, tx_tone=tx_tone)

    # Channel name (0x4000)
    if name is not None:
        nm_addr = addr_channel_name(idx)
        image[nm_addr:nm_addr + CHANNEL_NAME_SIZE] = encode_name(name)

    # ch_attr (0x8000): the F4HWN ChannelAttributes_t struct, 16 bits LE:
    #   bits 0-2 : band (0..6 valid; 7 = "EMPTY/uninitialised" marker)
    #   bits 3-4 : compander
    #   bits 5-6 : unused
    #   bit    7 : exclude (when set, channel is hidden from MR mode)
    #   bits 8-15: scanlist bitmask (bit N = "in list N+1"; max 8 lists
    #              addressable that way; F4HWN exposes 24 lists in UI but
    #              the byte holds 8 bits — see SCAN_LIST_LABELS)
    #
    # CRITICAL: write the FULL byte, do NOT preserve previous bits via OR.
    # On UV-K1(8) v3 fresh-from-the upstream K5/K1 tooling the existing bytes are 0xFF
    # or 0x07 0x00, both of which leave bit 7 (exclude) set if we only
    # touch the band field. With exclude=1 the F4HWN K1 firmware treats
    # the slot as empty (V/M won't enter MR mode, ChName/ChDel show NULL).
    # Always overwrite both bytes from scratch.
    attr_addr = addr_ch_attr(idx)
    if band is None and freq_hz is not None:
        band = freq_to_band(freq_hz)
    if band is None:
        # Reading the existing band byte for partial updates without freq.
        band = image[attr_addr] & 0x07
    if scanlist is None:
        scanlist = image[attr_addr + 1]
    elif not 0 <= scanlist <= SCAN_ALL:
        raise ValueError(f"scanlist out of range: {scanlist}")
    image[attr_addr]     = band & 0x07           # band only; compander=0, exclude=0
    image[attr_addr + 1] = scanlist & 0xFF       # scanlist bitmask


def clear_channel_in_image(image: bytearray, idx: int) -> None:
    """Mark a channel slot as empty.

    Channel record + name go to 0xFF (uninitialised flash).
    Attribute byte uses F4HWN's "empty slot" marker `0x07 0x00`
    (band=7 + scanlist=0); writing 0xFF 0xFF would also work but the
    `07 00` form matches what the firmware itself writes via
    `SETTINGS_UpdateChannel(...)` for fresh slots and what
    tools that initialise the table — keeps the
    flash sectors uniform and avoids unnecessary erase cycles on the
    next write.
    """
    if not 0 <= idx < NUM_CHANNELS:
        raise ValueError(f"channel index out of range: {idx}")
    rec_addr = addr_channel(idx)
    image[rec_addr:rec_addr + CHANNEL_SIZE] = b"\xFF" * CHANNEL_SIZE
    nm_addr = addr_channel_name(idx)
    image[nm_addr:nm_addr + CHANNEL_NAME_SIZE] = b"\xFF" * CHANNEL_NAME_SIZE
    attr_addr = addr_ch_attr(idx)
    image[attr_addr]     = 0x07   # band=7 = empty/uninitialised marker
    image[attr_addr + 1] = 0x00   # no scan lists
