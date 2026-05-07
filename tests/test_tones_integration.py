"""
Integration tests for the channel-level tone fields on both profiles
(F4HWN and K5 V1 stock). Verifies decode → patch → re-decode is stable
and that the flag nibble for each direction stays in its own half of
byte 0x0A (bits 4..7 = TX, 0..3 = RX).
"""
from __future__ import annotations

from quansheng_toolkit.kradio import memory as f4hwn
from quansheng_toolkit.kradio import memory_uvk5_v1 as k5v1
from quansheng_toolkit.kradio import tones


def _make_f4hwn_image():
    img = bytearray(b"\xff" * 0xB190)
    f4hwn.patch_channel_in_image(
        img, idx=0, name="HAM-V", freq_hz=145_500_000,
        mode="FM", scanlist=1
    )
    return img


def _make_k5v1_image():
    img = bytearray(b"\xff" * 0x2000)
    k5v1.patch_channel_in_image(
        img, idx=0, name="HAM-V", freq_hz=145_500_000,
        mode="FM", scanlist=1
    )
    return img


# ---------------------------------------------------------------------------
# F4HWN
# ---------------------------------------------------------------------------

class TestF4HWNTones:
    def test_default_channel_has_no_tones(self):
        img = _make_f4hwn_image()
        ch = f4hwn.decode_all_channels(bytes(img))[0]
        assert ch.rx_tmode == tones.TMODE_NONE
        assert ch.tx_tmode == tones.TMODE_NONE
        assert ch.rx_tone_label == ""
        assert ch.tx_tone_label == ""

    def test_set_ctcss_both_directions(self):
        img = _make_f4hwn_image()
        f4hwn.patch_channel_tones(img, 0, rx_tone="88.5", tx_tone="100.0")
        ch = f4hwn.decode_all_channels(bytes(img))[0]
        assert ch.rx_tmode == tones.TMODE_TONE
        assert ch.rx_tone_label == "88.5 Hz"
        assert ch.tx_tmode == tones.TMODE_TONE
        assert ch.tx_tone_label == "100.0 Hz"

    def test_dtcs_normal_and_inverted(self):
        img = _make_f4hwn_image()
        f4hwn.patch_channel_tones(img, 0, rx_tone="D023N", tx_tone="D754I")
        ch = f4hwn.decode_all_channels(bytes(img))[0]
        assert ch.rx_tone_label == "D023N"
        assert ch.tx_tone_label == "D754I"
        # Re-write only RX: TX side stays put.
        f4hwn.patch_channel_tones(img, 0, rx_tone="OFF")
        ch = f4hwn.decode_all_channels(bytes(img))[0]
        assert ch.rx_tone_label == ""
        assert ch.tx_tone_label == "D754I"

    def test_rx_and_tx_use_separate_nibbles_of_codeflag(self):
        img = _make_f4hwn_image()
        # Set both, then update only RX. The TX nibble must survive.
        f4hwn.patch_channel_tones(img, 0, rx_tone="88.5", tx_tone="100.0")
        rec_addr = f4hwn.addr_channel(0)
        codeflag = img[rec_addr + 0x0A]
        assert codeflag & 0x0F == tones.TMODE_TONE   # RX nibble
        assert (codeflag >> 4) & 0x0F == tones.TMODE_TONE   # TX nibble

        f4hwn.patch_channel_tones(img, 0, rx_tone="OFF")
        codeflag = img[rec_addr + 0x0A]
        # RX cleared, TX still set.
        assert codeflag & 0x0F == tones.TMODE_NONE
        assert (codeflag >> 4) & 0x0F == tones.TMODE_TONE


# ---------------------------------------------------------------------------
# K5 V1 / K1 stock
# ---------------------------------------------------------------------------

