"""
CTCSS / DCS tone tables and helpers shared between the F4HWN and K5 V1
memory modules. Both layouts use the same byte semantics for tones
(rxcode / txcode / codeflags) and share the same CTCSS and DTCS tables.

Tone byte layout inside a channel record (bytes 0x08..0x0A):
  * 0x08  rxcode      — index into CTCSS_TONES (TMODE_TONE) or
                        DTCS_CODES (TMODE_DTCS / TMODE_RDCS)
  * 0x09  txcode      — same encoding for the TX direction
  * 0x0A  txcodeflag (high nibble) | rxcodeflag (low nibble)
                      — TMODE per direction

TMODE values:
  0  NONE     no tone
  1  Tone     CTCSS analog tone
  2  DTCS     digital code (normal polarity)
  3  RDCS     digital code (reverse polarity)
"""
from __future__ import annotations


# --- Tone modes ------------------------------------------------------------

TMODE_NONE = 0
TMODE_TONE = 1
TMODE_DTCS = 2
TMODE_RDCS = 3

# Public-facing labels (CHIRP-style).
TMODE_LABELS = ["", "Tone", "DTCS", "DTCS-R"]

# UI-friendly type names matching the upstream channels.js dropdown.
# Used by the GUI to drive a 2-stage tone editor (type + value).
TONE_TYPE_OFF   = "OFF"
TONE_TYPE_CTCSS = "CTCSS"
TONE_TYPE_DCS_N = "DCS-N"
TONE_TYPE_DCS_I = "DCS-I"

TONE_TYPE_LABELS: list[str] = [
    TONE_TYPE_OFF, TONE_TYPE_CTCSS, TONE_TYPE_DCS_N, TONE_TYPE_DCS_I,
]


def tone_type_for_tmode(tmode: int) -> str:
    """Map a TMODE_* numeric mode to a UI type label."""
    if tmode == TMODE_TONE: return TONE_TYPE_CTCSS
    if tmode == TMODE_DTCS: return TONE_TYPE_DCS_N
    if tmode == TMODE_RDCS: return TONE_TYPE_DCS_I
    return TONE_TYPE_OFF


def tmode_for_tone_type(label: str) -> int:
    """Inverse of tone_type_for_tmode — used when the user picks a type."""
    if label == TONE_TYPE_CTCSS: return TMODE_TONE
    if label == TONE_TYPE_DCS_N: return TMODE_DTCS
    if label == TONE_TYPE_DCS_I: return TMODE_RDCS
    return TMODE_NONE


def tone_values_for_type(label: str) -> list[str]:
    """The dropdown items the value combobox should show for this type."""
    if label == TONE_TYPE_CTCSS:
        return [f"{f:.1f} Hz" for f in CTCSS_TONES]
    if label == TONE_TYPE_DCS_N:
        return [f"D{c:03d}N" for c in DTCS_CODES]
    if label == TONE_TYPE_DCS_I:
        return [f"D{c:03d}I" for c in DTCS_CODES]
    return []   # OFF → no value to pick


def default_value_for_type(label: str) -> str:
    """First valid value when switching INTO a type from OFF."""
    values = tone_values_for_type(label)
    return values[0] if values else "OFF"


# --- CTCSS table (50 standard tones) --------------------------------------
# Lifted verbatim from the F4HWN CHIRP driver; matches the radio's table.

CTCSS_TONES: list[float] = [
    67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4,
    88.5, 91.5, 94.8, 97.4, 100.0, 103.5, 107.2, 110.9,
    114.8, 118.8, 123.0, 127.3, 131.8, 136.5, 141.3, 146.2,
    151.4, 156.7, 159.8, 162.2, 165.5, 167.9, 171.3, 173.8,
    177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5,
    203.5, 206.5, 210.7, 218.1, 225.7, 229.1, 233.6, 241.8,
    250.3, 254.1,
]

# --- DTCS table (105 standard codes) --------------------------------------

