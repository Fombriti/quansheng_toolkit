"""
Tests for kradio.tones — CTCSS / DTCS encoding shared by both memory
modules. Locks in the byte-level semantics so a careless refactor of
the tone tables can't silently corrupt every channel's tones.
"""
from __future__ import annotations

import pytest

from quansheng_toolkit.kradio import tones


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

def test_ctcss_table_size_and_endpoints():
    assert len(tones.CTCSS_TONES) == 50
    assert tones.CTCSS_TONES[0] == 67.0
    assert tones.CTCSS_TONES[-1] == 254.1
    # The 88.5 Hz reference tone the F4HWN CSV uses everywhere.
    assert 88.5 in tones.CTCSS_TONES


def test_dtcs_table_size_and_endpoints():
    # Standard 104-entry list (matching kk7ds CHIRP and F4HWN).
    assert len(tones.DTCS_CODES) == 104
    assert tones.DTCS_CODES[0] == 23
    assert tones.DTCS_CODES[-1] == 754
    # 023 is the most common default in the wild.
    assert 23 in tones.DTCS_CODES


# ---------------------------------------------------------------------------
# decode_tone — roundtripping every byte the radio could legitimately store
# ---------------------------------------------------------------------------

def test_decode_none_flag_means_no_tone():
    mode, label = tones.decode_tone(0, tones.TMODE_NONE)
    assert mode == tones.TMODE_NONE
    assert label == ""


@pytest.mark.parametrize("idx, expected_label", [
    (0, "67.0 Hz"),
    (8, "88.5 Hz"),
    (49, "254.1 Hz"),
])
def test_decode_ctcss(idx, expected_label):
    mode, label = tones.decode_tone(idx, tones.TMODE_TONE)
    assert mode == tones.TMODE_TONE
    assert label == expected_label


@pytest.mark.parametrize("idx, expected", [
    (0, "D023N"),    # 023, normal polarity
    (103, "D754N"),  # last code
])
def test_decode_dtcs_normal(idx, expected):
    mode, label = tones.decode_tone(idx, tones.TMODE_DTCS)
    assert mode == tones.TMODE_DTCS
    assert label == expected


def test_decode_dtcs_inverted():
    mode, label = tones.decode_tone(0, tones.TMODE_RDCS)
    assert mode == tones.TMODE_RDCS
    assert label == "D023I"


def test_decode_out_of_range_falls_back_to_none():
    # Unitialised EEPROM byte: 0xFF as ctcss index.
    mode, label = tones.decode_tone(0xFF, tones.TMODE_TONE)
    assert mode == tones.TMODE_NONE
    assert label == ""


# ---------------------------------------------------------------------------
# encode_tone — accept the various human-readable forms
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("spec, expected_code, expected_flag", [
    (None,         0,  tones.TMODE_NONE),
    ("",           0,  tones.TMODE_NONE),
    ("OFF",        0,  tones.TMODE_NONE),
    ("none",       0,  tones.TMODE_NONE),
    ("88.5",       8,  tones.TMODE_TONE),
    ("88.5 Hz",    8,  tones.TMODE_TONE),
    ("88.5Hz",     8,  tones.TMODE_TONE),
    ("100",       12,  tones.TMODE_TONE),  # int matches CTCSS 100.0
    ("D023",       0,  tones.TMODE_DTCS),
    ("D023N",      0,  tones.TMODE_DTCS),
    ("D023I",      0,  tones.TMODE_RDCS),
    ("D754N",    103,  tones.TMODE_DTCS),
    ("23N",        0,  tones.TMODE_DTCS),  # short form
    ("023",        0,  tones.TMODE_DTCS),
])
def test_encode_tone(spec, expected_code, expected_flag):
    code, flag = tones.encode_tone(spec)
    assert code == expected_code
    assert flag == expected_flag


def test_encode_tone_rejects_garbage():
    with pytest.raises(ValueError):
        tones.encode_tone("88.x")
    with pytest.raises(ValueError):
        tones.encode_tone("D999")  # 999 is not a real DTCS code


def test_encode_then_decode_roundtrips_for_every_ctcss():
    for i, freq in enumerate(tones.CTCSS_TONES):
        code, flag = tones.encode_tone(f"{freq}")
        assert code == i
        assert flag == tones.TMODE_TONE
        mode, label = tones.decode_tone(code, flag)
        assert mode == tones.TMODE_TONE
        assert label == f"{freq:.1f} Hz"


def test_encode_then_decode_roundtrips_for_every_dtcs():
    for i, c in enumerate(tones.DTCS_CODES):
        code, flag = tones.encode_tone(f"D{c:03d}N")
        assert code == i
        assert flag == tones.TMODE_DTCS
        mode, label = tones.decode_tone(code, flag)
        assert mode == tones.TMODE_DTCS
        assert label == f"D{c:03d}N"


# ---------------------------------------------------------------------------
# Two-stage tone editor helpers (GUI Channels view)
# ---------------------------------------------------------------------------

def test_tone_type_labels_match_published_set():
    # Same four entries the upstream rxToneType dropdown shows.
    assert tones.TONE_TYPE_LABELS == ["OFF", "CTCSS", "DCS-N", "DCS-I"]


@pytest.mark.parametrize("tmode, expected", [
    (tones.TMODE_NONE, "OFF"),
    (tones.TMODE_TONE, "CTCSS"),
    (tones.TMODE_DTCS, "DCS-N"),
    (tones.TMODE_RDCS, "DCS-I"),
])
def test_tone_type_for_tmode_round_trip(tmode, expected):
    label = tones.tone_type_for_tmode(tmode)
    assert label == expected
    # Inverse must recover the original tmode.
    assert tones.tmode_for_tone_type(label) == tmode


def test_tmode_for_unknown_label_falls_back_to_none():
    assert tones.tmode_for_tone_type("definitely_not_a_type") == tones.TMODE_NONE


def test_tone_values_for_type_lengths():
    assert len(tones.tone_values_for_type("OFF")) == 0
    assert len(tones.tone_values_for_type("CTCSS")) == 50
    assert len(tones.tone_values_for_type("DCS-N")) == 104
    assert len(tones.tone_values_for_type("DCS-I")) == 104


def test_tone_values_dcs_polarity_suffix():
    # DCS-N values end in "N", DCS-I in "I". Used by the value combobox.
    n = tones.tone_values_for_type("DCS-N")
    i = tones.tone_values_for_type("DCS-I")
    assert all(v.endswith("N") for v in n)
    assert all(v.endswith("I") for v in i)
    # Same numeric codes, different polarity.
    assert n[0][:-1] == i[0][:-1]


def test_default_value_for_type():
    assert tones.default_value_for_type("OFF") == "OFF"
    assert tones.default_value_for_type("CTCSS") == "67.0 Hz"  # first CTCSS
    assert tones.default_value_for_type("DCS-N") == "D023N"
    assert tones.default_value_for_type("DCS-I") == "D023I"
