"""
Hex viewer: a diagnostic tab for inspecting the loaded EEPROM image
(or an arbitrary .bin file) byte by byte.

Layout per line:
    AAAAAAAA  HH HH HH HH HH HH HH HH  HH HH HH HH HH HH HH HH  ASCII

Useful when:
* checking what the radio actually wrote vs. what we sent
* eyeballing channel records, name slots and attribute bytes after
  a fix to the encoder
* quick comparison against the upstream K5/K1 tooling / CHIRP dumps when
  reverse-engineering a new firmware variant

Read-only on purpose. Writes go through the typed channel/settings
APIs — there's no way to commit edits from the hex viewer.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QTextCharFormat, QTextCursor, QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..prefs import Prefs
from ..state import AppState
from ..widgets import PageHeader


def hex_dump(data: bytes, start_addr: int = 0, bytes_per_line: int = 16) -> str:
    """Format `data` as a classic hex+ASCII dump."""
    out: list[str] = []
    for off in range(0, len(data), bytes_per_line):
        chunk = data[off:off + bytes_per_line]
        addr = f"{start_addr + off:08X}"
        # Hex columns, with an extra space halfway through for readability.
        hex_left = " ".join(f"{b:02X}" for b in chunk[:bytes_per_line // 2])
        hex_right = " ".join(f"{b:02X}" for b in chunk[bytes_per_line // 2:])
        # Pad if last line is short.
        hex_left = hex_left.ljust((bytes_per_line // 2) * 3 - 1)
        hex_right = hex_right.ljust((bytes_per_line // 2) * 3 - 1)
        # ASCII column — printable ASCII, others become '.'
        ascii_repr = "".join(
            chr(b) if 0x20 <= b < 0x7F else "." for b in chunk
        )
        out.append(f"{addr}  {hex_left}  {hex_right}  {ascii_repr}")
    return "\n".join(out)


class HexViewerView(QWidget):
    """Read-only hex viewer for the active EEPROM image / external .bin."""

    def __init__(self, state: AppState, prefs: Prefs | None = None, parent=None):
        super().__init__(parent)
        self.state = state
        self.prefs = prefs
        self.setObjectName("ContentRoot")
        self._loaded_external: bytes | None = None
        self._loaded_external_path: str | None = None
        self._build_ui()
        self.state.eeprom_loaded.connect(self._refresh_view)
        self._refresh_view()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)

        root.addWidget(PageHeader(
            "Hex Viewer",
            "Read-only inspection of the EEPROM image. Useful for "
            "diagnostics — channels are at 0x0000, names at 0x4000, "
            "channel attributes at 0x8000, calibration at 0xB000 (F4HWN).",
        ))

        # Expert-only disclaimer banner — dismissible, persists across runs.
        self.disclaimer = self._build_disclaimer_banner()
        root.addWidget(self.disclaimer)
        if self.prefs is not None and self.prefs.hex_viewer_disclaimer_dismissed:
            self.disclaimer.setVisible(False)

        # Toolbar
        bar = QHBoxLayout()
        bar.setSpacing(8)

        bar.addWidget(QLabel("Source:"))
        self.source_label = QLabel("Active EEPROM image")
        self.source_label.setStyleSheet("color: #a6e3a1; font-weight: 600;")
        bar.addWidget(self.source_label)

        self.btn_use_active = QPushButton("Use active image")
        self.btn_use_active.setObjectName("SecondaryBtn")
        self.btn_use_active.clicked.connect(self._on_use_active)
        bar.addWidget(self.btn_use_active)

        self.btn_open_file = QPushButton("Open .bin…")
        self.btn_open_file.setObjectName("SecondaryBtn")
        self.btn_open_file.clicked.connect(self._on_open_file)
        bar.addWidget(self.btn_open_file)

        bar.addSpacing(20)
        bar.addWidget(QLabel("Jump:"))
        self.jump_edit = QLineEdit()
        self.jump_edit.setPlaceholderText("0x0000")
        self.jump_edit.setMaximumWidth(110)
        self.jump_edit.returnPressed.connect(self._on_jump)
        bar.addWidget(self.jump_edit)

        self.btn_jump = QPushButton("Go")
        self.btn_jump.setObjectName("SecondaryBtn")
        self.btn_jump.clicked.connect(self._on_jump)
        bar.addWidget(self.btn_jump)

        bar.addSpacing(20)
        bar.addWidget(QLabel("Find hex:"))
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("e.g. 43 49 43 43 = CICC")
        self.find_edit.returnPressed.connect(self._on_find)
        bar.addWidget(self.find_edit, 1)

        self.btn_find = QPushButton("Find")
        self.btn_find.setObjectName("SecondaryBtn")
        self.btn_find.clicked.connect(self._on_find)
        bar.addWidget(self.btn_find)

        root.addLayout(bar)

        # The actual hex pane.
        self.hex_pane = QPlainTextEdit()
        self.hex_pane.setReadOnly(True)
        # Pick a stable monospace font that ships everywhere.
        font = QFont("Consolas")
        if not font.exactMatch():
            font = QFont("Menlo")
        if not font.exactMatch():
            font = QFont("Monospace")
        font.setStyleHint(QFont.StyleHint.TypeWriter)
        font.setPointSize(11)
        self.hex_pane.setFont(font)
        self.hex_pane.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        root.addWidget(self.hex_pane, 1)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #a6adc8; font-size: 12px;")
        root.addWidget(self.status_label)

    # ---------------------------------------------------- Disclaimer banner

    def _build_disclaimer_banner(self) -> QWidget:
        """A dismissible warning shown above the hex pane the first time
        the user lands on this tab.

        The viewer itself is read-only, but interpreting raw bytes —
        especially next to the calibration region — is easy to get
        wrong, and we don't want a casual user to think "I see the
        bytes, I can edit them" when actual channel/settings/cal edits
        belong in the typed views. This banner sets that expectation
        once and stays out of the way after dismissal.
        """
        wrap = QWidget()
        wrap.setStyleSheet(
            "background: rgba(249, 226, 175, 35);"
            "border: 1px solid rgba(249, 226, 175, 90);"
            "border-radius: 8px;"
        )
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(10)

        icon = QLabel("⚠")
        icon.setStyleSheet("color: #f9e2af; font-size: 20px;")
        icon.setFixedWidth(28)
        layout.addWidget(icon)

        msg = QLabel(
            "<b>Expert users only.</b> This page shows the raw EEPROM "
            "contents byte by byte. It's a diagnostic tool — reads are "
            "safe, but interpreting the data correctly (especially "
            "near the calibration region at 0xB000) requires knowing "
            "what you're looking at. To edit channels, settings or "
            "calibration use the dedicated tabs, not this one."
        )
        msg.setWordWrap(True)
        msg.setStyleSheet("color: palette(window-text); font-size: 13px;")
        layout.addWidget(msg, 1)

        btn = QPushButton("Got it")
        btn.setObjectName("SecondaryBtn")
        btn.clicked.connect(self._on_dismiss_disclaimer)
        layout.addWidget(btn)

        return wrap

    def _on_dismiss_disclaimer(self) -> None:
        if self.prefs is not None:
            self.prefs.hex_viewer_disclaimer_dismissed = True
        self.disclaimer.setVisible(False)

    # ----------------------------------------------------------- Data sync

    def _current_data(self) -> bytes | None:
        """Active dump being shown — either the loaded EEPROM image or
        an externally-opened .bin file."""
        if self._loaded_external is not None:
            return self._loaded_external
        if self.state.has_image:
            return bytes(self.state.eeprom)
        return None

    def _refresh_view(self) -> None:
        data = self._current_data()
        if data is None:
            self.hex_pane.setPlainText(
                "(no data loaded — Read EEPROM from the radio, or click "
                "Open .bin…)"
            )
            self.status_label.setText("")
            return
        self.hex_pane.setPlainText(hex_dump(data))
        self.status_label.setText(
            f"{len(data):,} bytes  (0x{len(data):X})  ·  "
            f"{(len(data) + 15) // 16} lines  ·  "
            f"source: {'active EEPROM' if self._loaded_external is None else self._loaded_external_path}"
        )

    # -------------------------------------------------------------- Actions

    def _on_use_active(self) -> None:
        self._loaded_external = None
        self._loaded_external_path = None
        self.source_label.setText("Active EEPROM image")
        self.source_label.setStyleSheet("color: #a6e3a1; font-weight: 600;")
        self._refresh_view()

    def _on_open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open EEPROM dump or any binary file",
            "", "Binary files (*.bin *.dat);;All files (*.*)"
        )
        if not path:
            return
        try:
            data = Path(path).read_bytes()
        except OSError as e:
            QMessageBox.critical(self, "Open failed", str(e))
            return
        self._loaded_external = data
        self._loaded_external_path = path
        self.source_label.setText(Path(path).name)
        self.source_label.setStyleSheet("color: #f9e2af; font-weight: 600;")
        self._refresh_view()

    def _on_jump(self) -> None:
        spec = self.jump_edit.text().strip()
        if not spec:
            return
        try:
            addr = int(spec, 16) if spec.lower().startswith("0x") else int(spec, 16)
        except ValueError:
            QMessageBox.warning(self, "Bad address",
                                f"Couldn't parse {spec!r} as a hex address.")
            return
        data = self._current_data()
        if data is None or addr >= len(data):
            QMessageBox.warning(self, "Out of range",
                                f"0x{addr:04X} is past the end of this dump "
                                f"({len(data) if data else 0} bytes).")
            return
        # Each line covers 16 bytes; line N starts at addr (N*16).
        line_no = addr // 16
        block = self.hex_pane.document().findBlockByLineNumber(line_no)
        cursor = QTextCursor(block)
        self.hex_pane.setTextCursor(cursor)
        self.hex_pane.centerCursor()
        self._highlight_byte_at(addr)

    def _on_find(self) -> None:
        spec = self.find_edit.text().strip()
        if not spec:
            return
        # Accept "43 49 43 43" or "43494343" or "0x43 0x49"
        cleaned = spec.replace("0x", "").replace(",", " ").split()
        try:
            if len(cleaned) == 1:
                # Single token — treat as hex string
                pattern = bytes.fromhex(cleaned[0])
            else:
                pattern = bytes(int(t, 16) for t in cleaned)
        except ValueError:
            QMessageBox.warning(self, "Bad pattern",
                                f"Couldn't parse {spec!r} as a hex byte sequence.")
            return
        if not pattern:
            return
        data = self._current_data()
        if data is None:
            return
        idx = data.find(pattern)
        if idx < 0:
            self.status_label.setText(
                f"Pattern {pattern.hex(' ').upper()} not found."
            )
            return
        # Jump to the match
        self.jump_edit.setText(f"{idx:04X}")
        self._on_jump()
        self.status_label.setText(
            f"Found {pattern.hex(' ').upper()} at 0x{idx:04X} "
            f"({len(pattern)} bytes)."
        )

    def _highlight_byte_at(self, addr: int) -> None:
        """Highlight the line containing `addr` for a moment so the
        user's eye is drawn to it after a Jump or Find."""
        line_no = addr // 16
        block = self.hex_pane.document().findBlockByLineNumber(line_no)
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        cursor.select(QTextCursor.SelectionType.LineUnderCursor)
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(249, 226, 175, 60))  # soft yellow
        # We can't easily flash and fade in QPlainTextEdit; just leave the
        # selection visible. Clicking elsewhere clears it.
        self.hex_pane.setTextCursor(cursor)
