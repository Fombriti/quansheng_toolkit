"""
Display Mirror — live LCD mirror of an F4HWN K1/K5 V3 over USB.

Shows the radio's 128×64 monochrome screen on the desktop in real
time. Useful for:
* Debugging settings without watching the radio
* Recording demos / screenshots for documentation
* Showing babbo what's happening on the radio from the laptop

Requires the radio to be running an F4HWN build with
`ENABLE_FEAT_F4HWN_SCREENSHOT` (Fusion 4.3+ has it by default). Stock
Quansheng firmware does not implement this protocol — the start
button will time out / show no frames.

Owns the serial port for the duration of a session — you can't read
EEPROM or write channels while the mirror is running.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...kradio import display_mirror as mirror
from ..state import AppState
from ..widgets import PageHeader


# Render parameters — match the upstream look so screenshots feel familiar.
PIXEL_SIZE = 6           # each LCD pixel rendered as N×N px
GAP_SIZE = 0             # gap between pixels (0 = solid LCD look)
CANVAS_W = mirror.DISPLAY_WIDTH * (PIXEL_SIZE + GAP_SIZE)
CANVAS_H = mirror.DISPLAY_HEIGHT * (PIXEL_SIZE + GAP_SIZE)
LCD_BG = QColor("#A8C292")     # green-ish LCD background
LCD_FG = QColor("#06080c")     # near-black pixels
LCD_OFF_ALPHA = 30             # very faint when "off" — gives the LCD grain


class DisplayMirrorView(QWidget):
    """LCD canvas + start/stop/save controls. Worker is owned by main_window."""

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setObjectName("ContentRoot")
        self._framebuffer: bytes = b"\x00" * mirror.FRAMEBUFFER_SIZE
        self._build_ui()
        self._render_canvas()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)

        root.addWidget(PageHeader(
            "Display Mirror",
            "Real-time mirror of the radio's LCD over USB. Requires F4HWN "
            "with the screenshot feature compiled in (Fusion 4.3+ default).",
        ))

        # Toolbar
        bar = QHBoxLayout()
        bar.setSpacing(8)

        self.btn_start = QPushButton("▶ Start mirror")
        self.btn_start.setObjectName("PrimaryBtn")
        bar.addWidget(self.btn_start)

        self.btn_stop = QPushButton("■ Stop")
        self.btn_stop.setObjectName("SecondaryBtn")
        self.btn_stop.setEnabled(False)
        bar.addWidget(self.btn_stop)

        bar.addSpacing(20)

        self.btn_save = QPushButton("📷 Save PNG…")
        self.btn_save.setObjectName("SecondaryBtn")
        self.btn_save.clicked.connect(self._on_save_png)
        bar.addWidget(self.btn_save)

        bar.addStretch()

        self.status_label = QLabel("Idle")
        self.status_label.setStyleSheet("color: #a6adc8; font-size: 13px;")
        bar.addWidget(self.status_label)

        root.addLayout(bar)

        # The LCD canvas itself.
        self.canvas = QLabel()
        self.canvas.setFixedSize(CANVAS_W + 24, CANVAS_H + 24)
        self.canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.canvas.setStyleSheet(
            f"background: #586a47; padding: 12px; border-radius: 8px;"
        )
        self.canvas.setSizePolicy(QSizePolicy.Policy.Fixed,
                                   QSizePolicy.Policy.Fixed)

        canvas_holder = QHBoxLayout()
        canvas_holder.addStretch()
        canvas_holder.addWidget(self.canvas)
        canvas_holder.addStretch()
        root.addLayout(canvas_holder)

        # Footer hint.
        hint = QLabel(
            "While the mirror is running, the toolkit cannot read or write "
            "EEPROM on this radio (the serial port is in use). Stop the "
            "mirror to do other operations."
        )
        hint.setStyleSheet("color:#a6adc8; font-size:12px;")
        hint.setWordWrap(True)
        root.addWidget(hint)
        root.addStretch()

    # ----------------------------------------------------------- Rendering

    def update_framebuffer(self, fb: bytes) -> None:
        """Slot for the worker's `frame` signal."""
        if len(fb) != mirror.FRAMEBUFFER_SIZE:
            return
        self._framebuffer = fb
        self._render_canvas()

    def _render_canvas(self) -> None:
        # Build a QImage at the LCD's native resolution, then scale up.
        img = QImage(
            mirror.DISPLAY_WIDTH, mirror.DISPLAY_HEIGHT,
            QImage.Format.Format_RGB32,
        )
        img.fill(LCD_BG)
        painter = QPainter(img)
        painter.setPen(LCD_FG)
        for y in range(mirror.DISPLAY_HEIGHT):
            for x in range(mirror.DISPLAY_WIDTH):
                bit_idx = y * mirror.DISPLAY_WIDTH + x
                byte = self._framebuffer[bit_idx >> 3]
                if (byte >> (bit_idx & 7)) & 1:
                    painter.drawPoint(x, y)
        painter.end()
        # Scale up with no smoothing (chunky pixelated look).
        scaled = img.scaled(
            CANVAS_W, CANVAS_H,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self.canvas.setPixmap(QPixmap.fromImage(scaled))

    # -------------------------------------------------- External UI hooks

    def set_running(self, running: bool, message: str = "") -> None:
        """Called by main_window to reflect worker state."""
        self.btn_start.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        if running:
            self.status_label.setText(message or "Mirror running…")
            self.status_label.setStyleSheet("color: #a6e3a1; font-weight: 600;")
        else:
            self.status_label.setText(message or "Idle")
            self.status_label.setStyleSheet("color: #a6adc8;")

    # ---------------------------------------------------------- Save PNG

    def _on_save_png(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save LCD screenshot", "lcd_screenshot.png",
            "PNG image (*.png)"
        )
        if not path:
            return
        # Save the high-resolution scaled canvas (looks better than 128×64).
        pix = self.canvas.pixmap()
        if pix is None or pix.isNull():
            QMessageBox.warning(self, "Nothing to save",
                                "The canvas is empty.")
            return
        if not pix.save(path, "PNG"):
            QMessageBox.critical(self, "Save failed",
                                  f"Could not save to {path}")
