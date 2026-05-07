"""QApplication setup + theme injection + persistent settings root."""
from __future__ import annotations

import sys

from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow
from .theme import ThemeManager
from . import wheel_guard


def _resolve_app_icon() -> QIcon | None:
    """Find assets/quansheng-toolkit.ico on disk regardless of how the
    app is launched (source tree, pip install, PyInstaller --onefile).

    PyInstaller's --onefile bundle extracts data into ``sys._MEIPASS``;
    `--add-data assets;assets` in the build script lands the icon
    there. In the source tree the file lives next to ``launcher.py``.
    """
    import sys
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "assets" / "quansheng-toolkit.ico")
    # Source tree: assets/ is a sibling of the gui package's parent.
    candidates.append(
        Path(__file__).resolve().parent.parent.parent / "assets"
        / "quansheng-toolkit.ico"
    )
    for path in candidates:
        if path.is_file():
            return QIcon(str(path))
    return None


# Used by QSettings under the hood — keep these stable across releases.
ORG_NAME = "QuanshengToolkit"
APP_NAME = "QuanshengToolkit"


def _set_default_font(app: QApplication) -> None:
    """
    Pick the nicest available system font. Inter or Segoe UI Variable look
    great; on Linux/macOS we fall through to system defaults.
    """
    candidates = ("Inter", "Segoe UI Variable", "Segoe UI", "SF Pro Text",
                  "Helvetica Neue", "system-ui")
    for name in candidates:
        font = QFont(name)
        if font.exactMatch() or QFont(name).family() == name:
            app.setFont(QFont(name, 10))
            return
    app.setFont(QFont(app.font().family(), 10))


def run() -> int:
    QCoreApplication.setOrganizationName(ORG_NAME)
    QCoreApplication.setApplicationName(APP_NAME)
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    _set_default_font(app)

    # App icon — appears in the Windows taskbar, window title bar and
    # the alt-tab switcher. Resolved through a helper so it works in
    # source, pip-installed, and PyInstaller --onefile bundles.
    icon = _resolve_app_icon()
    if icon is not None:
        app.setWindowIcon(icon)

    # Block accidental wheel-edits on spinboxes / combos / sliders. The
    # filter must be kept alive for the lifetime of the app — store it
    # on the app object so the GC doesn't drop it.
    app._wheel_guard = wheel_guard.install(app)  # type: ignore[attr-defined]

    theme = ThemeManager()
    theme.apply(app)
    theme.paletteChanged.connect(lambda: theme.apply(app))

    window = MainWindow(theme)
    # Start maximized — with 9+ horizontal nav tabs the cramped 1280×820
    # default would clip the toolbar on most laptop screens. The user
    # can still un-maximize via the OS button if they want a small
    # window. setMinimumSize on MainWindow guards against ridiculous
    # shrinking.
    window.showMaximized()
    return app.exec()
