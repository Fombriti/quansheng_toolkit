"""Tests for kradio.dtmf_contacts — 16-slot DTMF phonebook at 0x1C00."""
from __future__ import annotations

import pytest

from quansheng_toolkit.kradio import dtmf_contacts as dtmf


# ---------------------------------------------------------------------------
# Constants — basic shape
# ---------------------------------------------------------------------------

def test_layout_constants():
    assert dtmf.CONTACTS_BASE == 0x1C00
    assert dtmf.NUM_CONTACTS == 16
    assert dtmf.CONTACT_SIZE == 16
    assert dtmf.NAME_LEN == 8
    assert dtmf.CODE_LEN == 8
    assert dtmf.DTMF_CHARS == "0123456789ABCD*#"


def test_addr_contact_range():
    assert dtmf.addr_contact(0) == 0x1C00
    assert dtmf.addr_contact(1) == 0x1C10
    assert dtmf.addr_contact(15) == 0x1CF0
    with pytest.raises(ValueError):
        dtmf.addr_contact(-1)
    with pytest.raises(ValueError):
        dtmf.addr_contact(16)


# ---------------------------------------------------------------------------
# Decode
# ---------------------------------------------------------------------------

def test_decode_empty_slot_FF():
    contact = dtmf.decode_contact(3, b"\xFF" * 16)
    assert contact.index == 3
    assert contact.name == ""
    assert contact.code == ""
    assert contact.is_empty


def test_decode_empty_slot_zeros():
    contact = dtmf.decode_contact(0, b"\x00" * 16)
    assert contact.is_empty


def test_decode_typical_contact():
    raw = b"FRA" + b"\xFF" * 5 + b"123#" + b"\xFF" * 4
    contact = dtmf.decode_contact(7, raw)
    assert contact.name == "FRA"
    assert contact.code == "123#"
    assert not contact.is_empty


def test_decode_full_length_contact():
    # 8-char name + 8-char code
    raw = b"BABBO_HM" + b"12345678"
    contact = dtmf.decode_contact(0, raw)
    assert contact.name == "BABBO_HM"
    assert contact.code == "12345678"


def test_decode_all_contacts_count():
    eeprom = bytearray(b"\xFF" * 0x2000)
    contacts = dtmf.decode_all_contacts(bytes(eeprom))
    assert len(contacts) == 16
    assert all(c.is_empty for c in contacts)


def test_decode_too_short_raises():
    with pytest.raises(ValueError):
        dtmf.decode_contact(0, b"\xFF" * 15)


def test_decode_all_too_short_raises():
    with pytest.raises(ValueError):
        dtmf.decode_all_contacts(b"\xFF" * 0x100)


# ---------------------------------------------------------------------------
# Encode
# ---------------------------------------------------------------------------

def test_encode_pads_with_FF():
    enc = dtmf.encode_contact("FRA", "123")
    assert len(enc) == 16
    assert enc[:3] == b"FRA"
    assert enc[3:8] == b"\xFF\xFF\xFF\xFF\xFF"
    assert enc[8:11] == b"123"
    assert enc[11:] == b"\xFF\xFF\xFF\xFF\xFF"


def test_encode_uppercases_dtmf_chars():
    # 'a' is not valid DTMF; 'A' is. Auto-uppercase.
    enc = dtmf.encode_contact("X", "abcd")
    assert enc[8:12] == b"ABCD"


def test_encode_rejects_long_name():
    with pytest.raises(ValueError):
        dtmf.encode_contact("123456789", "1")


def test_encode_rejects_long_code():
    with pytest.raises(ValueError):
        dtmf.encode_contact("X", "123456789")


def test_encode_rejects_invalid_dtmf_char():
    with pytest.raises(ValueError, match="invalid char"):
        dtmf.encode_contact("X", "12E3")  # E is not a DTMF char


def test_encode_rejects_non_printable_name():
    with pytest.raises(ValueError, match="non-printable"):
        dtmf.encode_contact("FR\x01", "1")


def test_encode_empty_name_and_code():
    enc = dtmf.encode_contact("", "")
    assert enc == b"\xFF" * 16


# ---------------------------------------------------------------------------
# Patch / clear in image
# ---------------------------------------------------------------------------

def test_patch_contact_round_trip():
    img = bytearray(b"\xFF" * 0x2000)
    dtmf.patch_contact_in_image(img, 5, name="HOME", code="911")
    contact = dtmf.decode_all_contacts(bytes(img))[5]
    assert contact.name == "HOME"
    assert contact.code == "911"


def test_patch_does_not_disturb_other_slots():
    img = bytearray(b"\xFF" * 0x2000)
    # Pre-stage a known value at slot 6.
    img[dtmf.addr_contact(6):dtmf.addr_contact(6) + 16] = b"NEIGH" + b"\xFF\xFF\xFF" + b"234*" + b"\xFF\xFF\xFF\xFF"
    before = bytes(img[dtmf.addr_contact(6):dtmf.addr_contact(6) + 16])

    dtmf.patch_contact_in_image(img, 5, name="HOME", code="911")
    after = bytes(img[dtmf.addr_contact(6):dtmf.addr_contact(6) + 16])
    assert before == after


def test_clear_contact_writes_FF():
    img = bytearray(b"\xFF" * 0x2000)
    dtmf.patch_contact_in_image(img, 0, name="X", code="1")
    dtmf.clear_contact_in_image(img, 0)
    assert img[dtmf.addr_contact(0):dtmf.addr_contact(0) + 16] == b"\xFF" * 16


def test_decode_then_encode_round_trip_preserves_empty_padding():
    img = bytearray(b"\xFF" * 0x2000)
    dtmf.patch_contact_in_image(img, 9, name="ABCDE", code="*0#")
    contact = dtmf.decode_all_contacts(bytes(img))[9]
    re_enc = dtmf.encode_contact(contact.name, contact.code)
    assert img[dtmf.addr_contact(9):dtmf.addr_contact(9) + 16] == re_enc
