"""
Toolkit-level preferences. These live OUTSIDE the radio's EEPROM and are
persisted via QSettings under the application's organization/app name.

These are user choices that affect the toolkit's behaviour (default port,
backups, confirmations, logging, …) — never anything that goes onto the
radio.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QSettings, QStandardPaths, Signal


GROUP = "preferences"


def _default_backup_dir() -> str:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    if not base:
        base = str(Path.home() / ".quansheng_toolkit")
    return str(Path(base) / "backups")


class Prefs(QObject):
    """
    Typed wrapper around QSettings for the user-visible toolkit preferences.
    Emits `changed` whenever a setter writes a new value.
    """

    changed = Signal(str)   # emits the key that changed

    # ---- defaults ---------------------------------------------------------

    DEFAULTS: dict[str, Any] = {
        # connection
        "default_port": "",                 # "" = auto-detect
        # backups
        "backup_dir": "",                   # "" = system default
        "auto_backup_before_apply": True,
        "backup_retention": 30,             # keep last N
        # behavior
        "confirm_apply": True,
        "show_powercycle_hint": True,
        "auto_reload_after_apply": False,
        "reset_radio_after_apply": True,
        # appearance (theme is owned by ThemeManager; keep these here only
        # for things that don't already have a manager)
        "remember_window_geometry": True,
        # advanced
        "log_level": "INFO",
        "show_hex_viewer": False,
        # one-time disclaimers — true once dismissed
        "hex_viewer_disclaimer_dismissed": False,
    }

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._s = QSettings()

    # ---- generic API ------------------------------------------------------

    def _get(self, key: str) -> Any:
        self._s.beginGroup(GROUP)
        try:
            raw = self._s.value(key, self.DEFAULTS[key])
        finally:
            self._s.endGroup()
        # QSettings preserves types poorly across platforms — re-cast.
        default = self.DEFAULTS[key]
        if isinstance(default, bool):
            if isinstance(raw, str):
                return raw.lower() in ("1", "true", "yes", "on")
            return bool(int(raw)) if isinstance(raw, (int, float)) else bool(raw)
        if isinstance(default, int):
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default
        return raw if raw is not None else default

    def _set(self, key: str, value: Any) -> None:
        if key not in self.DEFAULTS:
            raise KeyError(key)
        self._s.beginGroup(GROUP)
        try:
            self._s.setValue(key, value)
        finally:
            self._s.endGroup()
        self.changed.emit(key)

    # ---- typed accessors --------------------------------------------------

    @property
    def default_port(self) -> str:
        return self._get("default_port") or ""

    @default_port.setter
    def default_port(self, v: str) -> None:
        self._set("default_port", v or "")

    @property
    def backup_dir(self) -> str:
        v = self._get("backup_dir")
        return v or _default_backup_dir()

    @backup_dir.setter
    def backup_dir(self, v: str) -> None:
        self._set("backup_dir", v or "")

    @property
    def auto_backup_before_apply(self) -> bool:
        return self._get("auto_backup_before_apply")

    @auto_backup_before_apply.setter
    def auto_backup_before_apply(self, v: bool) -> None:
        self._set("auto_backup_before_apply", bool(v))

    @property
    def backup_retention(self) -> int:
        return max(1, int(self._get("backup_retention")))

    @backup_retention.setter
    def backup_retention(self, v: int) -> None:
        self._set("backup_retention", max(1, int(v)))

    @property
    def confirm_apply(self) -> bool:
        return self._get("confirm_apply")

    @confirm_apply.setter
    def confirm_apply(self, v: bool) -> None:
        self._set("confirm_apply", bool(v))

    @property
    def show_powercycle_hint(self) -> bool:
        return self._get("show_powercycle_hint")

    @show_powercycle_hint.setter
    def show_powercycle_hint(self, v: bool) -> None:
        self._set("show_powercycle_hint", bool(v))

    @property
    def auto_reload_after_apply(self) -> bool:
        return self._get("auto_reload_after_apply")

    @auto_reload_after_apply.setter
    def auto_reload_after_apply(self, v: bool) -> None:
        self._set("auto_reload_after_apply", bool(v))

    @property
    def reset_radio_after_apply(self) -> bool:
        return self._get("reset_radio_after_apply")

    @reset_radio_after_apply.setter
    def reset_radio_after_apply(self, v: bool) -> None:
        self._set("reset_radio_after_apply", bool(v))

    @property
    def remember_window_geometry(self) -> bool:
        return self._get("remember_window_geometry")

    @remember_window_geometry.setter
    def remember_window_geometry(self, v: bool) -> None:
        self._set("remember_window_geometry", bool(v))

    @property
    def log_level(self) -> str:
        v = self._get("log_level")
        return v if v in ("DEBUG", "INFO", "WARNING", "ERROR") else "INFO"

    @log_level.setter
    def log_level(self, v: str) -> None:
        self._set("log_level", v)

    @property
    def show_hex_viewer(self) -> bool:
        return self._get("show_hex_viewer")

    @show_hex_viewer.setter
    def show_hex_viewer(self, v: bool) -> None:
        self._set("show_hex_viewer", bool(v))

    @property
    def hex_viewer_disclaimer_dismissed(self) -> bool:
        return self._get("hex_viewer_disclaimer_dismissed")

    @hex_viewer_disclaimer_dismissed.setter
    def hex_viewer_disclaimer_dismissed(self, v: bool) -> None:
        self._set("hex_viewer_disclaimer_dismissed", bool(v))
