"""Capture the LIVE MainWindow under each Cockpit color variant."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from .app import _set_default_font
from .main_window import MainWindow
from .theme import (
    COCKPIT_AMBER,
    COCKPIT_CYAN_JET,
    COCKPIT_PHOSPHOR,
    COCKPIT_RED_TACTICAL,
    COCKPIT_SYNTHWAVE,
    Palette,
    ThemeManager,
    qpalette_for,
    stylesheet,
)


VARIANTS = [
    ("cockpit-amber",        COCKPIT_AMBER),
    ("cockpit-cyan-jet",     COCKPIT_CYAN_JET),
    ("cockpit-red-tactical", COCKPIT_RED_TACTICAL),
    ("cockpit-phosphor",     COCKPIT_PHOSPHOR),
    ("cockpit-synthwave",    COCKPIT_SYNTHWAVE),
]


class _Pinned(ThemeManager):
    def __init__(self, fixed: Palette):
        super().__init__()
        self._fixed = fixed

    @property
    def palette(self) -> Palette:
        return self._fixed


def _wait(app, ms: int) -> None:
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    _set_default_font(app)

    out = Path("theme_gallery"); out.mkdir(exist_ok=True)
    sample = Path("quansheng_toolkit/current_state.bin")
    eeprom = sample.read_bytes() if sample.exists() else None

    for slug, palette in VARIANTS:
        app.setPalette(qpalette_for(palette))
        app.setStyleSheet(stylesheet(palette))

        theme = _Pinned(palette)
        win = MainWindow(theme)
        win.resize(1280, 820)
        if eeprom is not None:
            win.state.firmware = "F4HWN v5.4.0"
            win.state.set_profile_from_firmware("F4HWN v5.4.0")
            win.state.set_eeprom(eeprom)
        win.show()
        _wait(app, 500)

        for page_idx, page_name in [(0, "dashboard"), (1, "channels"),
                                     (3, "settings")]:
            win.stack.setCurrentIndex(page_idx)
            _wait(app, 250)
            path = out / f"live_{slug}_{page_name}.png"
            win.grab().save(str(path))
            print(f"  {slug:24s} {page_name:10s} -> {path}")

        win.close(); win.deleteLater(); app.processEvents()
    return 0


if __name__ == "__main__":
    sys.exit(main())