DTCS_CODES: list[int] = [
    23,  25,  26,  31,  32,  36,  43,  47,  51,  53,  54,
    65,  71,  72,  73,  74,  114, 115, 116, 122, 125, 131,
    132, 134, 143, 145, 152, 155, 156, 162, 165, 172, 174,
    205, 212, 223, 225, 226, 243, 244, 245, 246, 251, 252,
    255, 261, 263, 265, 266, 271, 274, 306, 311, 315, 325,
    331, 332, 343, 346, 351, 356, 364, 365, 371, 411, 412,
    413, 423, 431, 432, 445, 446, 452, 454, 455, 462, 464,
    465, 466, 503, 506, 516, 523, 526, 532, 546, 565, 606,
    612, 624, 627, 631, 632, 654, 662, 664, 703, 712, 723,
    731, 732, 734, 743, 754,
]


# --- Decode / encode -------------------------------------------------------

def decode_tone(code: int, flag: int) -> tuple[int, str]:
    """
    Resolve a (code, flag) pair from a channel record into (mode, label).

    Returns:
      mode  — one of TMODE_NONE / TMODE_TONE / TMODE_DTCS / TMODE_RDCS
      label — display string ("", "88.5 Hz", "D023N", "D023I")
              An out-of-range code returns "" for the label, and we
              degrade `mode` back to TMODE_NONE so callers don't show a
              bogus "?" value.
    """
    flag &= 0x03  # only low 2 bits matter; the byte stores 4 bits but the
                  # driver only uses 0..3.
    if flag == TMODE_NONE:
        return TMODE_NONE, ""
    if flag == TMODE_TONE:
        if 0 <= code < len(CTCSS_TONES):
            return TMODE_TONE, f"{CTCSS_TONES[code]:.1f} Hz"
        return TMODE_NONE, ""
    if flag in (TMODE_DTCS, TMODE_RDCS):
        if 0 <= code < len(DTCS_CODES):
            polarity = "N" if flag == TMODE_DTCS else "I"
            return flag, f"D{DTCS_CODES[code]:03d}{polarity}"
        return TMODE_NONE, ""
    return TMODE_NONE, ""


def encode_tone(spec: str | None) -> tuple[int, int]:
    """
    Parse a user-supplied tone string into (code, flag) ready to be
    written into bytes 0x08/0x09 (code) and a nibble of 0x0A (flag).

    Accepted forms (case-insensitive):
      ""   / "OFF" / None     → no tone
      "88.5"  / "88.5 Hz"     → CTCSS at the matching index
      "D023" / "D023N"        → DCS code, normal polarity
      "D023I"                 → DCS code, inverted polarity
      "23N"                   → DCS short form, normal polarity
    """
    if not spec:
        return 0, TMODE_NONE
    s = str(spec).strip().upper().replace(" HZ", "").replace("HZ", "")
    if s in ("", "OFF", "NONE"):
        return 0, TMODE_NONE

    # DTCS form: "D023N" / "D023I" / "D023" / "23N" / "023"
    if s.startswith("D"):
        s = s[1:]
    polarity = TMODE_DTCS
    if s.endswith("I"):
        polarity = TMODE_RDCS
        s = s[:-1]
    elif s.endswith("N"):
        s = s[:-1]
    if s.isdigit():
        n = int(s)
        if n in DTCS_CODES:
            return DTCS_CODES.index(n), polarity
        # Try CTCSS — the integer part might match (e.g. 100 vs 100.0).
        for i, t in enumerate(CTCSS_TONES):
            if int(t) == n and t == int(t):
                return i, TMODE_TONE
        raise ValueError(f"unknown tone code: {spec!r}")

    # CTCSS form: "88.5"
    try:
        f = float(s)
    except ValueError:
        raise ValueError(f"unrecognized tone spec: {spec!r}") from None
    # Match within 0.05 Hz to absorb input rounding.
    for i, t in enumerate(CTCSS_TONES):
        if abs(t - f) < 0.05:
            return i, TMODE_TONE
    raise ValueError(f"CTCSS tone {f} Hz not in the 50-tone table")
