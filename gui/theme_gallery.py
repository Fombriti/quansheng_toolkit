"""
Theme gallery: render the MainWindow under every named theme and save a
screenshot of each to `theme_gallery/`. Useful for picking a default look.

Run from the project root:
    python -m quansheng_toolkit.gui.theme_gallery
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from .app import _set_default_font, ORG_NAME, APP_NAME
from .main_window import MainWindow
from .theme import (
    NAMED_THEMES,
    THEME_DISPLAY_NAMES,
    Palette,
    ThemeManager,
    qpalette_for,
    stylesheet,
)


SAMPLE_PAGES = (0, 1, 3)   # dashboard / channels / radio settings indices


class GalleryThemeManager(ThemeManager):
    """ThemeManager that always returns a fixed palette — handy for capture."""

    def __init__(self, fixed: Palette):
        super().__init__()
        self._fixed = fixed

    @property
    def palette(self) -> Palette:
        return self._fixed


def _wait_for_paint(app: QApplication, ms: int = 350) -> None:
    end = time.time() + ms / 1000.0
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def _capture(window: MainWindow, app: QApplication, out_path: Path,
             page_index: int) -> None:
    # Switch to a representative page before grabbing.
    window.stack.setCurrentIndex(page_index)
    _wait_for_paint(app)
    pix = window.grab()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out_path))


def main() -> int:
    QCoreApplication.setOrganizationName(ORG_NAME)
    QCoreApplication.setApplicationName(APP_NAME)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    _set_default_font(app)

    out_dir = Path("theme_gallery")
    out_dir.mkdir(exist_ok=True)

    sample_eeprom_path = Path("quansheng_toolkit/current_state.bin")
    sample_eeprom = (
        sample_eeprom_path.read_bytes() if sample_eeprom_path.exists() else None
    )

    page_labels = {0: "dashboard", 1: "channels", 3: "settings"}

    for theme_key, palette in NAMED_THEMES.items():
        # Apply this theme's palette + stylesheet at the application level.
        app.setPalette(qpalette_for(palette))
        app.setStyleSheet(stylesheet(palette))

        # Build a manager pinned to this palette so MainWindow's icons
        # render in the correct theme colours.
        theme = GalleryThemeManager(palette)
        window = MainWindow(theme)
        window.resize(1280, 820)

        if sample_eeprom is not None:
            window.state.firmware = "F4HWN v5.4.0"
            window.state.set_profile_from_firmware("F4HWN v5.4.0")
            window.state.set_eeprom(sample_eeprom)

        window.show()
        _wait_for_paint(app, 500)

        for idx, label in page_labels.items():
            out = out_dir / f"{theme_key}_{label}.png"
            _capture(window, app, out, idx)
            print(f"  {theme_key:20s} {label:10s}  ->  {out}")

        window.close()
        window.deleteLater()
        app.processEvents()

    print()
    print(f"Gallery saved in: {out_dir.resolve()}")
    print(f"Total: {len(NAMED_THEMES)} themes x {len(page_labels)} pages")
    print()
    print("Open the folder in Explorer to compare. To pick a theme later:")
    print("  in the GUI go to Toolkit -> Appearance and choose from the picker.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
