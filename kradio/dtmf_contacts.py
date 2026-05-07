"""
DTMF contacts table — the radio's per-radio "phonebook" of named DTMF
codes used for selective calling.

Layout is identical between F4HWN K1 / K5 V3 and stock K5 V1 / K1 stock:

    0x1C00  contact[16]   16 bytes/record
              [0..7]  8-byte ASCII name (0xFF or 0x00 = unused tail)
              [8..15] 8-byte ASCII DTMF code (chars from `0123456789ABCD*#`)

F4HWN K1's expanded layout reserves channel slots 448..463 for this
table when both features are active (slot N at 0x1C00 + N*16 maps to
channel slot 448 + (N - whatever)). In practice users either fill the
DTMF contacts or use those high channel slots — not both.
"""
from __future__ import annotations

from dataclasses import dataclass


CONTACTS_BASE = 0x1C00
CONTACT_SIZE = 16
NUM_CONTACTS = 16
NAME_LEN = 8
CODE_LEN = 8

DTMF_CHARS = "0123456789ABCD*#"


@dataclass(frozen=True)
class DTMFContact:
    index: int
    name: str
    code: str

    @property
    def is_empty(self) -> bool:
        return not (self.name or self.code)


def _decode_field(raw: bytes) -> str:
    """ASCII field, terminated by 0x00 or 0xFF."""
    out = []
    for b in raw:
        if b in (0x00, 0xFF):
            break
        if 0x20 <= b < 0x7F:
            out.append(chr(b))
        # Skip non-printable bytes silently — corrupted slot.
    return "".join(out).rstrip()


def decode_contact(idx: int, raw: bytes) -> DTMFContact:
    if len(raw) < CONTACT_SIZE:
        raise ValueError(
            f"DTMF contact record must be {CONTACT_SIZE} bytes, got {len(raw)}"
        )
    name = _decode_field(raw[:NAME_LEN])
    code = _decode_field(raw[NAME_LEN:NAME_LEN + CODE_LEN])
    return DTMFContact(index=idx, name=name, code=code)


def decode_all_contacts(eeprom: bytes) -> list[DTMFContact]:
    """Decode all 16 contact slots from a complete EEPROM image."""
    if len(eeprom) < CONTACTS_BASE + NUM_CONTACTS * CONTACT_SIZE:
        raise ValueError(
            f"EEPROM image too small for DTMF contacts area "
            f"(need 0x{CONTACTS_BASE + NUM_CONTACTS * CONTACT_SIZE:04X}, "
            f"got {len(eeprom)})"
        )
    out = []
    for i in range(NUM_CONTACTS):
        off = CONTACTS_BASE + i * CONTACT_SIZE
        out.append(decode_contact(i, eeprom[off:off + CONTACT_SIZE]))
    return out


def addr_contact(idx: int) -> int:
    if not 0 <= idx < NUM_CONTACTS:
        raise ValueError(f"contact index out of range: {idx}")
    return CONTACTS_BASE + idx * CONTACT_SIZE


def _validate_name(name: str) -> str:
    """Names accept printable ASCII, max 8 chars."""
    if name is None:
        return ""
    name = str(name)
    if len(name) > NAME_LEN:
        raise ValueError(
            f"DTMF contact name too long ({len(name)} > {NAME_LEN})"
        )
    for c in name:
        if not (0x20 <= ord(c) < 0x7F):
            raise ValueError(
                f"DTMF contact name contains non-printable char: {c!r}"
            )
    return name


def _validate_code(code: str) -> str:
    """DTMF codes must be drawn from `0123456789ABCD*#`, max 8 chars."""
    if code is None:
        return ""
    code = str(code).upper()
    if len(code) > CODE_LEN:
        raise ValueError(
            f"DTMF code too long ({len(code)} > {CODE_LEN})"
        )
    for c in code:
        if c not in DTMF_CHARS:
            raise ValueError(
                f"DTMF code contains invalid char {c!r} "
                f"(allowed: {DTMF_CHARS})"
            )
    return code


def encode_contact(name: str, code: str) -> bytes:
    """Encode a 16-byte contact record (name | code), padded with 0xFF.

    The radio firmware terminates each field on the first 0x00 or 0xFF;
    we use 0xFF padding to match what fresh flash looks like (matches
    the upstream K5/K1 tooling and the F4HWN reads we observe in the wild).
    """
    name = _validate_name(name)
    code = _validate_code(code)
    name_bytes = name.encode("ascii").ljust(NAME_LEN, b"\xFF")
    code_bytes = code.encode("ascii").ljust(CODE_LEN, b"\xFF")
    return name_bytes + code_bytes


def patch_contact_in_image(image: bytearray, idx: int, *,
                           name: str | None = None,
                           code: str | None = None) -> None:
    """Update DTMF contact `idx` in-place in an EEPROM image.

    Both `name` and `code` are required because the firmware stores
    them as a single 16-byte record. To clear a slot pass empty
    strings (or call `clear_contact_in_image`).
    """
    addr = addr_contact(idx)
    image[addr:addr + CONTACT_SIZE] = encode_contact(
        name=name or "", code=code or ""
    )


def clear_contact_in_image(image: bytearray, idx: int) -> None:
    """Mark a contact slot as empty (all 0xFF)."""
    addr = addr_contact(idx)
    image[addr:addr + CONTACT_SIZE] = b"\xFF" * CONTACT_SIZE
