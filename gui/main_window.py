"""
Main window: sidebar navigation + stacked content area + status bar +
Apply Changes / Save / Theme switcher.

Owns the AppState and the ThemeManager, propagates theme changes to all
child views via the ThemeManager.paletteChanged signal.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QStatusBar,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .icons import svg_icon
from .state import AppState
from .theme import ThemeManager, ThemeMode
from .prefs import Prefs
from .views.dashboard import DashboardView
from .views.calibration import CalibrationView
from .views.channels import ChannelsView
from .views.display_mirror import DisplayMirrorView
from .views.dtmf import DTMFView
from .views.firmware import FirmwareView
from .views.hex_view import HexViewerView
from .views.scan_lists import ScanListsView
from .views.settings_view import SettingsView
from .views.toolkit_settings import ToolkitSettingsView
from .widgets import ConnectionStatus, StatusDot
from .workers import (
    DfuIdentifyWorker,
    DisplayMirrorWorker,
    DumpCalibrationWorker,
    ReadEepromWorker,
    RestoreCalibrationWorker,
    UploadEepromWorker,
)


# (icon_name, label, key) for sidebar entries.
NAV_ITEMS = [
    ("dashboard", "Dashboard",       "dashboard"),
    ("channels",  "Channels",        "channels"),
    ("layers",    "Scan Lists",      "scan_lists"),
    ("settings",  "Radio Settings",  "settings"),
    ("send",      "DTMF",            "dtmf"),
    ("monitor",   "Display Mirror",  "display_mirror"),
    ("alert",     "Calibration",     "calibration"),
    ("send",      "Firmware",        "firmware"),
    ("download",  "Hex Viewer",      "hex_view"),
    ("settings",  "Toolkit",         "toolkit"),
]


class MainWindow(QMainWindow):
    def __init__(self, theme_manager: ThemeManager):
        super().__init__()
        self.setWindowTitle("Quansheng Toolkit")
        # Sensible minimum (so the user can shrink later) + start maximized.
        # The horizontal nav bar fits ~7 tabs at 1280; with 9 tabs we
        # need either the user's full screen or one of our compact label
        # abbreviations (see _short_tab_label).
        self.resize(1280, 820)
        self.setMinimumSize(1100, 680)
        self.setAcceptDrops(True)

        self.theme = theme_manager
        self.prefs = Prefs(self)
        self.state = AppState()
        self._busy = False
        self._read_worker: ReadEepromWorker | None = None
        self._upload_worker: UploadEepromWorker | None = None
        self._cal_worker: DumpCalibrationWorker | None = None
        self._mirror_worker = None  # DisplayMirrorWorker — owns the port while alive

        self._build_ui()
        self._wire()
        self._refresh_icons()
        self._update_apply_button(False)
        self._update_connection_indicator()

    # ---- Drag & drop .bin -------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                p = url.toLocalFile()
                if p and p.lower().endswith(".bin"):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if p and p.lower().endswith(".bin"):
                self._open_file(p)
                event.acceptProposedAction()
                return
        event.ignore()

    # ---- Layout -----------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- Top bar: brand + horizontal nav tabs + actions ----
        top = QWidget()
        top.setObjectName("TopBar")
        top.setFixedHeight(56)
        tl = QHBoxLayout(top)
        tl.setContentsMargins(0, 0, 14, 0)
        tl.setSpacing(0)

        # Brand on the left.
        self.brand_icon = QLabel()
        self.brand_icon.setFixedSize(28, 28)
        brand_label = QLabel(">>> QUANSHENG")
        brand_label.setObjectName("SidebarBrand")
        sub_label = QLabel("// TOOLKIT R1.0")
        sub_label.setObjectName("SidebarSubtitle")
        tl.addWidget(self.brand_icon)
        tl.addSpacing(6)
        tl.addWidget(brand_label)
        tl.addWidget(sub_label)
        tl.addStretch()

        # Nav tabs (horizontal).
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: list[QPushButton] = []
        for i, (icon_name, label, _key) in enumerate(NAV_ITEMS):
            btn = QPushButton(_short_tab_label(label))
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setProperty("icon_name", icon_name)
            btn.setToolTip(label)  # full name on hover; the button shows abbrev
            self.nav_group.addButton(btn, i)
            tl.addWidget(btn)
            self.nav_buttons.append(btn)

        tl.addStretch()

        # Firmware/profile badge on the right of the top bar.
        # Replaces the old Light/Dark/Auto theme switcher which was a
        # leftover from the studio layout. Theme picking now lives in
        # Toolkit → Appearance → Style preset.
        self.fw_badge = QLabel("◯ no radio")
        self.fw_badge.setObjectName("NavButton")
        self.fw_badge.setStyleSheet(
            "padding: 14px 18px; font-family: 'Cascadia Mono','Consolas',monospace;"
            " font-size: 12px; letter-spacing: 2px;"
        )
        tl.addWidget(self.fw_badge)

        root.addWidget(top)

        # ---- Right-side content ----
        right = QWidget()
        right.setObjectName("ContentRoot")
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        right_layout.addWidget(self._build_toolbar())

        self.stack = QStackedWidget()
        self.dashboard = DashboardView(self.state)
        self.channels_view = ChannelsView(self.state)
        self.scan_lists_view = ScanListsView(self.state)
        self.settings_view = SettingsView(self.state)
        self.dtmf_view = DTMFView(self.state)
        self.display_mirror_view = DisplayMirrorView(self.state)
        self.calibration_view = CalibrationView(self.state)
        self.firmware_view = FirmwareView(self.state)
        self.hex_view = HexViewerView(self.state, self.prefs)
        self.toolkit_view = ToolkitSettingsView(self.prefs, self.theme)
        self.stack.addWidget(_wrap_in_scroll(self.dashboard))
        self.stack.addWidget(self.channels_view)
        self.stack.addWidget(self.scan_lists_view)
        self.stack.addWidget(_wrap_in_scroll(self.settings_view))
        self.stack.addWidget(_wrap_in_scroll(self.dtmf_view))
        self.stack.addWidget(_wrap_in_scroll(self.display_mirror_view))
        self.stack.addWidget(_wrap_in_scroll(self.calibration_view))
        self.stack.addWidget(_wrap_in_scroll(self.firmware_view))
        self.stack.addWidget(self.hex_view)  # no scroll — it has its own
        self.stack.addWidget(_wrap_in_scroll(self.toolkit_view))
        right_layout.addWidget(self.stack, 1)

        root.addWidget(right, 1)

        # We removed the sidebar; the version footer is gone too.

        # ---- Status bar ----
        sb = QStatusBar()
        self.setStatusBar(sb)
        self.status_dot = StatusDot()
        self.status_left = QLabel("Disconnected")
        self.status_progress = QProgressBar()
        self.status_progress.setMaximumWidth(180)
        self.status_progress.setVisible(False)
        sb.addWidget(self.status_dot)
        sb.addWidget(self.status_left, 1)
        sb.addPermanentWidget(self.status_progress)

    def _build_toolbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("ContentRoot")
        bar.setFixedHeight(64)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(28, 14, 28, 14)
        layout.setSpacing(10)

        self.dirty_label = QLabel("")
        self.dirty_label.setStyleSheet(
            "color: palette(highlight); font-weight: 600; font-size: 13px;"
        )
        layout.addWidget(self.dirty_label)
        layout.addStretch()

        self.save_bin_btn = QPushButton(" Save .bin")
        self.save_bin_btn.setObjectName("SecondaryBtn")
        self.save_bin_btn.setIconSize(QSize(18, 18))
        self.save_bin_btn.clicked.connect(self._save_bin)
        layout.addWidget(self.save_bin_btn)

        self.apply_btn = QPushButton(" Apply Changes")
        self.apply_btn.setObjectName("PrimaryBtn")
        self.apply_btn.setIconSize(QSize(18, 18))
        self.apply_btn.clicked.connect(self._apply_changes)
        layout.addWidget(self.apply_btn)

        return bar

    # ---- Wiring -----------------------------------------------------------

    def _wire(self) -> None:
        self.nav_group.idClicked.connect(self.stack.setCurrentIndex)
        self.nav_buttons[0].setChecked(True)

        self.dashboard.read_requested.connect(self._read_eeprom)
        self.dashboard.open_file_requested.connect(self._open_file)
        self.state.dirty_changed.connect(self._update_apply_button)
        self.state.eeprom_loaded.connect(self._on_eeprom_loaded)
        self.state.eeprom_loaded.connect(self._update_connection_indicator)

        self.calibration_view.dump_calibration_requested.connect(self._dump_calibration)
        self.calibration_view.restore_calibration_requested.connect(
            self._restore_calibration
        )
        self.firmware_view.dfu_identify_requested.connect(self._dfu_identify)
        self.firmware_view.flash_firmware_requested.connect(self._flash_firmware)

        self.display_mirror_view.btn_start.clicked.connect(self._start_display_mirror)
        self.display_mirror_view.btn_stop.clicked.connect(self._stop_display_mirror)

        self.theme.paletteChanged.connect(self._refresh_icons)

    # ---- Theme-aware icon refresh ----------------------------------------

    def _refresh_icons(self) -> None:
        """Re-render every icon in the active palette's text colour."""
        p = self.theme.palette
        is_cockpit = p.style_kind == "cockpit"

        # Brand glyph: only meaningful in studio layout. In cockpit the
        # brand is text-only (>>> QUANSHENG) so we hide the radio glyph.
        if is_cockpit:
            self.brand_icon.setVisible(False)
        else:
            self.brand_icon.setVisible(True)
            self.brand_icon.setPixmap(svg_icon("radio", p.lavender, 28).pixmap(28, 28))

        # Nav buttons
        for btn in self.nav_buttons:
            name = btn.property("icon_name")
            if is_cockpit:
                btn.setIcon(QIcon())   # text-only tabs
                btn.setIconSize(QSize(0, 0))
            else:
                btn.setIcon(svg_icon(name, p.subtext0, 20))
                btn.setIconSize(QSize(20, 20))

        # Toolbar buttons
        if is_cockpit:
            self.save_bin_btn.setIcon(QIcon())
            self.save_bin_btn.setIconSize(QSize(0, 0))
            self.save_bin_btn.setText("SAVE .BIN")
            self.apply_btn.setIcon(QIcon())
            self.apply_btn.setIconSize(QSize(0, 0))
            self.apply_btn.setText("APPLY CHANGES >>")
        else:
            self.save_bin_btn.setIcon(svg_icon("save", p.text, 18))
            self.save_bin_btn.setText(" Save .bin")
            self.save_bin_btn.setIconSize(QSize(18, 18))
            primary_text_color = "#ffffff" if p.name == "light" else p.base
            self.apply_btn.setIcon(svg_icon("send", primary_text_color, 18))
            self.apply_btn.setText(" Apply Changes")
            self.apply_btn.setIconSize(QSize(18, 18))

        # Forward to views that own their own iconography
        self.dashboard.refresh_icons(p)
        if hasattr(self, "scan_lists_view"):
            self.scan_lists_view.refresh_icons(p)
        if hasattr(self, "calibration_view"):
            self.calibration_view.refresh_icons(p)
        if hasattr(self, "firmware_view"):
            self.firmware_view.refresh_icons(p)
        if hasattr(self, "toolkit_view"):
            self.toolkit_view.refresh_icons(p)
        # FW badge text depends on theme colours.
        self._refresh_fw_badge()

    # ---- Slots ------------------------------------------------------------

    def _on_eeprom_loaded(self) -> None:
        if self.state.firmware:
            self.status_left.setText(
                f"  Firmware: {self.state.firmware}      "
                f"Port: {self.state.port_name or '—'}      "
                f"EEPROM: {len(self.state.eeprom):,} bytes"
            )
        else:
            self.status_left.setText(
                f"  Loaded image: {len(self.state.eeprom):,} bytes"
            )
        self._refresh_fw_badge()
        # Loud warning if the radio reported a firmware we don't recognise.
        if (self.state.firmware
                and not self.state.profile_recognized
                and not getattr(self, "_unrecognized_warned", False)):
            self._unrecognized_warned = True
            QMessageBox.warning(
                self,
                "Unrecognized firmware",
                f"The radio reported firmware:\n  {self.state.firmware!r}\n\n"
                f"This string does not match any profile we know "
                f"(F4HWN Fusion 5.x, UV-K5 stock, UV-K1 stock).\n\n"
                f"The toolkit fell back to the F4HWN profile to decode the "
                f"EEPROM, but the channel/settings tables you see may be "
                f"GARBAGE. Apply Changes is automatically blocked.\n\n"
                f"If this is a known firmware (egzumer, NUNU, IJV…) we "
                f"haven't ported yet, please open an issue with the firmware "
                f"string + a CHIRP backup."
            )

    def _refresh_fw_badge(self) -> None:
        """Top-bar firmware indicator. Reflects the recognition state."""
        p = self.theme.palette
        if not hasattr(self, "fw_badge"):
            return
        if not self.state.has_image and not self.state.firmware:
            text = "◯ no radio"
            color = p.subtext0
        elif not self.state.firmware:
            text = "◉ image loaded"
            color = p.green
        elif self.state.profile_recognized and self.state.profile.verified:
            text = f"● {self.state.firmware}"
            color = p.green
        elif self.state.profile_recognized:
            text = f"⚠ {self.state.firmware} unverified"
            color = p.yellow
        else:
            text = f"✗ {self.state.firmware} unknown"
            color = p.red
        self.fw_badge.setText(text)
        self.fw_badge.setStyleSheet(
            f"padding: 14px 18px; "
            f"font-family: 'Cascadia Mono','Consolas',monospace; "
            f"font-size: 12px; letter-spacing: 2px; color: {color};"
        )

    def _update_apply_button(self, dirty: bool) -> None:
        self.apply_btn.setEnabled(dirty and not self._busy)
        self.dirty_label.setText("● Unsaved changes" if dirty else "")

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.apply_btn.setEnabled(self.state.dirty and not busy)
        self.dashboard.set_busy(busy)
        self.status_progress.setVisible(busy)
        if not busy:
            self.status_progress.setValue(0)
        self._update_connection_indicator()

    def _update_connection_indicator(self) -> None:
        if self._busy:
            self.status_dot.set_status(ConnectionStatus.BUSY)
        elif self.state.has_image:
            self.status_dot.set_status(ConnectionStatus.READY)
        else:
            self.status_dot.set_status(ConnectionStatus.DISCONNECTED)

    # ---- Read EEPROM ------------------------------------------------------

    def _read_eeprom(self, port: str) -> None:
        if self._busy:
            return
        if self.state.dirty:
            ans = QMessageBox.question(
                self, "Discard changes?",
                "You have unsaved changes. Reading the radio will replace "
                "the in-memory image with the radio's current contents.\n\n"
                "Discard your edits and continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return

        self.state.port_name = port or None
        self._set_busy(True)
        self.status_left.setText("  Reading EEPROM…")

        worker = ReadEepromWorker(self.state.port_name)
        worker.signals.progress.connect(self._on_read_progress)
        worker.signals.succeeded.connect(self._on_read_done)
        worker.signals.failed.connect(self._on_worker_failed)
        worker.signals.finished.connect(lambda: self._set_busy(False))
        self._read_worker = worker
        worker.start()

    def _on_read_progress(self, done: int, total: int) -> None:
        self.dashboard.update_progress(done, total)
        self.status_progress.setRange(0, total)
        self.status_progress.setValue(done)

    def _on_read_done(self, payload: dict) -> None:
        self.state.firmware = payload["firmware"]
        self.state.port_name = payload["port"]
        # The worker already resolved the profile from the firmware string
        # so the EEPROM was downloaded at the right size — reuse that here.
        self.state.set_profile_from_firmware(payload["firmware"])
        self.state.set_eeprom(payload["data"])

    def _open_file(self, path: str) -> None:
        try:
            data = Path(path).read_bytes()
        except OSError as e:
            QMessageBox.critical(self, "Open file failed", str(e))
            return
        self.state.firmware = ""
        self.state.set_eeprom(data)
        self.status_left.setText(f"  Loaded image from disk: {path}")

    def _save_bin(self) -> None:
        if not self.state.has_image:
            QMessageBox.information(self, "No image",
                                    "Read the EEPROM first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save EEPROM .bin", "eeprom.bin", "EEPROM image (*.bin)"
        )
        if not path:
            return
        try:
            Path(path).write_bytes(bytes(self.state.eeprom))
        except OSError as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return
        self.state.mark_clean()
        self.status_left.setText(f"  Saved image: {path}")

    # ---- Apply changes ----------------------------------------------------

    def _apply_changes(self) -> None:
        if not self.state.has_image or not self.state.dirty:
            return
        # Refuse to upload if the firmware was never recognised.
        if self.state.firmware and not self.state.profile_recognized:
            QMessageBox.warning(
                self,
                "Upload blocked — unrecognized firmware",
                f"Cannot upload to a radio whose firmware "
                f"({self.state.firmware!r}) does not match any profile "
                f"signature. The image you'd be writing was decoded with "
                f"the F4HWN profile by best-effort fallback and is almost "
                f"certainly the wrong layout for this radio.\n\n"
                f"Read-only operations (decode, dashboard, settings view) "
                f"remain available.",
            )
            return
        # Refuse to upload to a profile that hasn't been verified on hardware.
        if not self.state.profile.verified:
            QMessageBox.warning(
                self,
                "Upload disabled for this firmware profile",
                f"The detected profile is "
                f"\"{self.state.profile.name}\" which is currently marked "
                f"as experimental in this build of the toolkit. Uploads "
                f"are disabled to avoid bricking the radio.\n\n"
                f"Profile notes: {self.state.profile.notes}\n\n"
                f"You can still read EEPROM and inspect channels/settings "
                f"safely.",
            )
            return
        if self.prefs.confirm_apply or self.prefs.show_powercycle_hint:
            ans = QMessageBox.question(
                self,
                "Apply changes — power-cycle recommended",
                "About to upload the entire program region (~16 seconds, "
                "352 blocks of 128 bytes). The radio will "
                + ("reboot at the end" if self.prefs.reset_radio_after_apply
                   else "NOT be reset (changes still take effect on next "
                        "manual power-cycle)") + ".\n\n"
                "If this is your first upload of the day, it is safer to:\n"
                "  • power-cycle the radio first;\n"
                "  • briefly unplug and replug the USB cable.\n\n"
                "Continue with the upload now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return

        self._set_busy(True)
        self.status_left.setText("  Uploading EEPROM (CHIRP-style)…")
        worker = UploadEepromWorker(
            bytes(self.state.eeprom),
            self.state.port_name,
            reset_after=self.prefs.reset_radio_after_apply,
            prog_size=self.state.profile.prog_size,
        )
        worker.signals.progress.connect(self._on_upload_progress)
        worker.signals.succeeded.connect(self._on_upload_done)
        worker.signals.failed.connect(self._on_worker_failed)
        worker.signals.finished.connect(lambda: self._set_busy(False))
        self._upload_worker = worker
        worker.start()

    def _on_upload_progress(self, done: int, total: int) -> None:
        self.status_progress.setRange(0, total)
        self.status_progress.setValue(done)

    def _on_upload_done(self, payload: dict) -> None:
        self.state.mark_clean()
        self.status_left.setText(
            f"  Upload complete — {payload['blocks']} blocks written. "
            f"Radio is rebooting."
        )

    def _on_worker_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Radio error", message)
        self.status_left.setText(f"  Error: {message}")
        self.status_dot.set_status(ConnectionStatus.ERROR)

    # ---- Calibration dump -------------------------------------------------

    def _calibration_region_for_active(self) -> tuple[int, int] | None:
        """The (start, end) of the calibration region for the active profile,
        or None when no EEPROM has been read yet."""
        prof = self.state.profile if self.state.has_image else None
        if prof is None:
            return None
        return (prof.cal_start, prof.mem_size)

    def _confirm_k5v1_experimental(self, *, action: str) -> bool:
        """
        Calibration round-trip on the 8 KB stock family was verified on a
        UV-K5 V3 stock (md5-identical dump → restore → dump on bootloader
        7.00.07 / firmware 7.00.11). Both UVK5_STOCK and UVK1_STOCK use
        the same protocol path, so no extra confirmation is needed for
        them today. Kept as a hook in case a future profile lands
        without round-trip verification.
        """
        return True

    def _dump_calibration(self) -> None:
        if self._busy:
            return
        region = self._calibration_region_for_active()
        if region is None:
            QMessageBox.warning(
                self, "Read EEPROM first",
                "Read the EEPROM (Dashboard → Read EEPROM) before dumping "
                "calibration so the toolkit knows which radio is connected.",
            )
            return
        port = self.prefs.default_port or self.state.port_name or None
        self._set_busy(True)
        self.status_left.setText(
            f"  Dumping calibration region "
            f"(0x{region[0]:04X}..0x{region[1]:04X})…"
        )
        worker = DumpCalibrationWorker(port)
        worker.signals.progress.connect(self._on_read_progress)
        worker.signals.succeeded.connect(self._on_calibration_dumped)
        worker.signals.failed.connect(self._on_worker_failed)
        worker.signals.finished.connect(lambda: self._set_busy(False))
        self._cal_worker = worker
        worker.start()

    def _on_calibration_dumped(self, payload: dict) -> None:
        from datetime import datetime
        from pathlib import Path

        data = payload["data"]
        backup_dir = Path(self.prefs.backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = backup_dir / f"calibration_{ts}.bin"
        try:
            out.write_bytes(data)
        except OSError as e:
            QMessageBox.critical(self, "Save failed", str(e))
            return
        self.status_left.setText(
            f"  Calibration dumped ({len(data)} bytes) → {out}"
        )
        QMessageBox.information(
            self,
            "Calibration dumped",
            f"Saved {len(data)} bytes to:\n{out}\n\n"
            f"Keep this file safe — you'll need it if you ever have to "
            f"restore calibration."
        )

    # ---- Calibration restore ---------------------------------------------

    def _restore_calibration(self, path: str) -> None:
        if self._busy:
            return
        region = self._calibration_region_for_active()
        if region is None:
            QMessageBox.warning(
                self, "Read EEPROM first",
                "Read the EEPROM (Dashboard → Read EEPROM) before restoring "
                "calibration so the toolkit knows which radio is connected.",
            )
            return
        if not self._confirm_k5v1_experimental(action="restore"):
            return
        from pathlib import Path
        try:
            data = Path(path).read_bytes()
        except OSError as e:
            QMessageBox.critical(self, "Read failed", str(e))
            return
        expected = region[1] - region[0]
        if len(data) != expected:
            QMessageBox.critical(
                self, "Wrong calibration size",
                f"This radio ({self.state.profile.name}) expects "
                f"{expected} bytes of calibration but the file is "
                f"{len(data)} bytes. Wrong file or wrong radio profile."
            )
            return

        # Read current calibration first as a recovery checkpoint, BEFORE
        # writing anything. This is a quick read of just 400 bytes so it
        # doesn't cost much and gives the user a guaranteed rollback.
        port = self.prefs.default_port or self.state.port_name or None
        ans = QMessageBox.question(
            self,
            "Pre-write backup",
            "Take a fresh dump of the radio's CURRENT calibration before "
            "writing the new one? Strongly recommended — this is your "
            "rollback if anything goes wrong.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        self._pending_restore_data = data

        if ans == QMessageBox.StandardButton.Yes:
            self._set_busy(True)
            self.status_left.setText("  Pre-restore: dumping current calibration…")
            worker = DumpCalibrationWorker(port)
            worker.signals.progress.connect(self._on_read_progress)
            worker.signals.succeeded.connect(self._on_pre_restore_dumped)
            worker.signals.failed.connect(self._on_worker_failed)
            worker.signals.finished.connect(lambda: self._set_busy(False))
            self._cal_worker = worker
            worker.start()
        else:
            self._do_restore_calibration_now()

    def _on_pre_restore_dumped(self, payload: dict) -> None:
        # Save the rollback dump, then proceed with the restore.
        from datetime import datetime
        from pathlib import Path
        backup_dir = Path(self.prefs.backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = backup_dir / f"calibration_pre-restore_{ts}.bin"
        try:
            out.write_bytes(payload["data"])
        except OSError as e:
            QMessageBox.warning(self, "Backup save failed",
                                f"{e}\n\nProceeding with restore anyway.")
        else:
            self.status_left.setText(
                f"  Pre-restore backup saved: {out}"
            )
        # Slight delay then start the actual write.
        QTimer.singleShot(400, self._do_restore_calibration_now)

    def _do_restore_calibration_now(self) -> None:
        if not getattr(self, "_pending_restore_data", None):
            return
        port = self.prefs.default_port or self.state.port_name or None
        region = self._calibration_region_for_active()
        self._set_busy(True)
        if region:
            self.status_left.setText(
                f"  Restoring calibration "
                f"(writing 0x{region[0]:04X}..0x{region[1]:04X})…"
            )
        else:
            self.status_left.setText("  Restoring calibration…")
        try:
            worker = RestoreCalibrationWorker(
                self._pending_restore_data, port,
                reset_after=self.prefs.reset_radio_after_apply,
            )
        except ValueError as e:
            QMessageBox.critical(self, "Bad calibration file", str(e))
            self._set_busy(False)
            return
        worker.signals.progress.connect(self._on_upload_progress)
        worker.signals.succeeded.connect(self._on_calibration_restored)
        worker.signals.failed.connect(self._on_worker_failed)
        worker.signals.finished.connect(lambda: self._set_busy(False))
        self._cal_worker = worker
        worker.start()

    def _on_calibration_restored(self, payload: dict) -> None:
        self._pending_restore_data = None
        self.status_left.setText(
            f"  Calibration restored — {payload['blocks']} blocks. "
            f"Radio is rebooting."
        )
        region = payload.get("region")
        bytes_written = (region[1] - region[0]) if region else 0
        region_str = (
            f" to 0x{region[0]:04X}..0x{region[1]:04X}" if region else ""
        )
        QMessageBox.information(
            self,
            "Calibration restored",
            f"Wrote {payload['blocks']} blocks "
            f"({bytes_written} bytes){region_str}.\n"
            f"The radio is rebooting. Verify TX power and RX sensitivity now."
        )

    # ---- DFU identify (firmware bootloader) ------------------------------

    # ---- Display Mirror --------------------------------------------------

    def _start_display_mirror(self) -> None:
        if self._busy:
            QMessageBox.information(
                self, "Busy",
                "Another radio operation is in progress. Wait for it to "
                "finish before starting the display mirror."
            )
            return
        if self._mirror_worker is not None and self._mirror_worker.isRunning():
            return

        port = self.prefs.default_port or self.state.port_name or None
        worker = DisplayMirrorWorker(port_name=port)

        def _on_started() -> None:
            self._set_busy(True)
            self.display_mirror_view.set_running(
                True, "Connected — receiving frames…"
            )

        def _on_stopped() -> None:
            self._set_busy(False)
            self.display_mirror_view.set_running(False, "Mirror stopped.")
            self._mirror_worker = None

        def _on_failed(msg: str) -> None:
            self.display_mirror_view.set_running(False, f"Error: {msg}")

        worker.signals.started.connect(_on_started)
        worker.signals.stopped.connect(_on_stopped)
        worker.signals.failed.connect(_on_failed)
        worker.signals.frame.connect(self.display_mirror_view.update_framebuffer)

        self._mirror_worker = worker
        worker.start()
        self.display_mirror_view.set_running(True, "Connecting…")

    def _stop_display_mirror(self) -> None:
        if self._mirror_worker is None:
            return
        self._mirror_worker.request_stop()

    def _dfu_identify(self) -> None:
        if self._busy:
            return
        port = self.prefs.default_port or self.state.port_name or None
        self._set_busy(True)
        self.status_left.setText(
            "  DFU: looking for bootloader at 115200 baud…"
        )
        worker = DfuIdentifyWorker(port)
        worker.signals.succeeded.connect(self._on_dfu_identified)
        worker.signals.failed.connect(self._on_dfu_failed)
        worker.signals.finished.connect(lambda: self._set_busy(False))
        self._dfu_worker = worker
        worker.start()

    def _on_dfu_identified(self, payload: dict) -> None:
        port = payload["port"]
        bl_version = payload["bl_version"]
        model = payload["model"]
        uid = payload["uid"]
        self.status_left.setText(
            f"  DFU bootloader detected on {port}: {model} (v{bl_version})"
        )
        # Map bootloader → DFU flash target name (matches kradio.dfu's
        # ALLOWED_BOOTLOADERS_BY_TARGET keys). This filters the bundled
        # firmware dropdown to entries that are actually flashable on
        # this radio.
        from ..kradio import dfu
        target_for_bl = None
        for target, bls in dfu.ALLOWED_BOOTLOADERS_BY_TARGET.items():
            if bl_version in bls:
                target_for_bl = target
                break
        from ..kradio import firmware_bundle as _fb
        nice_target = _fb.friendly_target_label(target_for_bl) if target_for_bl else "unknown"
        self.firmware_view.set_detected_target(
            target_for_bl,
            f"{bl_version} → {model}<br><span style='color:#a6adc8;'>"
            f"target=<b>{nice_target}</b></span>",
        )
        # Bootloader 7.00.07 is shared by UV-K5 V3 and UV-K1(8) v3 Mini
        # Kong — same MCU, same firmware payload — so we explain it
        # rather than picking a single name.
        shared_note = ""
        if bl_version == "7.00.07":
            shared_note = (
                "\n\nThis bootloader (7.00.07) ships on both UV-K5 V3 "
                "and UV-K1(8) v3 'Mini Kong'. They share the PY32F071 "
                "MCU — the same firmware binary works on both, "
                "so the toolkit groups them under one flash target."
            )

        QMessageBox.information(
            self,
            "DFU bootloader found",
            f"Connected on {port} at 38400 baud.\n\n"
            f"Bootloader version: {bl_version}\n"
            f"Detected model:     {model}\n"
            f"UID:                {uid}\n"
            + (f"Flash target:       {target_for_bl}\n\n"
               f"The bundled firmware list now shows only entries "
               f"compatible with this target." + shared_note
               if target_for_bl else
               "No matching DFU target in the anti-brick allowlist for "
               "this bootloader. Flashing will be refused.")
        )

    def _on_dfu_failed(self, message: str) -> None:
        QMessageBox.critical(
            self, "DFU detection failed",
            f"{message}\n\n"
            f"Tip: power off the radio, hold PTT while powering on with "
            f"USB connected. The LCD should stay blank — that's "
            f"bootloader mode. Then click Detect again."
        )
        self.status_left.setText(f"  DFU: {message}")

    # ---- DFU flash (firmware write) --------------------------------------

    def _flash_firmware(self, path: str, target: str) -> None:
        """Spawn the FlashFirmwareWorker. ToolkitSettingsView already
        showed the typed/triple-confirm dialogs before reaching here."""
        if self._busy:
            return
        port = self.prefs.default_port or self.state.port_name or None
        self._set_busy(True)
        self.status_left.setText(
            f"  Flashing firmware ({Path(path).name} → {target})…"
        )
        from .workers import FlashFirmwareWorker
        worker = FlashFirmwareWorker(path, target, port_name=port)
        worker.signals.progress.connect(self._on_flash_progress)
        worker.signals.succeeded.connect(self._on_flash_done)
        worker.signals.failed.connect(self._on_flash_failed)
        worker.signals.finished.connect(lambda: self._set_busy(False))
        self._flash_worker = worker
        worker.start()

    def _on_flash_progress(self, done: int, total: int) -> None:
        self.status_progress.setRange(0, total)
        self.status_progress.setValue(done)
        if total > 0:
            pct = (done * 100) // total
            self.status_left.setText(
                f"  Flashing firmware: page {done}/{total} ({pct}%)"
            )

    def _on_flash_done(self, payload: dict) -> None:
        self.status_left.setText(
            f"  Flash complete — {payload['pages']} pages "
            f"({payload['size']} bytes) written. Radio is rebooting."
        )
        QMessageBox.information(
            self,
            "Firmware flash complete",
            f"Successfully wrote {payload['pages']} pages "
            f"({payload['size']} bytes) to "
            f"{payload['model']} (bootloader {payload['bl_version']}).\n\n"
            f"The radio is rebooting into the new firmware. Wait a few "
            f"seconds, then click Read EEPROM to confirm everything's OK."
        )

    def _on_flash_failed(self, message: str) -> None:
        QMessageBox.critical(
            self, "Flash failed",
            f"{message}\n\n"
            f"The bootloader stays reachable as long as you hold PTT "
            f"while powering on — your radio is not bricked. Re-enter "
            f"DFU mode and try again, or flash a known-good firmware "
            f"from the original Quansheng tools."
        )
        self.status_left.setText(f"  Flash failed: {message}")


def _short_tab_label(label: str) -> str:
    """Compact uppercase label for the cockpit-style top tab bar.

    Keep every entry to <=6 characters — the bar is horizontal and gets
    cramped fast as the toolkit grows. The full label still appears in
    the tooltip + status bar.
    """
    short = {
        "Dashboard":      "DASH",
        "Channels":       "CHAN",
        "Scan Lists":     "LISTS",
        "Radio Settings": "CFG",
        "DTMF":           "DTMF",
        "Display Mirror": "MIRROR",
        "Calibration":    "CAL",
        "Firmware":       "FW",
        "Hex Viewer":     "HEX",
        "Toolkit":        "TKT",
    }
    return short.get(label, label.upper())


def _wrap_in_scroll(content: QWidget) -> QScrollArea:
    """Wrap a widget in a transparent, frameless QScrollArea."""
    scroll = QScrollArea()
    scroll.setWidget(content)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    return scroll
