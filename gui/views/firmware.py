"""
Firmware view: dedicated tab for DFU bootloader identification and
firmware flashing.

The view is split into four cards:

  1. Info — how to enter DFU mode
  2. Identify — read-only "Detect DFU bootloader" button. Once it
     reports back, the bundled-firmware dropdown filters to compatible
     entries automatically.
  3. Bundled firmware — pick from the F4HWN binaries shipped with the
     toolkit, click flash. Skips the file picker AND the target
     dialog because the manifest tells us both.
  4. Custom .bin — file picker + manual target picker, for any
     non-bundled firmware (e.g. stock Quansheng or a freshly
     downloaded build).

Anti-brick gate runs against the live bootloader on every flash, so
no path can write to an incompatible radio regardless of how the
firmware got picked.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...kradio import firmware_bundle as fbundle
from ..icons import svg_icon
from ..state import AppState
from ..theme import Palette
from ..widgets import Card, PageHeader


# Firmware sanity check: K5/K1 bootloaders cap the program region at <96 KB.
MAX_FIRMWARE_SIZE_BYTES = 0x18000


class FirmwareView(QWidget):
    """Dedicated page for DFU bootloader identification + firmware flash."""

    dfu_identify_requested = Signal()
    flash_firmware_requested = Signal(str, str)   # (path, target)

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setObjectName("ContentRoot")
        # Detected target — set by main_window after a successful DFU
        # identify, or derived from the active EEPROM profile. None = no
        # detection yet, dropdown shows everything.
        self._detected_target: str | None = None
        self._all_bundled = fbundle.load_manifest()
        self._build_ui()
        # If an EEPROM was already loaded before this view was built,
        # derive a target right away.
        self.state.eeprom_loaded.connect(self._on_eeprom_loaded)
        self._on_eeprom_loaded()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        root.addWidget(PageHeader(
            "Firmware",
            "Identify the radio's bootloader and flash a firmware. "
            "Anti-brick allowlist verified on UV-K5 V3, UV-K1(8) v3 "
            "Mini Kong (F4HWN 4.3 / 5.4) and UV-K1 standard (F4HWN 5.4).",
        ))

        # --- 1) How-to card ------------------------------------------------
        info_card = Card()
        info_layout = QVBoxLayout(info_card)
        info_layout.setContentsMargins(20, 16, 20, 16)
        info_layout.setSpacing(8)

        info_title = QLabel("HOW TO ENTER DFU MODE")
        info_title.setObjectName("CardTitle")
        info_layout.addWidget(info_title)

        info_text = QLabel(
            "1. Power off the radio (USB cable connected)<br>"
            "2. Hold <b>PTT + Side Key 2</b> while powering on<br>"
            "3. The LCD stays blank — the bootloader is broadcasting<br><br>"
            "DFU runs at 38400 baud, separate from the EEPROM read/write "
            "protocol. The toolkit auto-detects the bootloader version "
            "and refuses mismatched firmware/target pairs."
        )
        info_text.setWordWrap(True)
        info_text.setStyleSheet("color: #a6adc8; font-size: 13px;")
        info_layout.addWidget(info_text)

        root.addWidget(info_card)

        # --- 2) Identify card (read-only) ----------------------------------
        identify_box = QGroupBox("IDENTIFY — safe, read-only")
        identify_layout = QVBoxLayout(identify_box)
        identify_layout.setSpacing(10)

        identify_text = QLabel(
            "Sends a single Identify command to the bootloader and "
            "reports back the version + UID. Once detected, the bundled "
            "firmware list below filters to entries compatible with "
            "this radio."
        )
        identify_text.setWordWrap(True)
        identify_text.setStyleSheet("color: #a6adc8; font-size: 13px;")
        identify_layout.addWidget(identify_text)

        identify_btns = QHBoxLayout()
        self.detect_btn = QPushButton(" Detect DFU bootloader")
        self.detect_btn.setObjectName("PrimaryBtn")
        self.detect_btn.setIconSize(QSize(16, 16))
        self.detect_btn.clicked.connect(self._on_detect)
        identify_btns.addWidget(self.detect_btn)
        identify_btns.addStretch()

        self.detected_label = QLabel("(no radio detected yet)")
        self.detected_label.setStyleSheet(
            "color: #a6adc8; font-size: 13px; font-style: italic;"
        )
        identify_btns.addWidget(self.detected_label)
        identify_layout.addLayout(identify_btns)

        root.addWidget(identify_box)

        # --- 3) Bundled firmware card --------------------------------------
        if self._all_bundled:
            bundle_box = QGroupBox(
                "BUNDLED FIRMWARE — one-click flash, target auto-selected"
            )
            bundle_layout = QVBoxLayout(bundle_box)
            bundle_layout.setSpacing(10)

            self.bundle_status = QLabel("")
            self.bundle_status.setWordWrap(True)
            self.bundle_status.setStyleSheet("color: #a6adc8; font-size: 13px;")
            bundle_layout.addWidget(self.bundle_status)

            combo_row = QHBoxLayout()
            combo_row.addWidget(QLabel("Choose:"))
            self.bundle_combo = QComboBox()
            self.bundle_combo.currentIndexChanged.connect(
                self._on_bundle_combo_changed
            )
            combo_row.addWidget(self.bundle_combo, 1)
            bundle_layout.addLayout(combo_row)

            self.bundle_info = QLabel("")
            self.bundle_info.setWordWrap(True)
            self.bundle_info.setStyleSheet("color: #a6adc8; font-size: 12px;")
            bundle_layout.addWidget(self.bundle_info)

            bundle_btns = QHBoxLayout()
            self.flash_bundled_btn = QPushButton(" Flash selected bundled firmware")
            self.flash_bundled_btn.setObjectName("DangerBtn")
            self.flash_bundled_btn.setIconSize(QSize(16, 16))
            self.flash_bundled_btn.clicked.connect(self._on_flash_bundled)
            bundle_btns.addWidget(self.flash_bundled_btn)
            bundle_btns.addStretch()
            bundle_layout.addLayout(bundle_btns)

            root.addWidget(bundle_box)
            self._refresh_bundle_combo()
        else:
            self.bundle_combo = None        # type: ignore[assignment]
            self.bundle_info = None         # type: ignore[assignment]
            self.bundle_status = None       # type: ignore[assignment]
            self.flash_bundled_btn = None   # type: ignore[assignment]

        # --- 4) Custom firmware card ---------------------------------------
        custom_box = QGroupBox(
            "CUSTOM .BIN — pick a file from disk (advanced)"
        )
        custom_layout = QVBoxLayout(custom_box)
        custom_layout.setSpacing(10)

        custom_text = QLabel(
            "Use this for any firmware not in the bundled list — "
            "Quansheng stock firmware (download from "
            "<a href=\"https://en.qsfj.com/support/downloads/\">qsfj.com</a>), "
            "newer F4HWN releases, or experimental builds. "
            "You'll be asked to pick the radio family before any byte "
            "is written."
        )
        custom_text.setWordWrap(True)
        custom_text.setOpenExternalLinks(True)
        custom_text.setStyleSheet("color: #a6adc8; font-size: 13px;")
        custom_layout.addWidget(custom_text)

        custom_btns = QHBoxLayout()
        self.flash_custom_btn = QPushButton(" Pick .bin & flash…")
        self.flash_custom_btn.setObjectName("DangerBtn")
        self.flash_custom_btn.setIconSize(QSize(16, 16))
        self.flash_custom_btn.clicked.connect(self._on_flash_custom)
        custom_btns.addWidget(self.flash_custom_btn)
        custom_btns.addStretch()
        custom_layout.addLayout(custom_btns)

        root.addWidget(custom_box)
        root.addStretch()

    def refresh_icons(self, palette: Palette) -> None:
        primary_text = "#ffffff" if palette.name == "light" else palette.base
        self.detect_btn.setIcon(svg_icon("monitor", primary_text, 16))
        if self.flash_bundled_btn is not None:
            self.flash_bundled_btn.setIcon(svg_icon("send", "#1e1e2e", 16))
        self.flash_custom_btn.setIcon(svg_icon("send", "#1e1e2e", 16))

    # -------------------------------------------- Detected-target plumbing

    def set_detected_target(self, target: str | None,
                             label: str | None = None) -> None:
        """Called by main_window when DFU identify or EEPROM read tells
        us which radio family is connected.

        `target` is one of "k5_k6" / "k5_v3" / "k1" / None. `label` is
        a short human string to show under the Identify card (e.g.
        "7.03.01 → UV-K1"). When target changes, the bundled dropdown
        is repopulated.
        """
        if label is not None:
            self.detected_label.setText(label)
            self.detected_label.setStyleSheet(
                "color: #a6e3a1; font-size: 13px; font-weight: 600;"
            )
        if target == self._detected_target:
            return
        self._detected_target = target
        self._refresh_bundle_combo()

    def _on_eeprom_loaded(self) -> None:
        if not self.state.has_image:
            return
        targets = fbundle.targets_for_profile_name(self.state.profile.name)
        if not targets:
            return
        # Prefer the more specific target if there are multiple. The
        # profile's name carries the family, the bootloader is what
        # picks one specifically — but if the user only has the
        # EEPROM read (no DFU), fall back to the first target.
        chosen = next(iter(targets))
        self.set_detected_target(
            chosen,
            f"From EEPROM: {self.state.profile.name} → target={chosen}",
        )

    # ---------------------------------------------------- Bundle dropdown

    def _refresh_bundle_combo(self) -> None:
        if self.bundle_combo is None:
            return
        self.bundle_combo.blockSignals(True)
        try:
            self.bundle_combo.clear()
            filtered = fbundle.filter_for_target(
                self._all_bundled, self._detected_target
            )
            if filtered:
                for fw in filtered:
                    self.bundle_combo.addItem(fw.display_label, userData=fw.id)
                if self.bundle_status is not None:
                    if self._detected_target:
                        nice = fbundle.friendly_target_label(self._detected_target)
                        self.bundle_status.setText(
                            f"Showing <b>{len(filtered)} firmware(s)</b> "
                            f"compatible with <b>{nice}</b>. "
                            f"(Run Detect again to refresh, or use Custom .bin "
                            f"below for anything else.)"
                        )
                    else:
                        self.bundle_status.setText(
                            f"Showing all <b>{len(filtered)} bundled "
                            f"firmware(s)</b>. Run <b>Detect DFU bootloader</b> "
                            f"above to filter to your radio."
                        )
            else:
                self.bundle_combo.addItem(
                    "(no compatible bundled firmware for this target)",
                    userData=None,
                )
                if self.bundle_status is not None:
                    nice = fbundle.friendly_target_label(self._detected_target or "")
                    self.bundle_status.setText(
                        f"No bundled firmware matches <b>{nice}</b>. "
                        f"Use Custom .bin below to flash a file from disk."
                    )
        finally:
            self.bundle_combo.blockSignals(False)
        # Trigger info refresh.
        self._on_bundle_combo_changed()
        # Disable flash if there's nothing real selected.
        if self.flash_bundled_btn is not None:
            self.flash_bundled_btn.setEnabled(self._selected_bundled() is not None)

    def _selected_bundled(self) -> "fbundle.BundledFirmware | None":
        if self.bundle_combo is None:
            return None
        fw_id = self.bundle_combo.currentData()
        if not fw_id:
            return None
        return fbundle.find_by_id(fw_id)

    def _on_bundle_combo_changed(self) -> None:
        if self.bundle_info is None:
            return
        fw = self._selected_bundled()
        if fw is None:
            self.bundle_info.setText("")
            if self.flash_bundled_btn is not None:
                self.flash_bundled_btn.setEnabled(False)
            return
        if self.flash_bundled_btn is not None:
            self.flash_bundled_btn.setEnabled(True)
        tested = ", ".join(fw.tested_on) if fw.tested_on else "no hardware tests yet"
        nice_target = fbundle.friendly_target_label(fw.target)
        # Build a small "Source: <vendor> · <license>" line — the user
        # asked us to surface vendor + license clearly so it's obvious
        # which entries are F4HWN custom vs Quansheng stock.
        license_text = fw.license or "—"
        if fw.release_url:
            source_line = (
                f"Source: <a href=\"{fw.release_url}\">{fw.vendor}</a>  "
                f"·  License: {license_text}"
            )
        else:
            source_line = f"Source: {fw.vendor}  ·  License: {license_text}"
        self.bundle_info.setText(
            f"Version <b>{fw.version}</b>  ·  MCU <b>{fw.mcu}</b>  ·  "
            f"{fw.size_bytes:,} bytes<br>"
            f"Target: <b>{nice_target}</b><br>"
            f"Supports: {', '.join(fw.supports)}<br>"
            f"Tested on: {tested}<br>"
            f"{source_line}"
            + (f"<br><i>{fw.notes}</i>" if fw.notes else "")
        )
        self.bundle_info.setOpenExternalLinks(True)

    # -------------------------------------------------------------- Handlers

    def _on_detect(self) -> None:
        self.dfu_identify_requested.emit()

    def _confirm_dfu_warning(self) -> bool:
        ans = QMessageBox.warning(
            self,
            "Flash firmware — DESTRUCTIVE",
            "About to flash firmware to the radio.\n\n"
            "Before continuing:\n"
            "  • Power off the radio\n"
            "  • Hold PTT, then power on (LCD stays blank)\n"
            "  • USB cable connected\n\n"
            "The toolkit will detect the bootloader, refuse mismatched "
            "firmware/bootloader pairs (anti-brick allowlist), and only "
            "write after explicit confirmation.\n\n"
            "Proceed?",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        return ans == QMessageBox.StandardButton.Yes

    def _confirm_last_chance(self, *, display_name: str, sz: int,
                              target: str) -> bool:
        ans = QMessageBox.warning(
            self,
            "Last chance",
            f"About to flash:\n\n"
            f"  Firmware: {display_name}\n"
            f"  Size:     {sz:,} bytes\n"
            f"  Target:   {target}\n\n"
            f"The bootloader version on the connected radio must match "
            f"the target family. If it doesn't, the flash will be "
            f"refused (no byte written).\n\nReally proceed?",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        return ans == QMessageBox.StandardButton.Yes

    def _on_flash_bundled(self) -> None:
        fw = self._selected_bundled()
        if fw is None:
            return
        if not self._confirm_dfu_warning():
            return
        if not self._confirm_last_chance(
            display_name=fw.name, sz=fw.size_bytes, target=fw.target,
        ):
            return
        self.flash_firmware_requested.emit(str(fw.path), fw.target)

    def _on_flash_custom(self) -> None:
        if not self._confirm_dfu_warning():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick firmware .bin", "", "Firmware (*.bin)"
        )
        if not path:
            return
        try:
            sz = Path(path).stat().st_size
        except OSError as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        if sz > MAX_FIRMWARE_SIZE_BYTES:
            QMessageBox.critical(
                self, "File too large",
                f"This file is {sz} bytes — bigger than any K5/K1 "
                f"bootloader will accept. Did you pick the right .bin?"
            )
            return

        target_box = QMessageBox(self)
        target_box.setIcon(QMessageBox.Icon.Question)
        target_box.setWindowTitle("Pick firmware target")
        target_box.setText(
            "Which radio family is this firmware for?\n\n"
            "  • UV-K5 / UV-K5(8) / K6 / 5R Plus — DP32G030, 60 KB cap\n"
            "  • UV-K5 V3 / UV-K1(8) v3 Mini Kong — PY32F071 with\n"
            "    bootloader 7.00.07 (same firmware binary on both)\n"
            "  • UV-K1 — PY32F071 with bootloader 7.03.x\n\n"
            "If you pick the wrong family, the anti-brick gate will "
            "refuse the flash before any byte hits the radio."
        )
        btn_k5 = target_box.addButton("UV-K5 / K5(8) / K6",
                                       QMessageBox.ButtonRole.YesRole)
        btn_k5v3 = target_box.addButton("UV-K5 V3 / UV-K1(8) v3",
                                          QMessageBox.ButtonRole.YesRole)
        btn_k1 = target_box.addButton("UV-K1",
                                        QMessageBox.ButtonRole.YesRole)
        btn_cancel = target_box.addButton(QMessageBox.StandardButton.Cancel)
        target_box.exec()
        clicked = target_box.clickedButton()
        if clicked is btn_cancel or clicked is None:
            return
        if clicked is btn_k5:
            target = "k5_k6"
        elif clicked is btn_k5v3:
            target = "k5_v3"
        elif clicked is btn_k1:
            target = "k1"
        else:
            return

        if not self._confirm_last_chance(
            display_name=Path(path).name, sz=sz, target=target,
        ):
            return
        self.flash_firmware_requested.emit(path, target)
