"""
Calibration view: dedicated tab for calibration dump / restore.

The calibration region holds the radio's TX power tables, RX sensitivity
trim and frequency offsets — the trim values that make THIS radio behave
like a calibrated unit. Address range and size are profile-dependent:

* F4HWN Fusion 5.x: 0xB000..0xB190     (400 bytes)
* K5 V1 / K1 / K6 stock: 0x1E00..0x2000 (512 bytes)
* Older 768-byte dumps with 256 B of 0xFF padding are also accepted on
  restore (the padding is stripped before write).

Restoring the wrong dump can leave the radio audibly broken, so the
view goes through TWO confirmation dialogs and a file-size sanity
check before emitting `restore_calibration_requested`.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..icons import svg_icon
from ..state import AppState
from ..theme import Palette
from ..widgets import Card, PageHeader


class CalibrationView(QWidget):
    """Dedicated page for calibration backup + restore."""

    dump_calibration_requested = Signal()
    restore_calibration_requested = Signal(str)   # path to calibration .bin
    verify_dump_requested = Signal(str)           # path to .bin to verify against
    compare_dumps_requested = Signal(str, str)    # (path_a, path_b)

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setObjectName("ContentRoot")
        self._last_dump_path: str | None = None

        self._build_ui()
        self.state.eeprom_loaded.connect(self._refresh_status)
        self._refresh_status()

    # ---------------------------------------------------------------- UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        root.addWidget(PageHeader(
            "Calibration",
            "Backup and restore the radio's per-unit RF trim. Dump first, "
            "always; restore only from a dump of the SAME radio.",
        ))

        # --- Status card ----------------------------------------------------
        status_card = Card()
        status_layout = QVBoxLayout(status_card)
        status_layout.setContentsMargins(20, 16, 20, 16)
        status_layout.setSpacing(8)

        status_title = QLabel("CURRENT RADIO")
        status_title.setObjectName("CardTitle")
        status_layout.addWidget(status_title)

        self.profile_label = QLabel("—")
        self.profile_label.setStyleSheet("font-size: 14px; font-weight: 500;")
        status_layout.addWidget(self.profile_label)

        self.region_label = QLabel("—")
        self.region_label.setStyleSheet("color: #a6adc8; font-size: 13px;")
        status_layout.addWidget(self.region_label)

        root.addWidget(status_card)

        # --- Action grid: Dump | Restore ------------------------------------
        actions = QGridLayout()
        actions.setHorizontalSpacing(14)
        actions.setVerticalSpacing(0)

        # ---- Dump card -----------------------------------------------------
        dump_box = QGroupBox("DUMP — safe, read-only")
        dump_layout = QVBoxLayout(dump_box)
        dump_layout.setSpacing(10)

        dump_text = QLabel(
            "Reads the calibration region into a local .bin file. Always "
            "do this BEFORE flashing custom firmware or restoring from a "
            "dump — gives you a way back if anything goes wrong."
        )
        dump_text.setWordWrap(True)
        dump_text.setStyleSheet("color: #a6adc8; font-size: 13px;")
        dump_layout.addWidget(dump_text)

        dump_btn_row = QHBoxLayout()
        self.dump_btn = QPushButton(" Dump calibration")
        self.dump_btn.setObjectName("PrimaryBtn")
        self.dump_btn.setIconSize(QSize(16, 16))
        self.dump_btn.clicked.connect(self._on_dump)
        dump_btn_row.addWidget(self.dump_btn)
        dump_btn_row.addStretch()
        dump_layout.addLayout(dump_btn_row)

        actions.addWidget(dump_box, 0, 0)

        # ---- Restore card --------------------------------------------------
        restore_box = QGroupBox("RESTORE — DANGEROUS, writes to flash")
        restore_layout = QVBoxLayout(restore_box)
        restore_layout.setSpacing(10)

        restore_text = QLabel(
            "Writes a previous dump back to the calibration region. "
            "Use ONLY a dump taken from this exact radio. A wrong dump can "
            "leave the radio audibly broken (TX power, RX sensitivity and "
            "frequency offsets all drift). Two confirmation dialogs gate "
            "the actual write."
        )
        restore_text.setWordWrap(True)
        restore_text.setStyleSheet("color: #fab387; font-size: 13px;")
        restore_layout.addWidget(restore_text)

        restore_btn_row = QHBoxLayout()
        self.restore_btn = QPushButton(" Restore calibration…")
        self.restore_btn.setObjectName("DangerBtn")
        self.restore_btn.setIconSize(QSize(16, 16))
        self.restore_btn.clicked.connect(self._on_restore)
        restore_btn_row.addWidget(self.restore_btn)
        restore_btn_row.addStretch()
        restore_layout.addLayout(restore_btn_row)

        actions.addWidget(restore_box, 0, 1)

        # ---- Verify card ---------------------------------------------------
        # Re-reads calibration from the radio and compares it byte-for-byte
        # against a reference dump (defaults to the most recent one taken
        # this session). Identical = the saved dump is a stable, trustworthy
        # backup. Differs = the link is unstable or the dump is stale —
        # either way, do not trust it for a future restore.
        verify_box = QGroupBox("VERIFY — safe, read-only")
        verify_layout = QVBoxLayout(verify_box)
        verify_layout.setSpacing(10)

        verify_text = QLabel(
            "Re-reads the calibration region from the radio and compares it "
            "to a saved .bin (defaults to the dump you just took). If they "
            "match byte-for-byte, your backup is trustworthy. If they "
            "differ, either the link is flaky or the dump is from a "
            "different radio — don't restore from it."
        )
        verify_text.setWordWrap(True)
        verify_text.setStyleSheet("color: #a6adc8; font-size: 13px;")
        verify_layout.addWidget(verify_text)

        verify_btn_row = QHBoxLayout()
        self.verify_btn = QPushButton(" Verify dump…")
        self.verify_btn.setObjectName("SecondaryBtn")
        self.verify_btn.setIconSize(QSize(16, 16))
        self.verify_btn.clicked.connect(self._on_verify)
        verify_btn_row.addWidget(self.verify_btn)
        verify_btn_row.addStretch()
        verify_layout.addLayout(verify_btn_row)

        actions.addWidget(verify_box, 1, 0)

        # ---- Compare card --------------------------------------------------
        compare_box = QGroupBox("COMPARE — diagnostic only")
        compare_layout = QVBoxLayout(compare_box)
        compare_layout.setSpacing(10)

        compare_text = QLabel(
            "Diff two calibration .bin files byte-by-byte. Useful for "
            "auditing what a restore would change before you commit to it, "
            "without touching the radio."
        )
        compare_text.setWordWrap(True)
        compare_text.setStyleSheet("color: #a6adc8; font-size: 13px;")
        compare_layout.addWidget(compare_text)

        compare_btn_row = QHBoxLayout()
        self.compare_btn = QPushButton(" Compare two dumps…")
        self.compare_btn.setObjectName("SecondaryBtn")
        self.compare_btn.setIconSize(QSize(16, 16))
        self.compare_btn.clicked.connect(self._on_compare)
        compare_btn_row.addWidget(self.compare_btn)
        compare_btn_row.addStretch()
        compare_layout.addLayout(compare_btn_row)

        actions.addWidget(compare_box, 1, 1)

        wrap = QWidget()
        wrap.setLayout(actions)
        root.addWidget(wrap)
        root.addStretch()

    def refresh_icons(self, palette: Palette) -> None:
        primary_text = "#ffffff" if palette.name == "light" else palette.base
        self.dump_btn.setIcon(svg_icon("download", primary_text, 16))
        self.restore_btn.setIcon(svg_icon("alert", "#1e1e2e", 16))
        self.verify_btn.setIcon(svg_icon("refresh", palette.text, 16))
        self.compare_btn.setIcon(svg_icon("layers", palette.text, 16))

    # ---------------------------------------------------------- Status sync

    def _refresh_status(self) -> None:
        """Update the profile + region labels based on the active profile."""
        profile = self.state.profile if self.state.has_image else None
        if profile is None:
            self.profile_label.setText("No EEPROM loaded")
            self.region_label.setText(
                "Read from the radio first (Dashboard tab)."
            )
            self.dump_btn.setEnabled(False)
            self.restore_btn.setEnabled(False)
            self.verify_btn.setEnabled(False)
            return

        self.profile_label.setText(profile.name)
        cal_start = getattr(profile, "cal_start", None)
        cal_end = getattr(profile, "cal_end", None)
        if cal_start is not None and cal_end is not None:
            length = cal_end - cal_start
            self.region_label.setText(
                f"Calibration region: 0x{cal_start:04X} – 0x{cal_end:04X}  "
                f"({length} bytes)"
            )
        else:
            self.region_label.setText("Calibration region not declared "
                                       "for this profile.")
        self.dump_btn.setEnabled(True)
        self.restore_btn.setEnabled(True)
        self.verify_btn.setEnabled(True)

    # ------------------------------------------------------------- Handlers

    def _on_dump(self) -> None:
        self.dump_calibration_requested.emit()

    def _on_restore(self) -> None:
        ans = QMessageBox.warning(
            self,
            "Restore calibration — DANGEROUS",
            "Restoring the calibration region writes the radio's TX power "
            "and RX sensitivity tables. The exact byte count is profile-"
            "dependent (400 B on F4HWN, 512 B on K5 V1 / K1 stock).\n\n"
            "Use ONLY a calibration dump taken from THIS specific radio. A "
            "wrong restore can leave the radio audibly broken.\n\nProceed?",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        path, _ = QFileDialog.getOpenFileName(
            self, "Pick calibration dump", "", "Calibration dump (*.bin *.dat)"
        )
        if not path:
            return

        try:
            sz = Path(path).stat().st_size
        except OSError as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        if sz not in (0x190, 0x200, 0x300):
            QMessageBox.critical(
                self, "Wrong file size",
                f"Calibration dump must be 400 bytes (F4HWN, 0x190) or "
                f"512 bytes (stock K5 V1/K1, 0x200). 768-byte (0x300) "
                f"dumps from older builds are also accepted (the leading "
                f"256-byte 0xFF padding is stripped on write). "
                f"This file is {sz} bytes."
            )
            return

        # Final confirmation
        ans2 = QMessageBox.warning(
            self,
            "Last chance",
            f"About to write {sz} bytes from\n  {path}\n"
            f"to the radio's calibration region.\n\nReally proceed?",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if ans2 != QMessageBox.StandardButton.Yes:
            return
        self.restore_calibration_requested.emit(path)

    def set_last_dump_path(self, path: str) -> None:
        """Remember the most recent successful dump so Verify can default
        to it. Called by MainWindow after `_on_calibration_dumped` writes
        the file."""
        self._last_dump_path = path

    def _on_verify(self) -> None:
        default_dir = ""
        if self._last_dump_path:
            default_dir = self._last_dump_path
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick the dump to verify", default_dir,
            "Calibration dump (*.bin *.dat)"
        )
        if not path:
            return
        try:
            sz = Path(path).stat().st_size
        except OSError as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        if sz not in (0x190, 0x200, 0x300):
            QMessageBox.critical(
                self, "Wrong file size",
                f"Selected file is {sz} bytes, which doesn't match any "
                f"known calibration layout (400 / 512 / 768)."
            )
            return
        self.verify_dump_requested.emit(path)

    def _on_compare(self) -> None:
        path_a, _ = QFileDialog.getOpenFileName(
            self, "First calibration dump", "",
            "Calibration dump (*.bin *.dat)"
        )
        if not path_a:
            return
        path_b, _ = QFileDialog.getOpenFileName(
            self, "Second calibration dump", "",
            "Calibration dump (*.bin *.dat)"
        )
        if not path_b:
            return
        try:
            a = Path(path_a).read_bytes()
            b = Path(path_b).read_bytes()
        except OSError as e:
            QMessageBox.critical(self, "Error", str(e))
            return

        if len(a) != len(b):
            QMessageBox.warning(
                self, "Sizes differ",
                f"  A: {len(a)} bytes\n"
                f"  B: {len(b)} bytes\n\n"
                f"Comparing only the common prefix ({min(len(a), len(b))} bytes)."
            )

        n = min(len(a), len(b))
        diffs = sum(1 for i in range(n) if a[i] != b[i])
        first = next((i for i in range(n) if a[i] != b[i]), None)
        identical = (a == b)

        if identical:
            QMessageBox.information(
                self, "Identical",
                f"The two dumps are byte-for-byte identical "
                f"({len(a)} bytes)."
            )
        else:
            first_str = f"first diff at byte 0x{first:04X}" if first is not None else "no diffs in common prefix"
            QMessageBox.information(
                self, "Diff summary",
                f"Files differ.\n\n"
                f"  Common bytes: {n}\n"
                f"  Diff bytes  : {diffs}\n"
                f"  {first_str}\n"
                f"  Match ratio : {100 * (1 - diffs / max(1, n)):.2f}%"
            )
        # Future: emit compare_dumps_requested(a, b) so an external viewer
        # can render a hex side-by-side. Today the popup summary is enough.
