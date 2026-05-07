"""
Serial protocol for Quansheng UV-K1 and UV-K5 V3 radios running the F4HWN
Fusion 5.x custom firmware.

Reverse-engineered by the community; this module follows the reference
implementation in the bundled CHIRP driver (`f4hwn.fusion.chirp.v5.4.0.py`,
which is itself based on sq5bpf's reverse engineering work).

Frame layout (on the wire):
    AB CD <len_lo> <len_hi> <obfuscated payload || crc16> DC BA

Obfuscation is a per-byte XOR against a fixed 16-byte key. CRC is CRC-16/CCITT
(poly=0x1021, init=0x0000) computed over the unobfuscated payload.

Known firmware quirk (F4HWN Fusion 5.4 on UV-K1):
    Mixing reads and writes in the same session — particularly opening the
    port a second time after a previous open/close — can lock up the radio.
    The reliable pattern is what CHIRP does: a single fresh session that is
    *only* sequential block writes from offset 0x0000 up to PROG_SIZE, ending
    with a reset packet. Use `reset_radio()` at the end of a write session.
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass

import serial
import serial.tools.list_ports


# 16-byte XOR key. The last byte is 0x80, not 0x28 as some older docs claim;
# the bundled CHIRP driver is authoritative.
XOR_KEY = bytes([
    0x16, 0x6C, 0x14, 0xE6, 0x2E, 0x91, 0x0D, 0x40,
    0x21, 0x35, 0xD5, 0x40, 0x13, 0x03, 0xE9, 0x80,
])

FRAME_HEAD = b"\xAB\xCD"
FRAME_TAIL = b"\xDC\xBA"
# Trailer the radio expects on every command. the upstream K5/K1 tooling (and the
# kk7ds K5 flasher it forks from) sends a fixed 0xFFFFFFFF here on every
# packet — the radio ignores the actual bytes but expects the slot to
# be present. Older code in this toolkit sent 0x6a395764 (a recovered
# value from an early packet capture) which the K1 with F4HWN accepted,
# but the K5 V3 stock firmware silently rejected: writes were ACKed
# (0x1E reply) but the bytes never made it to the cal region.
# Aligned to the upstream value 2026-05-04.
SESSION_MAGIC = b"\xff\xff\xff\xff"

BAUDRATE = 38400

# Default timeout (seconds). CHIRP uses 4s for read/write phases. We default
# to 10s to match the k5prog (sq5bpf) C reference, which is more forgiving
# when the radio is slow to respond after many requests.
DEFAULT_TIMEOUT = 10.0

# Defensive sleep after every write. The firmware seems to need a small
# moment between consecutive writes to commit reliably.
POST_WRITE_SLEEP = 0.05

# Memory layout constants (F4HWN Fusion 5.x).
# `MEM_BLOCK` is the size of a single read/write transaction over the
# wire. the upstream K5/K1 tooling and kk7ds K5 flasher both use 64 bytes — sticking
# with that for byte-for-byte compatibility with every Quansheng radio
# we've verified. Larger blocks (we used 128 before 2026-05-04) work
# on the K1 with F4HWN but are silently dropped by K5 V3 stock 7.00.x.
MEM_BLOCK = 0x40
MEM_SIZE = 0xB190         # total EEPROM size including calibration
PROG_SIZE = 0xB000        # writeable region (anything above is calibration)
CAL_START = 0xB000        # NEVER WRITE here without explicit calibration mode


class RadioError(RuntimeError):
    """Raised on any radio-side communication error."""


# ------------------------------------------------------------------------- #
# Frame primitives                                                          #
# ------------------------------------------------------------------------- #

def xor_payload(data: bytes) -> bytes:
    """Apply the 16-byte XOR obfuscation. Self-inverse."""
    return bytes(b ^ XOR_KEY[i % len(XOR_KEY)] for i, b in enumerate(data))


def crc16_xmodem(data: bytes) -> int:
    """CRC-16/CCITT (XMODEM variant): poly=0x1021, init=0x0000."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc <<= 1
            if crc & 0x10000:
                crc = (crc ^ 0x1021) & 0xFFFF
    return crc & 0xFFFF


