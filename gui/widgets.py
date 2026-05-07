"""Reusable widgets: card frames, stat boxes, section headers, LCD preview."""
from __future__ import annotations

from enum import Enum
from PySide6.QtCore import Qt, QRect, QSize, QTimer
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


class ConnectionStatus(str, Enum):
    DISCONNECTED = "disconnected"
    BUSY = "busy"
    READY = "ready"
    ERROR = "error"


class StatusDot(QWidget):
    """
    Small circular status dot for the bottom status bar. Pulses while
    BUSY, holds steady otherwise.
    """

    SIZE = 12

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE + 4, self.SIZE + 4)
        self._status = ConnectionStatus.DISCONNECTED
        self._pulse_phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)

    def set_status(self, status: ConnectionStatus) -> None:
        if status == self._status:
            return
        self._status = status
        if status == ConnectionStatus.BUSY:
            self._timer.start()
        else:
            self._timer.stop()
            self._pulse_phase = 0.0
        self.update()

    def _tick(self) -> None:
        self._pulse_phase = (self._pulse_phase + 0.08) % 1.0
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        base_colors = {
            ConnectionStatus.DISCONNECTED: QColor("#7f849c"),
            ConnectionStatus.BUSY:         QColor("#f9e2af"),
            ConnectionStatus.READY:        QColor("#a6e3a1"),
            ConnectionStatus.ERROR:        QColor("#f38ba8"),
        }
        color = base_colors[self._status]

        cx, cy = self.width() / 2, self.height() / 2

        # Pulsing halo while busy.
        if self._status == ConnectionStatus.BUSY:
            import math
            t = abs(math.sin(self._pulse_phase * math.pi))
            halo_radius = (self.SIZE / 2) + 4 * t
            halo = QColor(color)
            halo.setAlpha(int(80 * (1.0 - t)))
            painter.setBrush(halo)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(int(cx - halo_radius), int(cy - halo_radius),
                                int(halo_radius * 2), int(halo_radius * 2))

        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        r = self.SIZE / 2
        painter.drawEllipse(int(cx - r), int(cy - r),
                            self.SIZE, self.SIZE)
        painter.end()


class Card(QFrame):
    """A surface card with rounded corners. Use as a container."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setFrameShape(QFrame.Shape.NoFrame)


class StatCard(QFrame):
    """A small stat card: BIG VALUE on top, label below."""
    def __init__(self, value: str, label: str, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(6)

        self.value_label = QLabel(value)
        self.value_label.setObjectName("StatBig")
        layout.addWidget(self.value_label)

        self.label_label = QLabel(label.upper())
        self.label_label.setObjectName("StatLabel")
        layout.addWidget(self.label_label)
        layout.addStretch()
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class PageHeader(QWidget):
    """Big title + subtitle pair shown at the top of every page."""
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)
        layout.setSpacing(2)
        t = QLabel(title)
        t.setObjectName("PageHeading")
        layout.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("PageSubheading")
            s.setWordWrap(True)
            layout.addWidget(s)


class HSpacer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)


class LcdPreview(QWidget):
    """
    A small WYSIWYG of how the radio's 2x16 character boot logo will look on
    the actual LCD. Renders two rows of 16 monospace characters on a faint
    pixel-grid background, classic green-on-dark LCD look.
    """

    # The radio's welcome screen renders 12 characters per row. The
    # underlying EEPROM field is 16 bytes wide but the firmware truncates
    # everything past column 12, so the preview matches that to stay
    # WYSIWYG.
    COLS = 12
    ROWS = 2
    CHAR_W = 18
    CHAR_H = 28
    MARGIN = 12

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._line1 = ""
        self._line2 = ""
        self.setFixedSize(
            self.COLS * self.CHAR_W + 2 * self.MARGIN,
            self.ROWS * self.CHAR_H + 2 * self.MARGIN,
        )
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_lines(self, line1: str, line2: str) -> None:
        self._line1 = (line1 or "")[: self.COLS]
        self._line2 = (line2 or "")[: self.COLS]
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        # LCD body
        body_rect = self.rect()
        bg = QColor("#0d2b1a")          # near-black green
        on_color = QColor("#7eff95")    # bright LCD green
        off_color = QColor("#1c4a32")   # subtle pixel ghosts

        painter.setBrush(bg)
        painter.setPen(QPen(QColor("#0a1f12"), 2))
        painter.drawRoundedRect(body_rect.adjusted(1, 1, -1, -1), 8, 8)

        # Pixel-grid ghost layer
        ghost_pen = QPen(off_color)
        ghost_pen.setWidth(1)
        painter.setPen(ghost_pen)
        for r in range(self.ROWS):
            for c in range(self.COLS):
                x = self.MARGIN + c * self.CHAR_W + self.CHAR_W // 2
                y = self.MARGIN + r * self.CHAR_H + self.CHAR_H // 2
                painter.drawText(
                    x - 6, y - 7, self.CHAR_W, self.CHAR_H,
                    Qt.AlignmentFlag.AlignCenter,
                    "█",
                )

        # Real characters
        font = QFont("Consolas")
        if not font.exactMatch():
            font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPixelSize(self.CHAR_H - 8)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(on_color))
        self._draw_line(painter, 0, self._line1)
        self._draw_line(painter, 1, self._line2)

        painter.end()

    def _draw_line(self, painter: QPainter, row_idx: int, text: str) -> None:
        for i, ch in enumerate(text):
            x = self.MARGIN + i * self.CHAR_W
            y = self.MARGIN + row_idx * self.CHAR_H
            painter.drawText(
                QRect(x, y, self.CHAR_W, self.CHAR_H),
                int(Qt.AlignmentFlag.AlignCenter),
                ch,
            )
