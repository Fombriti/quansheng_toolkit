"""Tests for kradio.firmware_bundle — loader for bundled F4HWN firmwares."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from quansheng_toolkit.kradio import firmware_bundle as fb


# ---------------------------------------------------------------------------
# Smoke tests against the actual repo bundle (validates reality of `firmwares/`)
# ---------------------------------------------------------------------------

def test_real_manifest_loads_at_least_one_entry():
    """The repo always ships at least one F4HWN firmware. If this test
    fails, someone removed the bundle entirely — confirm that's the
    intent before shipping.
    """
    entries = fb.load_manifest()
    assert len(entries) >= 1


def test_real_manifest_has_a_recommended_entry_per_target():
    """For each target where we ship at least one *open-source* (F4HWN)
    firmware, exactly one entry must be marked recommended so the GUI
    has a sensible default. Vendor-stock entries (Quansheng) are
    intentionally never recommended — users should reach for F4HWN
    first and pick stock only deliberately.
    """
    entries = fb.load_manifest()
    targets = {e.target for e in entries
               if not e.vendor.startswith("Quansheng")}
    for target in targets:
        recommended = [
            e for e in entries
            if e.target == target and e.is_recommended
            and not e.vendor.startswith("Quansheng")
        ]
        assert recommended, (
            f"target {target!r} has no recommended F4HWN firmware — at "
            f"least one open-source entry per target should set "
            f"is_recommended: true"
        )


def test_real_manifest_files_exist_and_match_size():
    for entry in fb.load_manifest():
        assert entry.path.is_file(), f"{entry.filename} missing"
        actual = entry.path.stat().st_size
        assert actual == entry.size_bytes, (
            f"{entry.filename}: manifest size {entry.size_bytes} ≠ "
            f"actual {actual}"
        )


def test_real_manifest_sha256_matches_file():
    import hashlib
    for entry in fb.load_manifest():
        actual = hashlib.sha256(entry.path.read_bytes()).hexdigest()
        assert actual == entry.sha256, (
            f"{entry.filename}: manifest sha256 doesn't match file"
        )


# ---------------------------------------------------------------------------
# Synthetic manifest tests — fault tolerance
# ---------------------------------------------------------------------------

def _write_manifest(tmp_path: Path, entries: list[dict]) -> None:
    """Helper: write a fake manifest + matching .bin stubs to `tmp_path`."""
    bundle = tmp_path / "firmwares"
    bundle.mkdir()
    for e in entries:
        if "filename" in e:
            (bundle / e["filename"]).write_bytes(b"\x00" * e.get("size_bytes", 16))
    (bundle / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "firmwares": entries,
    }))


def test_missing_manifest_returns_empty(tmp_path, monkeypatch):
    """If the bundle directory is missing entirely the loader returns []."""
    monkeypatch.setattr(fb, "MANIFEST_PATH", tmp_path / "nonexistent.json")
    monkeypatch.setattr(fb, "BUNDLE_DIR", tmp_path / "nonexistent")
    assert fb.load_manifest() == []


def test_corrupt_manifest_returns_empty(tmp_path, monkeypatch):
    bad = tmp_path / "manifest.json"
    bad.write_text("{ this is not json")
    monkeypatch.setattr(fb, "MANIFEST_PATH", bad)
    monkeypatch.setattr(fb, "BUNDLE_DIR", tmp_path)
    assert fb.load_manifest() == []


def test_entry_with_missing_bin_is_skipped(tmp_path, monkeypatch):
    bundle = tmp_path / "fw"
    bundle.mkdir()
    (bundle / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "firmwares": [
            # This one's .bin doesn't exist — should be skipped.
            {
                "id": "ghost", "name": "ghost", "version": "1.0",
                "filename": "ghost.bin", "target": "k1",
                "mcu": "PY32F071",
            },
        ],
    }))
    monkeypatch.setattr(fb, "MANIFEST_PATH", bundle / "manifest.json")
    monkeypatch.setattr(fb, "BUNDLE_DIR", bundle)
    assert fb.load_manifest() == []


def test_entry_with_bad_keys_is_skipped(tmp_path, monkeypatch):
    bundle = tmp_path / "fw"
    bundle.mkdir()
    (bundle / "ok.bin").write_bytes(b"x" * 100)
    (bundle / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "firmwares": [
            # Missing required key 'id' — skipped.
            {"name": "broken", "filename": "ok.bin", "target": "k1"},
            # Valid entry alongside.
            {
                "id": "ok_one", "name": "ok one", "version": "1.0",
                "filename": "ok.bin", "target": "k1",
                "mcu": "PY32F071", "compatible_targets": ["k1"],
                "size_bytes": 100, "sha256": "abc",
            },
        ],
    }))
    monkeypatch.setattr(fb, "MANIFEST_PATH", bundle / "manifest.json")
    monkeypatch.setattr(fb, "BUNDLE_DIR", bundle)
    out = fb.load_manifest()
    assert len(out) == 1
    assert out[0].id == "ok_one"


def test_recommended_entry_sorted_first_within_target(tmp_path, monkeypatch):
    bundle = tmp_path / "fw"
    bundle.mkdir()
    for f in ("a.bin", "b.bin"):
        (bundle / f).write_bytes(b"x" * 16)
    (bundle / "manifest.json").write_text(json.dumps({
        "schema_version": 1,
        "firmwares": [
            {"id": "old", "name": "old", "version": "4.0", "filename": "a.bin",
             "target": "k1", "mcu": "PY32F071", "is_recommended": False,
             "compatible_targets": ["k1"]},
            {"id": "new", "name": "new", "version": "5.0", "filename": "b.bin",
             "target": "k1", "mcu": "PY32F071", "is_recommended": True,
             "compatible_targets": ["k1"]},
        ],
    }))
    monkeypatch.setattr(fb, "MANIFEST_PATH", bundle / "manifest.json")
    monkeypatch.setattr(fb, "BUNDLE_DIR", bundle)
    out = fb.load_manifest()
    assert out[0].id == "new"   # recommended first


# ---------------------------------------------------------------------------
# find_by_id
# ---------------------------------------------------------------------------

def test_find_by_id_returns_entry():
    entries = fb.load_manifest()
    if not entries:
        pytest.skip("no bundled firmwares — covered by separate test")
    first = entries[0]
    found = fb.find_by_id(first.id)
    assert found is not None
    assert found.filename == first.filename


def test_find_by_id_returns_none_for_unknown():
    assert fb.find_by_id("definitely_not_a_real_firmware_id") is None


# ---------------------------------------------------------------------------
# display_label — the GUI uses this for the combo box
# ---------------------------------------------------------------------------

def test_display_label_marks_recommended():
    fw = fb.BundledFirmware(
        id="x", name="X", version="1", filename="x.bin",
        path=Path("/tmp/x.bin"), target="k1",
        compatible_targets=("k1",), mcu="PY32F071",
        supports=(), tested_on=(),
        release_url="", license="Apache-2.0",
        is_recommended=True, size_bytes=0, sha256="",
    )
    assert fw.display_label.startswith("★ ")


def test_targets_for_profile_name_known():
    assert "k5_v3" in fb.targets_for_profile_name("F4HWN Fusion 5.x")
    assert "k1" in fb.targets_for_profile_name("F4HWN Fusion 5.x")
    assert fb.targets_for_profile_name("UV-K5 stock (DP32G030)") == {"k5_k6"}
    assert fb.targets_for_profile_name("UV-K1 stock (PY32F071)") == {"k1"}


def test_targets_for_profile_name_unknown():
    assert fb.targets_for_profile_name("Future Custom Firmware Z") == set()


def test_filter_for_target_passthrough_when_none():
    entries = fb.load_manifest()
    if not entries:
        pytest.skip("no bundled firmwares")
    assert fb.filter_for_target(entries, None) == entries


def test_filter_for_target_keeps_only_compatible():
    entries = fb.load_manifest()
    if not entries:
        pytest.skip("no bundled firmwares")
    k5_k6_entries = fb.filter_for_target(entries, "k5_k6")
    assert all("k5_k6" in e.compatible_targets for e in k5_k6_entries)
    assert all("k5_k6" not in e.compatible_targets
               for e in entries if e not in k5_k6_entries)


def test_filter_for_target_unknown_target_returns_empty():
    entries = fb.load_manifest()
    if not entries:
        pytest.skip("no bundled firmwares")
    assert fb.filter_for_target(entries, "definitely_not_a_real_target") == []


def test_friendly_target_label_known_targets():
    assert "K1(8) v3" in fb.friendly_target_label("k5_v3")
    assert "UV-K1" in fb.friendly_target_label("k1")
    assert "DP32G030" in fb.friendly_target_label("k5_k6")


def test_friendly_target_label_unknown_falls_back_to_id():
    # If a new target lands in the allowlist before a friendly label
    # is added, the user just sees the raw id (acceptable degradation).
    assert fb.friendly_target_label("k5_v9") == "k5_v9"


def test_display_label_includes_name_and_vendor_tag():
    fw = fb.BundledFirmware(
        id="x", name="MyFW", version="1", filename="x.bin",
        path=Path("/tmp/x.bin"), target="k5_v3",
        compatible_targets=("k5_v3",), mcu="PY32F071",
        supports=(), tested_on=(),
        release_url="", license="Apache-2.0",
        is_recommended=False, size_bytes=0, sha256="",
        vendor="F4HWN (open source)",
    )
    label = fw.display_label
    assert "MyFW" in label
    assert "[F4HWN]" in label   # vendor tag rendered short


def test_display_label_marks_quansheng_stock():
    fw = fb.BundledFirmware(
        id="s", name="Stock", version="7", filename="s.bin",
        path=Path("/tmp/s.bin"), target="k1",
        compatible_targets=("k1",), mcu="PY32F071",
        supports=(), tested_on=(),
        release_url="", license="proprietary",
        is_recommended=False, size_bytes=0, sha256="",
        vendor="Quansheng proprietary",
    )
    assert "[Quansheng stock]" in fw.display_label