def build_frame(payload: bytes) -> bytes:
    """Wrap a payload into the on-wire frame (head + len + obf+crc + tail)."""
    crc = crc16_xmodem(payload)
    body = payload + struct.pack("<H", crc)
    return (
        FRAME_HEAD
        + struct.pack("<H", len(payload))
        + xor_payload(body)
        + FRAME_TAIL
    )


def parse_frame(header: bytes, body: bytes, footer: bytes) -> bytes:
    """Validate framing bytes and return the de-obfuscated body."""
    if len(header) != 4 or header[0] != 0xAB or header[1] != 0xCD or header[3] != 0x00:
        raise RadioError(f"bad frame header: {header.hex()}")
    if len(footer) != 4 or footer[2] != 0xDC or footer[3] != 0xBA:
        raise RadioError(f"bad frame footer: {footer.hex()}")
    return xor_payload(body)


# ------------------------------------------------------------------------- #
# Port wrapper                                                              #
# ------------------------------------------------------------------------- #

@dataclass
class RadioPort:
    """Thin wrapper around pyserial that does framed I/O."""
    port: serial.Serial

    def send(self, payload: bytes) -> None:
        frame = build_frame(payload)
        n = self.port.write(frame)
        if n != len(frame):
            raise RadioError("incomplete serial write")

    def recv(self) -> bytes:
        header = self.port.read(4)
        if len(header) != 4:
            raise RadioError(f"short header read ({len(header)} bytes)")
        body_len = header[2]
        body = self.port.read(body_len)
        if len(body) != body_len:
            raise RadioError(f"short body read ({len(body)}/{body_len})")
        footer = self.port.read(4)
        if len(footer) != 4:
            raise RadioError(f"short footer read ({len(footer)} bytes)")
        return parse_frame(header, body, footer)

    def round_trip(self, payload: bytes) -> bytes:
        self.send(payload)
        return self.recv()


# ------------------------------------------------------------------------- #
# Port discovery / open                                                     #
# ------------------------------------------------------------------------- #

# USB IDs for radios we can talk to. The K1 has a native USB-C bridge
# enumerating as VID 36B7. The K5 / K5(8) typically uses the supplied CH340
# cable. CH9102 clones also exist.
KNOWN_RADIO_VIDS = {
    0x36B7: "Quansheng UV-K1 native USB-C bridge",
}
KNOWN_USB_BRIDGE_KEYWORDS = ("ch340", "ch9102", "wch", "qinheng")


def auto_detect_port() -> str | None:
    """
    Try to find a connected Quansheng radio. Prefers native UV-K1 USB-C
    (VID 36B7); falls back to typical CH340/CH9102 USB-serial cables; last
    resort: any "USB Serial Device".
    """
    preferred: list[str] = []
    fallback: list[str] = []
    for p in serial.tools.list_ports.comports():
        desc = (p.description or "").lower()
        manuf = (p.manufacturer or "").lower()
        hwid = (p.hwid or "").upper()
        if any(f"VID:PID={vid:04X}" in hwid for vid in KNOWN_RADIO_VIDS):
            preferred.append(p.device)
        elif any(k in desc or k in manuf for k in KNOWN_USB_BRIDGE_KEYWORDS):
            preferred.append(p.device)
        elif "usb-serial" in desc or "usb serial" in desc \
                or "dispositivo seriale usb" in desc:
            fallback.append(p.device)
    if preferred:
        return preferred[0]
    if fallback:
        return fallback[0]
    return None


