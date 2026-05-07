"""
Toolkit Settings view: preferences that affect the toolkit's BEHAVIOUR
(not the radio). Calibration moved to its own tab in `calibration.py`;
firmware flash moved to its own tab in `firmware.py`. This view is now
strictly app-level configuration.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import serial.tools.list_ports

from ...kradio import dfu
from ...kradio import protocol as proto
from ..icons import svg_icon
from ..prefs import Prefs
from ..theme import (
    NAMED_THEMES,
    Palette,
    THEME_DISPLAY_NAMES,
    THEME_FAMILIES,
    ThemeManager,
    ThemeMode,
)
from ..widgets import PageHeader


class ToolkitSettingsView(QWidget):
    """All toolkit-level configuration in one page."""

    # Calibration signals → CalibrationView; firmware signals → FirmwareView.
    # ToolkitSettingsView is now app-prefs only.

    def __init__(self, prefs: Prefs, theme: ThemeManager, parent=None):
        super().__init__(parent)
        self.prefs = prefs
        self.theme = theme
        self.setObjectName("ContentRoot")
        self._build_ui()
        self._populate_from_prefs()

    # --------------------------------------------------------------- UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        root.addWidget(PageHeader(
            "Toolkit",
            "Preferences for this app, plus calibration & firmware tools. "
            "These do NOT change anything on the radio's EEPROM.",
        ))

        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(14)

        # --- Connection ---------------------------------------------------
        conn_box = QGroupBox("CONNECTION")
        conn_form = QFormLayout(conn_box)
        conn_form.setSpacing(10)
        conn_form.setHorizontalSpacing(20)
        conn_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft
                                    | Qt.AlignmentFlag.AlignVCenter)

        self.port_combo = QComboBox()
        self.port_combo.setEditable(False)
        conn_form.addRow(self._label("Default port"), self.port_combo)
        grid.addWidget(conn_box, 0, 0)

        # --- Backups ------------------------------------------------------
        bak_box = QGroupBox("BACKUPS")
        bak_form = QFormLayout(bak_box)
        bak_form.setSpacing(10)
        bak_form.setHorizontalSpacing(20)
        bak_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft
                                   | Qt.AlignmentFlag.AlignVCenter)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        self.backup_dir_edit = QLineEdit()
        dir_row.addWidget(self.backup_dir_edit, 1)
        self.backup_dir_browse = QPushButton(" Browse")
        self.backup_dir_browse.setObjectName("SecondaryBtn")
        self.backup_dir_browse.setIconSize(QSize(16, 16))
        self.backup_dir_browse.clicked.connect(self._pick_backup_dir)
        dir_row.addWidget(self.backup_dir_browse)
        dir_holder = QWidget()
        dir_holder.setLayout(dir_row)
        bak_form.addRow(self._label("Backup directory"), dir_holder)

        self.auto_backup_check = QCheckBox("before every Apply Changes")
        bak_form.addRow(self._label("Auto-backup"), self.auto_backup_check)

        self.retention_spin = QSpinBox()
        self.retention_spin.setRange(1, 999)
        self.retention_spin.setSuffix("  backups")
        bak_form.addRow(self._label("Keep last"), self.retention_spin)
        grid.addWidget(bak_box, 0, 1)

        # --- Behaviour ----------------------------------------------------
        beh_box = QGroupBox("BEHAVIOUR")
        beh_form = QFormLayout(beh_box)
        beh_form.setSpacing(10)
        beh_form.setHorizontalSpacing(20)
        beh_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft
                                   | Qt.AlignmentFlag.AlignVCenter)

        self.confirm_check = QCheckBox("show confirmation dialog")
        beh_form.addRow(self._label("Apply Changes"), self.confirm_check)

        self.powercycle_check = QCheckBox("show power-cycle reminder")
        beh_form.addRow(self._label("Pre-upload"), self.powercycle_check)

        self.reset_after_check = QCheckBox(
            "send reset packet after upload (recommended)"
        )
        beh_form.addRow(self._label("Post-upload"), self.reset_after_check)

        self.auto_reload_check = QCheckBox("re-read EEPROM after upload")
        beh_form.addRow(self._label(""), self.auto_reload_check)
        grid.addWidget(beh_box, 1, 0)

        # --- Appearance ---------------------------------------------------
        app_box = QGroupBox("APPEARANCE")
        app_form = QFormLayout(app_box)
        app_form.setSpacing(10)
        app_form.setHorizontalSpacing(20)
        app_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft
                                   | Qt.AlignmentFlag.AlignVCenter)

        # Family + Mode pickers. The Family combo lists the 5 cockpit
        # presets and the studio fallbacks; the Mode combo selects between
        # Auto (follow OS), Light, and Dark.
        self.family_combo = QComboBox()
        for fam_id, fam in THEME_FAMILIES.items():
            self.family_combo.addItem(fam["label"], fam_id)
        self.family_combo.currentIndexChanged.connect(self._on_family_combo)
        app_form.addRow(self._label("Theme family"), self.family_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Auto (follow system)", ThemeMode.SYSTEM.value)
        self.mode_combo.addItem("Light", ThemeMode.LIGHT.value)
        self.mode_combo.addItem("Dark", ThemeMode.DARK.value)
        self.mode_combo.currentIndexChanged.connect(self._on_mode_combo)
        app_form.addRow(self._label("Light / Dark"), self.mode_combo)

        self.geometry_check = QCheckBox("remember window size & position")
        app_form.addRow(self._label("Window"), self.geometry_check)
        grid.addWidget(app_box, 1, 1)

        # --- Advanced -----------------------------------------------------
        adv_box = QGroupBox("ADVANCED")
        adv_form = QFormLayout(adv_box)
        adv_form.setSpacing(10)
        adv_form.setHorizontalSpacing(20)
        adv_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft
                                   | Qt.AlignmentFlag.AlignVCenter)

        self.log_combo = QComboBox()
        self.log_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        adv_form.addRow(self._label("Log level"), self.log_combo)

        self.hex_check = QCheckBox("show raw EEPROM hex viewer")
        adv_form.addRow(self._label("Diagnostics"), self.hex_check)
        grid.addWidget(adv_box, 2, 0)

        # Calibration moved to its own dedicated tab — see views/calibration.py.
        # Firmware moved to its own dedicated tab — see views/firmware.py.

        wrap = QWidget()
        wrap.setLayout(grid)
        root.addWidget(wrap)
        root.addStretch()

        # Wiring
        self.port_combo.currentIndexChanged.connect(self._save_port)
        self.backup_dir_edit.editingFinished.connect(self._save_backup_dir)
        self.auto_backup_check.toggled.connect(
            lambda v: setattr(self.prefs, "auto_backup_before_apply", v))
        self.retention_spin.valueChanged.connect(
            lambda v: setattr(self.prefs, "backup_retention", v))
        self.confirm_check.toggled.connect(
            lambda v: setattr(self.prefs, "confirm_apply", v))
        self.powercycle_check.toggled.connect(
            lambda v: setattr(self.prefs, "show_powercycle_hint", v))
        self.reset_after_check.toggled.connect(
            lambda v: setattr(self.prefs, "reset_radio_after_apply", v))
        self.auto_reload_check.toggled.connect(
            lambda v: setattr(self.prefs, "auto_reload_after_apply", v))
        self.geometry_check.toggled.connect(
            lambda v: setattr(self.prefs, "remember_window_geometry", v))
        self.log_combo.currentTextChanged.connect(
            lambda v: setattr(self.prefs, "log_level", v))
        self.hex_check.toggled.connect(
            lambda v: setattr(self.prefs, "show_hex_viewer", v))

        # React to theme switches done elsewhere (sidebar menu) so the combo stays in sync.
        self.theme.paletteChanged.connect(self._sync_theme_combo)

    def refresh_icons(self, palette: Palette) -> None:
        self.backup_dir_browse.setIcon(svg_icon("folder-open", palette.text, 16))

    # ----------------------------------------------------------- Helpers

    def _label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setMinimumWidth(150)
        return lbl

    # --------------------------------------------------------- Population

    def _populate_from_prefs(self) -> None:
        # Ports
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        self.port_combo.addItem("(auto-detect)", "")
        for p in serial.tools.list_ports.comports():
            label = f"{p.device} — {p.description or 'unknown'}"
            self.port_combo.addItem(label, p.device)
        # Set selection
        target = self.prefs.default_port
        idx = 0
        for i in range(self.port_combo.count()):
            if self.port_combo.itemData(i) == target:
                idx = i
                break
        self.port_combo.setCurrentIndex(idx)
        self.port_combo.blockSignals(False)

        # Backups
        self.backup_dir_edit.setText(self.prefs.backup_dir)
        self.auto_backup_check.setChecked(self.prefs.auto_backup_before_apply)
        self.retention_spin.setValue(self.prefs.backup_retention)
        # Behaviour
        self.confirm_check.setChecked(self.prefs.confirm_apply)
        self.powercycle_check.setChecked(self.prefs.show_powercycle_hint)
        self.reset_after_check.setChecked(self.prefs.reset_radio_after_apply)
        self.auto_reload_check.setChecked(self.prefs.auto_reload_after_apply)
        # Appearance
        self._sync_theme_combo()
        self.geometry_check.setChecked(self.prefs.remember_window_geometry)
        # Advanced
        idx = self.log_combo.findText(self.prefs.log_level)
        if idx >= 0:
            self.log_combo.setCurrentIndex(idx)
        self.hex_check.setChecked(self.prefs.show_hex_viewer)

    def _sync_theme_combo(self) -> None:
        # Family
        if hasattr(self, "family_combo"):
            target = self.theme.family
            for i in range(self.family_combo.count()):
                if self.family_combo.itemData(i) == target:
                    self.family_combo.blockSignals(True)
                    self.family_combo.setCurrentIndex(i)
                    self.family_combo.blockSignals(False)
                    break
        # Mode (Auto/Light/Dark)
        if hasattr(self, "mode_combo"):
            target = self.theme.mode.value
            for i in range(self.mode_combo.count()):
                if self.mode_combo.itemData(i) == target:
                    self.mode_combo.blockSignals(True)
                    self.mode_combo.setCurrentIndex(i)
                    self.mode_combo.blockSignals(False)
                    break

    # ------------------------------------------------------------ Slots

    def _save_port(self, _idx: int) -> None:
        self.prefs.default_port = self.port_combo.currentData() or ""

    def _save_backup_dir(self) -> None:
        self.prefs.backup_dir = self.backup_dir_edit.text().strip()

    def _pick_backup_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(
            self, "Pick backup directory", self.backup_dir_edit.text() or ""
        )
        if d:
            self.backup_dir_edit.setText(d)
            self.prefs.backup_dir = d

    def _on_family_combo(self, _idx: int) -> None:
        family = self.family_combo.currentData() or ""
        try:
            self.theme.set_family(family)
        except Exception:
            pass

    def _on_mode_combo(self, _idx: int) -> None:
        value = self.mode_combo.currentData()
        try:
            self.theme.set_mode(ThemeMode(value))
        except Exception:
            pass

    # _on_detect_dfu / _on_flash_firmware moved to FirmwareView
    # (gui/views/firmware.py).
    # _on_restore_cal moved to CalibrationView (gui/views/calibration.py).
