"""
Dashboard / connection view: port selector, Connect/Read EEPROM button,
firmware info and a few summary stat cards.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import serial.tools.list_ports

from ..icons import svg_icon
from ..state import AppState
from ..theme import Palette
from ..widgets import Card, PageHeader, StatCard


class DashboardView(QWidget):
    """Main landing page after launch."""

    read_requested = Signal(str)        # emits port name (or empty for auto)
    open_file_requested = Signal(str)   # emits file path

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setObjectName("ContentRoot")
        self._build_ui()
        self._wire()
        self.refresh()

    # ---- Layout -----------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(18)

        root.addWidget(PageHeader(
            "Dashboard",
            "Connect a Quansheng UV-K1, UV-K1(8) v3 Mini Kong or UV-K5 V3 "
            "(F4HWN Fusion 5.x or stock).",
        ))

        # Connection card
        conn_card = Card()
        conn_layout = QVBoxLayout(conn_card)
        conn_layout.setContentsMargins(20, 18, 20, 18)
        conn_layout.setSpacing(10)

        title = QLabel("CONNECTION")
        title.setObjectName("CardTitle")
        conn_layout.addWidget(title)

        controls = QHBoxLayout()
        controls.setSpacing(10)

        self.port_combo = QComboBox()
        self.port_combo.setMinimumWidth(220)
        controls.addWidget(self.port_combo)

        self.refresh_ports_btn = QPushButton()
        self.refresh_ports_btn.setObjectName("SecondaryBtn")
        self.refresh_ports_btn.setFixedSize(40, 38)
        self.refresh_ports_btn.setIconSize(QSize(18, 18))
        self.refresh_ports_btn.setToolTip("Re-scan available serial ports")
        controls.addWidget(self.refresh_ports_btn)

        self.read_btn = QPushButton(" Read EEPROM from radio")
        self.read_btn.setObjectName("PrimaryBtn")
        self.read_btn.setIconSize(QSize(18, 18))
        controls.addWidget(self.read_btn)

        self.open_btn = QPushButton(" Open .bin file")
        self.open_btn.setObjectName("SecondaryBtn")
        self.open_btn.setIconSize(QSize(18, 18))
        controls.addWidget(self.open_btn)

        controls.addStretch()
        conn_layout.addLayout(controls)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        conn_layout.addWidget(self.progress)

        root.addWidget(conn_card)

        # Stats grid (populated after EEPROM is loaded)
        self.stats_grid = QGridLayout()
        self.stats_grid.setHorizontalSpacing(14)
        self.stats_grid.setVerticalSpacing(14)

        self.stat_fw = StatCard("—", "FIRMWARE")
        self.stat_channels = StatCard("—", "CHANNELS CONFIGURED")
        self.stat_lists = StatCard("—", "SCAN LISTS USED")
        self.stat_boot_ch = StatCard("—", "BOOT CHANNEL")

        self.stats_grid.addWidget(self.stat_fw, 0, 0)
        self.stats_grid.addWidget(self.stat_channels, 0, 1)
        self.stats_grid.addWidget(self.stat_lists, 0, 2)
        self.stats_grid.addWidget(self.stat_boot_ch, 0, 3)

        stats_wrapper = QWidget()
        stats_wrapper.setLayout(self.stats_grid)
        root.addWidget(stats_wrapper)

        # Quick info card
        info_card = Card()
        info_layout = QVBoxLayout(info_card)
        info_layout.setContentsMargins(20, 16, 20, 16)
        info_layout.setSpacing(6)

        info_title = QLabel("HOW IT WORKS")
        info_title.setObjectName("CardTitle")
        info_layout.addWidget(info_title)

        body = QLabel(
            "1. <b>Read</b> the radio's EEPROM into memory.<br>"
            "2. Edit channels / scan lists / settings in the side views.<br>"
            "3. Click <b>Apply Changes</b> in the top-right of any page when "
            "ready. The upload writes the entire program region (≈16 s) "
            "in CHIRP-compatible mode and reboots the radio.<br><br>"
            "<i>Tip:</i> if the upload hangs, power-cycle the radio and "
            "unplug/replug the USB cable, then retry."
        )
        body.setWordWrap(True)
        body.setStyleSheet("color: #a6adc8;")
        info_layout.addWidget(body)

        root.addWidget(info_card)
        root.addStretch()

    # ---- Wiring -----------------------------------------------------------

    def _wire(self) -> None:
        self.refresh_ports_btn.clicked.connect(self._populate_ports)
        self.read_btn.clicked.connect(self._on_read)
        self.open_btn.clicked.connect(self._on_open_file)
        self.state.eeprom_loaded.connect(self.refresh)
        self._populate_ports()

    def refresh_icons(self, palette: Palette) -> None:
        """Re-render the icons on local buttons in the active palette."""
        primary_text = "#ffffff" if palette.name == "light" else palette.base
        self.read_btn.setIcon(svg_icon("download", primary_text, 18))
        self.refresh_ports_btn.setIcon(svg_icon("refresh", palette.text, 18))
        self.open_btn.setIcon(svg_icon("folder-open", palette.text, 18))

    def _populate_ports(self) -> None:
        self.port_combo.clear()
        items: list[tuple[str, str]] = [("(auto-detect)", "")]
        for p in serial.tools.list_ports.comports():
            label = f"{p.device} — {p.description or 'unknown'}"
            items.append((label, p.device))
        for label, value in items:
            self.port_combo.addItem(label, value)

    def _selected_port(self) -> str:
        idx = self.port_combo.currentIndex()
        if idx < 0:
            return ""
        return self.port_combo.itemData(idx) or ""

    # ---- Slots ------------------------------------------------------------

    def _on_read(self) -> None:
        self.read_requested.emit(self._selected_port())

    def _on_open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open EEPROM .bin", "", "EEPROM image (*.bin);;All files (*)"
        )
        if path:
            self.open_file_requested.emit(path)

    # ---- Progress (driven from MainWindow) -------------------------------

    def set_busy(self, busy: bool) -> None:
        self.read_btn.setEnabled(not busy)
        self.open_btn.setEnabled(not busy)
        self.progress.setVisible(busy)
        if not busy:
            self.progress.setValue(0)

    def update_progress(self, done: int, total: int) -> None:
        if total <= 0:
            return
        self.progress.setRange(0, total)
        self.progress.setValue(done)

    # ---- Refresh after EEPROM load ---------------------------------------

    def refresh(self) -> None:
        if not self.state.has_image:
            self.stat_fw.set_value(self.state.firmware or "—")
            self.stat_channels.set_value("—")
            self.stat_lists.set_value("—")
            self.stat_boot_ch.set_value("—")
            return

        eeprom = bytes(self.state.eeprom)
        profile = self.state.profile
        mm = profile.memory_module
        try:
            channels = mm.decode_all_channels(eeprom)
        except Exception:
            channels = []
        configured = [c for c in channels if not c.is_empty]
        # Count scan lists used: any channel scanlist value > 0 and < ALL.
        used_lists: set[int] = set()
        for c in configured:
            v = c.scanlist
            if profile.name.startswith("F4HWN") and 1 <= v <= 24:
                used_lists.add(v)
            elif 1 <= v <= profile.num_scan_lists:
                used_lists.add(v)

        boot_label = "—"
        if profile.settings_module is not None:
            try:
                s = profile.settings_module.decode_settings(eeprom)
                boot = s.session.mr_channel_a
                boot_label = (
                    f"ch{boot + 1}"
                    if boot != 0xFFFF and boot < profile.num_channels
                    else "—"
                )
            except Exception:
                pass

        fw = self.state.firmware or "?"
        if not profile.verified:
            fw = f"{fw}  (experimental profile)"
        self.stat_fw.set_value(fw)
        self.stat_channels.set_value(f"{len(configured)} / {profile.num_channels}")
        self.stat_lists.set_value(f"{len(used_lists)} / {profile.num_scan_lists}")
        self.stat_boot_ch.set_value(boot_label)
