"""
kradio — toolkit for managing Quansheng UV-K1 / UV-K5 V3 radios running the
F4HWN Fusion 5.x custom firmware via the USB serial bridge.

Public API:
    Radio: high-level radio session
    EepromImage: in-memory EEPROM with typed accessors
    profiles: detected/declared per-radio specifics
"""
from .protocol import (
    RadioPort,
    RadioError,
    open_radio,
    auto_detect_port,
    hello,
    read_mem,
    write_mem,
    reset_radio,
    BAUDRATE,
    MEM_BLOCK,
)

__version__ = "0.1.0"