def open_radio(port_name: str | None = None,
               timeout: float = DEFAULT_TIMEOUT) -> RadioPort:
    """
    Open the serial port with parameters matching CHIRP's known-good config:
    8N1, no flow control, dtr/rts asserted (pyserial defaults).
    """
    if port_name is None:
        port_name = auto_detect_port()
        if port_name is None:
            raise RadioError(
                "No serial port found for the radio. "
                "Pass --port (e.g. COM5 or /dev/ttyUSB0)."
            )
    port = serial.Serial(
        port=port_name,
        baudrate=BAUDRATE,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=timeout,
        xonxoff=False,
        rtscts=False,
        dsrdtr=False,
    )
    return RadioPort(port=port)


# ------------------------------------------------------------------------- #
# High-level commands                                                       #
# ------------------------------------------------------------------------- #

def hello(rp: RadioPort, retries: int = 5) -> str:
    """
    Handshake. Returns the firmware version string. If the radio replies
    with 0x18 0x05 it is in flash/programming mode and must be rebooted
    into normal operation first.
    """
    pkt = b"\x14\x05\x04\x00" + SESSION_MAGIC
    last_err: Exception | None = None
    for _ in range(retries):
        try:
            rep = rp.round_trip(pkt)
            if rep:
                if rep.startswith(b"\x18\x05"):
                    raise RadioError(
                        "Radio is in flash/programming mode. Power-cycle it "
                        "into normal operation first."
                    )
                fw_chars = []
                for b in rep[4:28]:
                    if b < 0x20 or b > 0x7E:
                        break
                    fw_chars.append(chr(b))
                return "".join(fw_chars)
        except Exception as e:
            last_err = e
            time.sleep(0.1)
    raise RadioError(f"hello failed after {retries} retries: {last_err}")


def read_mem(rp: RadioPort, offset: int, length: int) -> bytes:
    """Read up to 255 bytes from EEPROM starting at `offset`."""
    if length < 1 or length > 0xFF:
        raise ValueError(f"length must be 1..255, got {length}")
    if offset < 0 or offset + length > MEM_SIZE:
        raise ValueError(
            f"offset/length out of range (offset=0x{offset:04x}, len={length})"
        )
    pkt = b"\x1b\x05\x08\x00" + struct.pack("<HBB", offset, length, 0) + SESSION_MAGIC
    rep = rp.round_trip(pkt)
    if len(rep) < 8 + length:
        raise RadioError(f"read response too short: {len(rep)} bytes")
    return rep[8:8 + length]


def read_block_chunked(rp: RadioPort, offset: int, length: int,
                       block: int = MEM_BLOCK,
                       progress_cb=None) -> bytes:
    """Read an arbitrary range in `block`-sized chunks."""
    out = bytearray()
    addr = offset
    end = offset + length
    while addr < end:
        chunk = min(block, end - addr)
        out.extend(read_mem(rp, addr, chunk))
        if progress_cb:
            progress_cb(addr - offset + chunk, length)
        addr += chunk
    return bytes(out)


def write_mem(rp: RadioPort, offset: int, data: bytes) -> bool:
    """
    Write `data` to EEPROM at `offset`. The firmware acks with a 0x1e
    response that echoes back the offset.
    """
    dlen = len(data)
    if dlen < 1 or dlen > 0xFF:
        raise ValueError(f"data length must be 1..255, got {dlen}")
    if offset + dlen > MEM_SIZE:
        raise ValueError("offset+len out of range")
    pkt = (
        b"\x1d\x05"
        + struct.pack("<BBHBB", dlen + 8, 0, offset, dlen, 1)
        + SESSION_MAGIC
        + data
    )
    rep = rp.round_trip(pkt)
    ok = (
        len(rep) >= 6
        and rep[0] == 0x1E
        and rep[4] == (offset & 0xFF)
        and rep[5] == (offset >> 8) & 0xFF
    )
    if not ok:
        raise RadioError(
            f"unexpected write response: {rep.hex() if rep else '(empty)'}"
        )
    time.sleep(POST_WRITE_SLEEP)
    return True


def reset_radio(rp: RadioPort) -> None:
    """Tell the radio to reboot. CHIRP sends this at the end of an upload."""
    rp.send(b"\xdd\x05\x00\x00")
