"""
Loader for the bundled F4HWN firmwares that ship in `firmwares/`.

`manifest.json` next to the binaries lists every firmware we bundle
plus its target, MCU, recommendation flag and source URL. The GUI
reads this manifest at startup so the user can flash a known-good
firmware with one click instead of hunting download pages.

The bundle is optional — if `firmwares/` or `manifest.json` is
missing (e.g. someone pruned the directory in a custom build) the
loader returns an empty list and the GUI falls back to the file
picker.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path


def _resolve_bundle_dir() -> Path:
    """Find the firmwares/ directory at runtime.

    Two cases:

    1. **Source / pip install** — the package is on disk and
       `__file__` points at `quansheng_toolkit/kradio/firmware_bundle.py`,
       so the bundle is at `<package>/firmwares/`.
    2. **PyInstaller --onefile** — the executable extracts data files
       into a temp dir exposed as `sys._MEIPASS`. Our build pipeline
       passes `--add-data firmwares;firmwares`, which lands the bundle
       at `<MEIPASS>/firmwares/`.

    The PyInstaller branch wins when the attribute is set; otherwise we
    fall back to the package-relative path. This keeps `pip install -e`
    working in development unchanged.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "firmwares"
    # Source/pip path: <repo>/quansheng_toolkit/firmwares/  →
    # parent of kradio = quansheng_toolkit package root.
    return Path(__file__).resolve().parent.parent / "firmwares"


BUNDLE_DIR = _resolve_bundle_dir()
MANIFEST_PATH = BUNDLE_DIR / "manifest.json"


@dataclass(frozen=True)
class BundledFirmware:
    """One entry from `firmwares/manifest.json`, validated and resolved."""
    id: str
    name: str
    version: str
    filename: str
    path: Path
    target: str
    compatible_targets: tuple[str, ...]
    mcu: str
    supports: tuple[str, ...]
    tested_on: tuple[str, ...]
    release_url: str
    license: str
    is_recommended: bool
    size_bytes: int
    sha256: str
    vendor: str = "F4HWN (open source)"
    notes: str = ""

    @property
    def display_label(self) -> str:
        """Pretty label for the GUI combo box.

        Includes a [Quansheng] / [F4HWN] tag so vendor stock firmware is
        instantly distinguishable from open-source custom firmware in
        the dropdown.
        """
        star = "★ " if self.is_recommended else ""
        # Short vendor tag.
        if self.vendor.startswith("Quansheng"):
            tag = "[Quansheng stock]"
        elif "F4HWN" in self.vendor:
            tag = "[F4HWN]"
        else:
            tag = f"[{self.vendor}]"
        return f"{star}{self.name}  ·  {tag}"


def load_manifest() -> list[BundledFirmware]:
    """Read `firmwares/manifest.json` and return parsed entries.

    Missing manifest = empty list (no bundle = no dropdown). Entries
    whose `.bin` file is missing are silently skipped — the manifest
    might list a firmware that wasn't shipped in this build.
    """
    if not MANIFEST_PATH.is_file():
        return []
    try:
        text = MANIFEST_PATH.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, json.JSONDecodeError):
        return []
    raw_entries = data.get("firmwares", [])
    out: list[BundledFirmware] = []
    for raw in raw_entries:
        path = BUNDLE_DIR / raw.get("filename", "")
        if not path.is_file():
            continue
        try:
            out.append(BundledFirmware(
                id=str(raw["id"]),
                name=str(raw["name"]),
                version=str(raw.get("version", "")),
                filename=str(raw["filename"]),
                path=path,
                target=str(raw["target"]),
                compatible_targets=tuple(raw.get("compatible_targets", [raw["target"]])),
                mcu=str(raw.get("mcu", "")),
                supports=tuple(raw.get("supports", [])),
                tested_on=tuple(raw.get("tested_on", [])),
                release_url=str(raw.get("release_url", "")),
                license=str(raw.get("license", "")),
                is_recommended=bool(raw.get("is_recommended", False)),
                size_bytes=int(raw.get("size_bytes", path.stat().st_size)),
                sha256=str(raw.get("sha256", "")),
                vendor=str(raw.get("vendor", "F4HWN (open source)")),
                notes=str(raw.get("notes", "")),
            ))
        except (KeyError, ValueError, TypeError):
            # Bad entry — skip rather than break the dropdown for
            # everyone else.
            continue
    # Sort: recommended first within each target, then by version desc,
    # then by name. Stable so manifest order breaks ties.
    out.sort(key=lambda f: (
        0 if f.is_recommended else 1,
        f.target,
        # Descending version: split on dots, treat numeric-only parts as ints.
        tuple(-int(p) if p.isdigit() else 0 for p in f.version.split(".")),
        f.name,
    ))
    return out


def find_by_id(fw_id: str) -> BundledFirmware | None:
    """Lookup a single bundled firmware by its manifest id."""
    for fw in load_manifest():
        if fw.id == fw_id:
            return fw
    return None


# ---------------------------------------------------------------------------
# Profile/target compatibility helpers — used by the GUI to filter the
# bundled-firmware dropdown to "what can I actually flash on the radio I
# see right now?"
# ---------------------------------------------------------------------------

# Maps the human-facing radio profile name (RadioProfile.name) to the set
# of DFU flash targets that profile is compatible with. The profile
# itself doesn't carry MCU/target metadata explicitly, so we centralise
# the mapping here.
_PROFILE_NAME_TO_TARGETS = {
    "F4HWN Fusion 5.x": {"k5_v3", "k1"},
    "UV-K5 stock (DP32G030)": {"k5_k6"},
    "UV-K1 stock (PY32F071)": {"k1"},
}


def targets_for_profile_name(profile_name: str) -> set[str]:
    """Which DFU targets is this RadioProfile compatible with?

    F4HWN Fusion is the same binary across UV-K1 and UV-K5 V3 (shared
    PY32F071 MCU), so it maps to two targets. Stock K5 and stock K1
    each map to one. Unknown profile names fall back to the empty
    set (= no filtering).
    """
    return set(_PROFILE_NAME_TO_TARGETS.get(profile_name, set()))


def filter_for_target(entries: list[BundledFirmware], target: str | None
                       ) -> list[BundledFirmware]:
    """Return only entries compatible with `target`. None = passthrough.

    A firmware is "compatible" if `target` is in its `compatible_targets`
    tuple. The entry's primary `target` is always in that tuple.
    """
    if target is None:
        return list(entries)
    return [e for e in entries if target in e.compatible_targets]


# Human-readable labels for the internal flash-target names. The keys
# match the keys of `kradio.dfu.ALLOWED_BOOTLOADERS_BY_TARGET`. Used by
# the GUI to describe the target alongside the raw id, so end users
# see "UV-K5 V3 / UV-K1(8) v3" instead of "k5_v3".
_TARGET_FRIENDLY_LABELS = {
    "k5_k6":  "UV-K5 / UV-K5(8) / K6 / 5R+ (DP32G030)",
    "k5_v3":  "UV-K5 V3 / UV-K1(8) v3 Mini Kong (PY32F071, BL 7.00.07)",
    "k1":     "UV-K1 (PY32F071, BL 7.03.x)",
}


def friendly_target_label(target: str) -> str:
    """Return a human-readable description of a flash target id.

    Falls back to the raw id when we don't have a label for it (a new
    target added to the allowlist but not yet in the friendly map will
    just show as e.g. ``k5_v4`` until somebody adds the label).
    """
    return _TARGET_FRIENDLY_LABELS.get(target, target)
