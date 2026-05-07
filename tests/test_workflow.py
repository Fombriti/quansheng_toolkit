"""Tests for the high-level workflow helpers (offline, no radio)."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from quansheng_toolkit.kradio import memory as mem
from quansheng_toolkit.kradio import memory_uvk5_v1 as mm_k1
from quansheng_toolkit.kradio import protocol as proto
from quansheng_toolkit.kradio import workflow as wf


class TestPatchScanlistByte:
    def test_in_range(self, sample_eeprom):
        addr = wf.patch_scanlist_byte(sample_eeprom, 16, 5)
        assert addr == mem.addr_scanlist_byte(16)
        assert sample_eeprom[addr] == 5

    def test_out_of_range_index(self, sample_eeprom):
        with pytest.raises(ValueError):
            wf.patch_scanlist_byte(sample_eeprom, mem.NUM_CHANNELS, 1)

    def test_invalid_value(self, sample_eeprom):
        with pytest.raises(ValueError):
            wf.patch_scanlist_byte(sample_eeprom, 0, 99)

    # Profile-aware path: K5 V1 stock packs scanlist into compander/band byte.
    def test_k5v1_preserves_other_bits(self, k1_stock_eeprom):
        addr = mm_k1.addr_scanlist_byte(2)
        # The fixture put MAR-16 at slot 2 with band=2 (VHF marine).
        before = k1_stock_eeprom[addr]
        wf.patch_scanlist_byte(k1_stock_eeprom, 2, mm_k1.SCAN_OFF,
                               memory_module=mm_k1)
        after = k1_stock_eeprom[addr]
        # Top 2 bits go to 00 (=OFF); low 6 bits (compander/free/band)
        # must be unchanged.
        assert (after & 0xC0) == 0
        assert (after & 0x3F) == (before & 0x3F)


class TestPatchSessionState:
    def test_changes_mr_channel_a(self, sample_eeprom):
        wf.patch_session_state(sample_eeprom, mr_channel_a=42)
        # 0xA012 = MrChannel_A (LE u16)
        assert int.from_bytes(sample_eeprom[0xA012:0xA014], "little") == 42


class TestImportChannelsFromCsv:
    def test_basic_import(self, sample_eeprom, tmp_path: Path):
        csv_path = tmp_path / "channels.csv"
        with csv_path.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "Location", "Name", "Frequency", "Duplex", "Offset",
                "Tone", "rToneFreq", "cToneFreq",
                "DtcsCode", "DtcsPolarity", "RxDtcsCode", "CrossMode",
                "Mode", "TStep", "Skip", "Power", "Comment",
            ])
            # Channel 600 = TEST FM at 145.5 MHz, list 7
            w.writerow([
                "600", "TEST_A", "145.500000", "", "",
                "", "88.5", "88.5", "023", "NN", "023", "Tone->Tone",
                "FM", "12.50", "", "0.5W", "LISTA 7 - test",
            ])
            # Channel 601 = TEST AM at 121 MHz, list 8
            w.writerow([
                "601", "TEST_B", "121.500000", "", "",
                "", "88.5", "88.5", "023", "NN", "023", "Tone->Tone",
                "AM", "25.00", "", "0.1W", "LISTA 8 - test",
            ])

        result = wf.import_channels_from_csv(
            sample_eeprom, csv_path, derive_scanlist_from_comment=True,
        )
        assert result["updated"] == 2
        assert len(result["skipped"]) == 0

        decoded = mem.decode_all_channels(bytes(sample_eeprom))
        assert decoded[599].name == "TEST_A"
        assert decoded[599].freq_mhz == 145.5
        assert decoded[599].mode == "FM"
        assert decoded[599].scanlist == 7

        assert decoded[600].name == "TEST_B"
        assert decoded[600].freq_mhz == 121.5
        assert decoded[600].mode == "AM"
        assert decoded[600].scanlist == 8


# ---------------------------------------------------------------------------
# upload_eeprom_chirp_style — verify-and-retry loop
# ---------------------------------------------------------------------------

class _FakeRadioState:
    """In-memory stand-in for the radio's EEPROM during upload tests.

    Simulates the radio side of `proto.write_mem` and
    `proto.read_block_chunked`. Optional `drop_offsets` set lets a test
    pretend specific 64-byte block writes were silently rejected (the
    real bug observed on F4HWN 5.4).
    """

    def __init__(self, size: int):
        self.bytes_ = bytearray(b"\xff" * size)
        self.write_log: list[int] = []
        self.drop_offsets: set[int] = set()
        self.dropped_log: list[int] = []

    def write_mem(self, _rp, offset: int, data: bytes) -> bool:
        self.write_log.append(offset)
        if offset in self.drop_offsets:
            # Pretend the radio ACKed but didn't persist — drop only on
            # the first attempt so retries succeed.
            self.dropped_log.append(offset)
            self.drop_offsets.discard(offset)
            return True
        end = offset + len(data)
        self.bytes_[offset:end] = data
        return True

    def read_block_chunked(self, _rp, offset: int, length: int,
                           progress_cb=None) -> bytes:
        if progress_cb:
            progress_cb(length, length)
        return bytes(self.bytes_[offset:offset + length])


class _FakePort:
    timeout = 0.0


class _FakeRP:
    """Minimal stand-in for `proto.RadioPort` (we only touch `.port.timeout`)."""
    port = _FakePort()


@pytest.fixture
def fake_radio(monkeypatch):
    state = _FakeRadioState(size=0x1D00)
    monkeypatch.setattr(proto, "write_mem", state.write_mem)
    monkeypatch.setattr(proto, "read_block_chunked", state.read_block_chunked)
    monkeypatch.setattr(proto, "reset_radio", lambda _rp: None)
    return state


@pytest.fixture
def fake_rp():
    return _FakeRP()


class TestUploadVerifyLoop:
    def test_clean_upload_passes_verify_first_try(self, fake_radio, fake_rp):
        image = bytes(range(256)) * (0x1D00 // 256)
        n = wf.upload_eeprom_chirp_style(
            fake_rp, image, prog_size=0x1D00, verify=True,
        )
        # Initial pass writes every block; with no drops, no retries.
        expected = (0x1D00 + proto.MEM_BLOCK - 1) // proto.MEM_BLOCK
        assert n == expected
        assert fake_radio.bytes_[:0x1D00] == image
        # write_log should equal exactly one write per block — no retries.
        assert len(fake_radio.write_log) == expected

    def test_one_dropped_block_is_retried_and_persists(self, fake_radio, fake_rp):
        image = bytes(range(256)) * (0x1D00 // 256)
        # Drop block at offset 0x02C0 (the real-world failure we hit on
        # K5 V3 / F4HWN 5.4 — channels 45-48).
        fake_radio.drop_offsets = {0x02C0}

        wf.upload_eeprom_chirp_style(
            fake_rp, image, prog_size=0x1D00, verify=True,
        )
        # Final state must match the source image exactly.
        assert fake_radio.bytes_[:0x1D00] == image
        # The dropped block must appear twice in the write log
        # (initial write + 1 retry).
        assert fake_radio.write_log.count(0x02C0) == 2
        assert fake_radio.dropped_log == [0x02C0]

    def test_persistent_failure_raises_after_retries(self, fake_radio, fake_rp, monkeypatch):
        image = bytes(range(256)) * (0x1D00 // 256)

        # Override write_mem so block 0x02C0 is ALWAYS silently dropped.
        original_write = fake_radio.write_mem

        def always_drop(rp, offset: int, data: bytes) -> bool:
            if offset == 0x02C0:
                fake_radio.dropped_log.append(offset)
                fake_radio.write_log.append(offset)
                return True  # ACK without persisting
            return original_write(rp, offset, data)

        monkeypatch.setattr(proto, "write_mem", always_drop)

        with pytest.raises(RuntimeError, match="verify failed"):
            wf.upload_eeprom_chirp_style(
                fake_rp, image, prog_size=0x1D00, verify=True,
                verify_retries=2,
            )
        # 1 initial attempt + 2 retries = 3 writes that all dropped.
        assert fake_radio.dropped_log.count(0x02C0) == 3

    def test_verify_disabled_skips_readback(self, fake_radio, fake_rp):
        image = bytes(range(256)) * (0x1D00 // 256)
        # Drop a block; without verify, the toolkit will not notice.
        fake_radio.drop_offsets = {0x02C0}
        wf.upload_eeprom_chirp_style(
            fake_rp, image, prog_size=0x1D00, verify=False,
        )
        # The dropped block was never recovered.
        assert fake_radio.bytes_[0x02C0:0x02C0 + proto.MEM_BLOCK] == \
            b"\xff" * proto.MEM_BLOCK
