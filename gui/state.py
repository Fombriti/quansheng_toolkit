"""
Centralised mutable state for the GUI.

The application keeps a single in-memory EEPROM image plus a "dirty" flag
that flips to True any time a view edits one or more bytes. The Apply
flow snapshots this image and uploads it via the CHIRP-style sequential
write workflow.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from PySide6.QtCore import QObject, Signal

from ..kradio.models import (
    DEFAULT_PROFILE,
    RadioProfile,
    is_recognized_firmware,
    select_profile,
)


@dataclass
class _State:
    port_name: str | None = None
    firmware: str = ""
    eeprom: bytearray | None = None
    dirty: bool = False
    profile: RadioProfile = DEFAULT_PROFILE
    profile_recognized: bool = False


class AppState(QObject):
    """Observable container for the current EEPROM image."""

    changed = Signal()           # Anything in the state changed.
    eeprom_loaded = Signal()     # A complete EEPROM is now available.
    dirty_changed = Signal(bool) # The image has been modified.

    def __init__(self):
        super().__init__()
        self._s = _State()

    # ---- Properties -------------------------------------------------------

    @property
    def port_name(self) -> str | None:
        return self._s.port_name

    @port_name.setter
    def port_name(self, value: str | None) -> None:
        if self._s.port_name != value:
            self._s.port_name = value
            self.changed.emit()

    @property
    def firmware(self) -> str:
        return self._s.firmware

    @firmware.setter
    def firmware(self, value: str) -> None:
        if self._s.firmware != value:
            self._s.firmware = value
            self.changed.emit()

    @property
    def eeprom(self) -> bytearray | None:
        return self._s.eeprom

    @property
    def dirty(self) -> bool:
        return self._s.dirty

    @property
    def has_image(self) -> bool:
        return self._s.eeprom is not None

    # ---- Mutations --------------------------------------------------------

    @property
    def profile(self) -> RadioProfile:
        return self._s.profile

    @property
    def profile_recognized(self) -> bool:
        return self._s.profile_recognized

    def set_profile_from_firmware(self, fw: str) -> None:
        self._s.profile = select_profile(fw)
        self._s.profile_recognized = is_recognized_firmware(fw)
        self.changed.emit()

    def set_eeprom(self, data: bytes | bytearray) -> None:
        self._s.eeprom = bytearray(data)
        self._set_dirty(False)
        self.eeprom_loaded.emit()
        self.changed.emit()

    def mark_dirty(self) -> None:
        self._set_dirty(True)
        self.changed.emit()

    def mark_clean(self) -> None:
        self._set_dirty(False)
        self.changed.emit()

    def _set_dirty(self, value: bool) -> None:
        if self._s.dirty != value:
            self._s.dirty = value
            self.dirty_changed.emit(value)
