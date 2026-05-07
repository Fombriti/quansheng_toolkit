"""
DTMF tools view: 16-slot contacts editor + quick reference of the
existing DTMF timing settings.

The DTMF timing parameters (preload time, side tone, kill code etc.)
are already exposed in the Radio Settings tab via the registry. This
view focuses on the **contacts table** which lives at EEPROM 0x1C00
on F4HWN K1, K1(8) v3 and stock K5 V1 / K1 stock — same layout
across the family.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QRegularExpressionValidator
from PySide6.QtCore import QRegularExpression
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...kradio import dtmf_contacts as dtmf
from ..state import AppState
from ..widgets import PageHeader


class DTMFView(QWidget):
    """16-slot DTMF contacts editor."""

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setObjectName("ContentRoot")
        self._building_table = False
        self._build_ui()
        self.state.eeprom_loaded.connect(self._reload_from_eeprom)
        self._reload_from_eeprom()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)

        root.addWidget(PageHeader(
            "DTMF Contacts",
            "16 named DTMF codes the radio can call by name. Stored at "
            "EEPROM 0x1C00 (16 slots × 16 bytes). Names: 8 chars ASCII. "
            "DTMF codes: up to 8 chars from 0-9 A-D * #.",
        ))

        # Bulk action bar
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self.btn_clear_all = QPushButton("Clear all contacts")
        self.btn_clear_all.setObjectName("SecondaryBtn")
        self.btn_clear_all.clicked.connect(self._on_clear_all)
        bar.addWidget(self.btn_clear_all)

        bar.addStretch()

        self.modified_label = QLabel("")
        self.modified_label.setStyleSheet("color: #f9e2af; font-weight: 600;")
        bar.addWidget(self.modified_label)

        root.addLayout(bar)

        # The table itself: 16 rows × 3 cols (#, Name, DTMF code)
        self.table = QTableWidget(dtmf.NUM_CONTACTS, 3)
        self.table.setHorizontalHeaderLabels(["#", "Name (max 8)", "DTMF code (0-9 A-D * #, max 8)"])
        self.table.verticalHeader().setVisible(False)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setAlternatingRowColors(True)
        # Each row is a fixed widget with its own validators — NOT
        # QTableWidgetItem editors which would let arbitrary text in.
        # Make rows tall enough that the embedded QLineEdits inherit a
        # comfortable height + readable font, instead of the cramped
        # default that the parent table imposes.
        ROW_HEIGHT = 38
        FIELD_FONT_PX = 16
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)

        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.TypeWriter)
        mono.setPixelSize(FIELD_FONT_PX)

        idx_font = QFont()
        idx_font.setPixelSize(FIELD_FONT_PX)
        idx_font.setBold(True)

        name_font = QFont()
        name_font.setPixelSize(FIELD_FONT_PX)

        cell_qss = (
            "QLineEdit { "
            f" font-size: {FIELD_FONT_PX}px; padding: 4px 8px; "
            " border: 1px solid rgba(127,132,156,80); border-radius: 4px;"
            " background: transparent;"
            "}"
            "QLineEdit:focus { border: 1px solid #89b4fa; }"
        )

        self._row_widgets: list[tuple[QLineEdit, QLineEdit]] = []
        for i in range(dtmf.NUM_CONTACTS):
            idx_item = QTableWidgetItem(str(i + 1))
            idx_item.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter
            )
            idx_item.setFlags(idx_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            idx_item.setFont(idx_font)
            self.table.setItem(i, 0, idx_item)

            name_edit = QLineEdit()
            name_edit.setMaxLength(dtmf.NAME_LEN)
            name_edit.setPlaceholderText("(empty)")
            name_edit.setFont(name_font)
            name_edit.setStyleSheet(cell_qss)
            name_edit.setMinimumHeight(ROW_HEIGHT - 6)
            name_edit.editingFinished.connect(
                lambda i=i: self._on_row_changed(i)
            )
            self.table.setCellWidget(i, 1, name_edit)

            code_edit = QLineEdit()
            code_edit.setMaxLength(dtmf.CODE_LEN)
            code_edit.setPlaceholderText("(empty)")
            code_edit.setFont(mono)
            code_edit.setStyleSheet(cell_qss)
            code_edit.setMinimumHeight(ROW_HEIGHT - 6)
            # Restrict input to valid DTMF chars while typing.
            re = QRegularExpression(r"^[0-9A-Da-d\*#]{0,8}$")
            code_edit.setValidator(QRegularExpressionValidator(re))
            code_edit.editingFinished.connect(
                lambda i=i: self._on_row_changed(i)
            )
            self.table.setCellWidget(i, 2, code_edit)

            self._row_widgets.append((name_edit, code_edit))

        root.addWidget(self.table, 1)

        # Footer: a hint about timing / advanced settings.
        hint = QLabel(
            "Timing parameters (preload time, side tone, kill code, "
            "auto-reset) are in the Radio Settings tab — search for "
            "'dtmf'."
        )
        hint.setStyleSheet("color:#a6adc8; font-size:12px; padding-top:6px;")
        hint.setWordWrap(True)
        root.addWidget(hint)

    # -------------------------------------------------------- Sync logic

    def _reload_from_eeprom(self) -> None:
        if not self.state.has_image:
            for name_edit, code_edit in self._row_widgets:
                name_edit.setEnabled(False)
                code_edit.setEnabled(False)
                name_edit.setText("")
                code_edit.setText("")
            self.btn_clear_all.setEnabled(False)
            self.modified_label.setText("(read EEPROM first)")
            return

        try:
            contacts = dtmf.decode_all_contacts(bytes(self.state.eeprom))
        except ValueError as e:
            self.modified_label.setText(f"({e})")
            return

        self._building_table = True
        try:
            for c, (name_edit, code_edit) in zip(contacts, self._row_widgets):
                name_edit.setEnabled(True)
                code_edit.setEnabled(True)
                name_edit.setText(c.name)
                code_edit.setText(c.code)
        finally:
            self._building_table = False
        self.btn_clear_all.setEnabled(True)
        self.modified_label.setText("")

    def _on_row_changed(self, idx: int) -> None:
        if self._building_table or not self.state.has_image:
            return
        name_edit, code_edit = self._row_widgets[idx]
        new_name = name_edit.text().strip()
        new_code = code_edit.text().strip().upper()

        try:
            dtmf.patch_contact_in_image(
                self.state.eeprom, idx, name=new_name, code=new_code
            )
        except ValueError as e:
            QMessageBox.warning(self, "Invalid contact",
                                f"Contact #{idx + 1}: {e}")
            # Roll back the editor to last persisted value.
            self._reload_from_eeprom()
            return

        self.state.mark_dirty()
        self.modified_label.setText(f"● Slot {idx + 1} updated")

    def _on_clear_all(self) -> None:
        if not self.state.has_image:
            return
        ans = QMessageBox.warning(
            self, "Clear all DTMF contacts",
            "Clear all 16 DTMF contact slots?\n\n"
            "This only updates the local image — Apply Changes still "
            "needs to upload to the radio.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return
        for i in range(dtmf.NUM_CONTACTS):
            dtmf.clear_contact_in_image(self.state.eeprom, i)
        self.state.mark_dirty()
        self._reload_from_eeprom()
        self.modified_label.setText("● All slots cleared")