class TestK5V1Tones:
    def test_default_channel_has_no_tones(self):
        img = _make_k5v1_image()
        ch = k5v1.decode_all_channels(bytes(img))[0]
        assert ch.rx_tmode == tones.TMODE_NONE
        assert ch.tx_tmode == tones.TMODE_NONE

    def test_patch_via_patch_channel_in_image(self):
        # Using the higher-level patch_channel_in_image (the path the CSV
        # importer goes through) — tones should be applied AFTER the
        # encode-record step, not lost.
        img = bytearray(b"\xff" * 0x2000)
        k5v1.patch_channel_in_image(
            img, idx=5, name="REPEATER",
            freq_hz=433_000_000, mode="FM", scanlist=1,
            rx_tone="88.5", tx_tone="88.5",
        )
        ch = k5v1.decode_all_channels(bytes(img))[5]
        assert ch.name == "REPEATER"
        assert ch.rx_tone_label == "88.5 Hz"
        assert ch.tx_tone_label == "88.5 Hz"
        # Channel-encode side effects must NOT have clobbered the tones.

    def test_patch_with_freq_only_keeps_existing_tones(self):
        img = bytearray(b"\xff" * 0x2000)
        k5v1.patch_channel_in_image(
            img, idx=0, name="X", freq_hz=145_500_000,
            mode="FM", scanlist=1, rx_tone="100.0"
        )
        # Now move the channel to a new frequency without touching tones.
        k5v1.patch_channel_in_image(
            img, idx=0, freq_hz=145_700_000, mode="FM"
        )
        ch = k5v1.decode_all_channels(bytes(img))[0]
        assert ch.freq_hz == 145_700_000
        assert ch.rx_tone_label == "100.0 Hz"


# ---------------------------------------------------------------------------
# CSV import: CHIRP-style tone columns → K5 V1 channel tones
# ---------------------------------------------------------------------------

def test_csv_import_picks_up_chirp_tone_columns(tmp_path):
    import csv
    from pathlib import Path
    from quansheng_toolkit.kradio import workflow as wf

    csv_path = tmp_path / "channels.csv"
    with csv_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "Location", "Name", "Frequency", "Duplex", "Offset",
            "Tone", "rToneFreq", "cToneFreq",
            "DtcsCode", "DtcsPolarity", "RxDtcsCode", "CrossMode",
            "Mode", "TStep", "Skip", "Power", "Comment",
        ])
        # CTCSS-only on TX (Tone)
        w.writerow([
            "1", "TWR-A", "118.300000", "", "",
            "Tone", "88.5", "88.5", "023", "NN", "023", "Tone->Tone",
            "AM", "25.00", "", "0.1W", "LISTA 1",
        ])
        # CTCSS both ways (TSQL)
        w.writerow([
            "2", "REP-V", "145.500000", "", "",
            "TSQL", "100.0", "88.5", "023", "NN", "023", "Tone->Tone",
            "FM", "12.50", "", "0.5W", "LISTA 1",
        ])
        # DTCS on both with normal polarity
        w.writerow([
            "3", "DCS-V", "433.000000", "", "",
            "DTCS", "88.5", "88.5", "025", "NN", "025", "Tone->Tone",
            "FM", "12.50", "", "0.5W", "LISTA 1",
        ])

    img = bytearray(b"\xff" * 0x2000)
    res = wf.import_channels_from_csv(
        img, csv_path, derive_scanlist_from_comment=True,
        memory_module=k5v1,
    )
    assert res["updated"] == 3

    chs = k5v1.decode_all_channels(bytes(img))
    # Tone-only on TX: TX has 88.5, RX is OFF
    assert chs[0].tx_tone_label == "88.5 Hz"
    assert chs[0].rx_tone_label == ""
    # TSQL: both directions carry CTCSS (cToneFreq for RX, rToneFreq for TX)
    assert chs[1].tx_tone_label == "100.0 Hz"
    assert chs[1].rx_tone_label == "88.5 Hz"
    # DTCS NN: both sides DTCS, normal polarity
    assert chs[2].tx_tone_label == "D025N"
    assert chs[2].rx_tone_label == "D025N"
