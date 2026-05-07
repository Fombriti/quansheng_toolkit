"""
DFU (Device Firmware Update) protocol for Quansheng K5/K6/K1/K5V3 radios
in bootloader mode.

This is the REAL protocol used by the public K5/K1 web flasher / the upstream K5/K1 tooling / kk7ds K5
flasher — same framing as the normal-mode EEPROM protocol (AB CD ...
DC BA + 16-byte XOR + CRC-CCITT) just at a different baudrate (38400)
and with bootloader-specific message types.

Read-only support today (Phase 2): we can connect to the bootloader,
observe its periodic NOTIFY_DEV_INFO broadcasts and read out the UID
+ bootloader version string. This is enough to identify the model
(UV-K5 V1/V2 vs K5 V3 vs K1 vs K5(8)) without writing anything.

Write paths (`flash_firmware`) are added in Phase 4 and are gated
behind explicit user confirmation + bootloader-version allowlist
checking.

Reference: the published K5/K1 references — js/protocol.js, js/k5-flash.js,
js/flash-k1.js (MIT-licensed).

DFU-mode entry:
  * Power off the radio. Plug USB. Hold PTT, then power-on while still
    holding PTT. The LCD should stay blank — that's bootloader mode.
  * The OS may enumerate a different serial port than normal mode.
"""
from __future__ import annotations

import struct
import time
from dataclasses import dataclass
from typing import Iterator

import serial
import serial.tools.list_ports


# ============================================================ #
#  Wire format                                                 #
# ============================================================ #
# Same as kradio.protocol but on a different baudrate. Inlined so this
# module can be used without pulling in the (longer) normal-mode helpers.

DFU_BAUDRATE = 38400
HEAD = b"\xAB\xCD"
TAIL = b"\xDC\xBA"

# 16-byte XOR key applied to inner-message + CRC (same as EEPROM packets).
XOR_KEY = bytes([
    0x16, 0x6c, 0x14, 0xe6, 0x2e, 0x91, 0x0d, 0x40,
    0x21, 0x35, 0xd5, 0x40, 0x13, 0x03, 0xe9, 0x80,
])

# Message types (u16 LE inside the inner message).
MSG_NOTIFY_DEV_INFO = 0x0518   # bootloader → host: UID + version, periodic
MSG_NOTIFY_BL_VER   = 0x0530   # host → bootloader: "I see you, your version is X"
MSG_PROG_FW         = 0x0519   # host → bootloader: write a 256-byte page
MSG_PROG_FW_RESP    = 0x051A   # bootloader → host: page write ACK


class DfuError(RuntimeError):
    """Raised on framing, CRC, or protocol errors during DFU."""


def _xor(data: bytes) -> bytes:
    return bytes(b ^ XOR_KEY[i % len(XOR_KEY)] for i, b in enumerate(data))


def _crc16_xmodem(data: bytes) -> int:
    """CRC-16/CCITT XMODEM (poly 0x1021, init 0). Same as protocol.py."""
    crc = 0
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc <<= 1
            if crc & 0x10000:
                crc = (crc ^ 0x1021) & 0xFFFF
    return crc & 0xFFFF


def build_packet(msg_type: int, data: bytes = b"") -> bytes:
    """
    Wrap a message into the on-wire DFU packet:

        HEAD(2) + msg_len(u16 LE) + obf(inner+crc) + TAIL(2)

    where `inner` is the deobfuscated payload:

        msg_type(u16 LE) + data_len(u16 LE) + data(N)

    The inner length field reports `data_len` (the user-data portion,
    not the 4-byte msg header). The wire `msg_len` is the inner length
    INCLUDING the 4-byte header — equal to `len(inner)`.

    Inner+CRC are XOR-obfuscated together (CRC is computed on the
    de-obfuscated bytes).
    """
    inner = struct.pack("<HH", msg_type, len(data)) + data
    # Quansheng radios pad inner to even length before adding the CRC.
    if len(inner) & 1:
        inner += b"\x00"
    crc = _crc16_xmodem(inner)
    obf = _xor(inner + struct.pack("<H", crc))
    return HEAD + struct.pack("<H", len(inner)) + obf + TAIL


@dataclass(frozen=True)
class DfuMessage:
    msg_type: int
    data: bytes        # the user-data portion (length = data_len from header)


def parse_packet(buf: bytes, *, verify_crc: bool = False) -> DfuMessage:
    """
    Parse a single complete on-wire packet. Caller has already split out
    a sequence that starts with HEAD and ends with TAIL.

    `verify_crc` defaults to False because the radio's bootloader sends
    0xFFFF as a "skip CRC" sentinel for its broadcasts (verified live
    on a UV-K5 V3 with bl 7.00.07 — the deobfuscated CRC field reads
    0xFFFF, not the actual CRC of the message). the upstream parser
    `unpacketize` likewise doesn't validate the inbound CRC; we follow
    that policy. We DO compute correct CRCs on outbound packets in
    `build_packet`, since the bootloader does validate those.
    """
    if len(buf) < 8:
        raise DfuError(f"packet too short: {len(buf)} bytes")
    if buf[:2] != HEAD:
        raise DfuError(f"bad head: {buf[:2].hex()}")
    if buf[-2:] != TAIL:
        raise DfuError(f"bad tail: {buf[-2:].hex()}")
    msg_len = struct.unpack("<H", buf[2:4])[0]
    if len(buf) != 4 + msg_len + 2 + 2:
        raise DfuError(f"length mismatch: declared {msg_len}, got {len(buf) - 8}")
    obf = buf[4:4 + msg_len + 2]
    inner_with_crc = _xor(obf)
    inner = inner_with_crc[:msg_len]
    if verify_crc:
        crc_recv = struct.unpack("<H", inner_with_crc[msg_len:msg_len + 2])[0]
        crc_calc = _crc16_xmodem(inner)
        if crc_recv != crc_calc:
            raise DfuError(
                f"CRC mismatch: declared 0x{crc_recv:04X}, "
                f"computed 0x{crc_calc:04X}"
            )
    msg_type, data_len = struct.unpack_from("<HH", inner, 0)
    if 4 + data_len > msg_len:
        raise DfuError(
            f"inner data_len overflow: data_len={data_len}, msg_len={msg_len}"
        )
    data = inner[4:4 + data_len]
    return DfuMessage(msg_type=msg_type, data=data)


def iter_packets(buf: bytearray) -> Iterator[DfuMessage]:
    """
    Stream parser: yield each complete packet found at the start of `buf`,
    consuming it from the buffer as it goes. Stops when the buffer no
    longer contains a complete packet (caller appends more data and calls
    again). Garbage before HEAD is silently discarded.
    """
    while True:
        # Find HEAD.
        try:
            i = buf.index(HEAD[0])
        except ValueError:
            buf.clear()
            return
        # Drop bytes before HEAD.
        if i:
            del buf[:i]
        if len(buf) < 4:
            return
        if buf[1] != HEAD[1]:
            del buf[0]
            continue
        msg_len = struct.unpack("<H", bytes(buf[2:4]))[0]
        total = 4 + msg_len + 2 + 2
        if len(buf) < total:
            return
        try:
            msg = parse_packet(bytes(buf[:total]))
        except DfuError:
            # Malformed packet — drop the head byte and try the next match.
            del buf[0]
            continue
        del buf[:total]
        yield msg


# ============================================================ #
#  Connection                                                  #
# ============================================================ #

@dataclass
class DfuPort:
    port: serial.Serial
    _rx: bytearray = None  # type: ignore[assignment]

    def __post_init__(self):
        if self._rx is None:
            self._rx = bytearray()

    def send(self, packet: bytes) -> None:
        n = self.port.write(packet)
        if n != len(packet):
            raise DfuError("incomplete serial write")

    def poll_messages(self) -> list[DfuMessage]:
        """
        Read everything currently in the kernel RX buffer (plus whatever
        arrives during the port's read timeout window) and return a list
        of all complete messages seen so far. We ask for a buffer-sized
        read so a busy bootloader broadcasting every ~50 ms isn't read
        a single byte at a time.
        """
        # Drain whatever's currently buffered in one shot.
        in_w = self.port.in_waiting
        if in_w:
            chunk = self.port.read(in_w)
        else:
            # Block for up to the port's timeout window for the first
            # byte, then drain anything else that piled up while we
            # were waiting.
            chunk = self.port.read(1)
            if chunk and self.port.in_waiting:
                chunk += self.port.read(self.port.in_waiting)
        if chunk:
            self._rx.extend(chunk)
        return list(iter_packets(self._rx))


def open_dfu(port_name: str, timeout: float = 0.2) -> DfuPort:
    """Open the bootloader port at 38400 8N1, short read timeout."""
    port = serial.Serial(
        port=port_name, baudrate=DFU_BAUDRATE,
        bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE, timeout=timeout,
        xonxoff=False, rtscts=False, dsrdtr=False,
    )
    return DfuPort(port=port)


# ============================================================ #
#  Bootloader detection / handshake                            #
# ============================================================ #

@dataclass(frozen=True)
class BootloaderInfo:
    """What we know about a connected bootloader."""
    uid: bytes               # 16 bytes
    bl_version: str          # e.g. "7.00.07", "5.00.01", "2.00.06"

    @property
    def uid_hex(self) -> str:
        return self.uid.hex()


def _decode_bl_version(raw: bytes) -> str:
    """Strip trailing nulls and return the bootloader version string."""
    end = raw.find(b"\x00")
    if end < 0:
        end = len(raw)
    return raw[:end].decode("ascii", errors="replace").strip()


def wait_for_dev_info(rp: DfuPort, *,
                      observation_count: int = 2,
                      timeout: float = 15.0) -> BootloaderInfo:
    """
    Listen for periodic NOTIFY_DEV_INFO broadcasts from the bootloader.
    The bootloader emits one every ~50–100 ms while it's idle. We wait
    until we've seen `observation_count` of them with valid framing +
    CRC (the framing itself + checksum is enough to filter line noise;
    earlier we also enforced inter-arrival timing but that was tripped
    up by buffered reads where multiple frames arrive together).

    Returns the parsed UID + bootloader version from the latest sighting.
    Raises DfuError on timeout.
    """
    deadline = time.time() + timeout
    seen = 0
    info: BootloaderInfo | None = None

    while time.time() < deadline:
        for msg in rp.poll_messages():
            if msg.msg_type != MSG_NOTIFY_DEV_INFO:
                continue
            if len(msg.data) < 32:
                continue
            seen += 1
            uid = msg.data[:16]
            version = _decode_bl_version(msg.data[16:32])
            info = BootloaderInfo(uid=uid, bl_version=version)
            if seen >= observation_count and info.bl_version:
                return info

    if info is not None:
        raise DfuError(
            f"only saw {seen} NOTIFY_DEV_INFO broadcasts; need "
            f"{observation_count}. Last version observed: "
            f"{info.bl_version!r}"
        )
    raise DfuError(
        "no NOTIFY_DEV_INFO received. Make sure the radio is in DFU "
        "mode (PTT held while powering on) and on the right serial port."
    )


# ============================================================ #
#  Anti-brick: bootloader → model mapping                      #
# ============================================================ #
# Source: the published K1 flash reference BOOTLOADER_TO_MODEL.

# NB: bootloader 7.00.07 is shared by UV-K5 V3 AND UV-K1(8) v3 "Mini Kong"
# — same PY32F071 MCU, same bootloader binary, same firmware payload.
# `identify_model` returns the combined label so users with a K1(8) v3
# don't see a misleading "K5 V3" detection. The toolkit's flash target
# (`k5_v3`) is unchanged for backwards compat — both radios accept the
# same firmware bytes, so renaming it would be cosmetic only.
BOOTLOADER_TO_MODEL: dict[str, str] = {
    "5.00.01": "UV-K5 V2",
    "2.00.06": "UV-K5 V1",
    "7.00.07": "UV-K5 V3 / UV-K1(8) v3",
    "7.02.02": "UV-K1",
    "7.03.01": "UV-K1",
    "7.03.02": "UV-K1",
    "7.03.03": "UV-K1",
}

# These bootloaders MUST refuse K1/K5V3 firmware — flashing it will
# brick the radio because the chip and flash layout differ.
BLOCKED_FOR_K1_FAMILY: frozenset[str] = frozenset({"5.00.01", "2.00.06"})


# ----------------------------------------------------------------------
# Flash-target allowlist (Phase 3)
# ----------------------------------------------------------------------
# Canonical firmware-target names — what the user declares they're
# flashing. Each maps to the bootloader versions that can SAFELY accept
# that firmware. Anything outside is refused.
#
# Source: the published K5/K1 references separate flash pages — the K5/K6 page
# blocks K1/K5V3 bootloaders, and the K1 page blocks K5 V1/V2 (the
# brick-list).

FLASH_TARGET_K5_K6 = "k5_k6"        # UV-K5 / UV-K5(8) / UV-K6 / UV-5R Plus
FLASH_TARGET_K5_V3 = "k5_v3"        # UV-K5 V3
FLASH_TARGET_K1    = "k1"            # UV-K1

ALLOWED_BOOTLOADERS_BY_TARGET: dict[str, frozenset[str]] = {
    FLASH_TARGET_K5_K6: frozenset({"5.00.01", "2.00.06"}),
    FLASH_TARGET_K5_V3: frozenset({"7.00.07"}),
    FLASH_TARGET_K1:    frozenset({"7.02.02", "7.03.01", "7.03.02", "7.03.03"}),
}

# Reverse map: bootloader → which firmware-target it accepts. Used by
# the GUI / CLI to suggest the right flash family after a handshake.
def target_for_bootloader(bl_version: str) -> str | None:
    bl = bl_version.strip()
    for target, allowed in ALLOWED_BOOTLOADERS_BY_TARGET.items():
        if bl in allowed:
            return target
    return None


def identify_model(version: str) -> str:
    """Resolve a bootloader version string to a human-readable model."""
    return BOOTLOADER_TO_MODEL.get(version.strip(), "unknown")


# ============================================================ #
#  Phase 4: flash write                                        #
# ============================================================ #
# Pages: 256 bytes each. PROG_FW message has a 12-byte header followed
# by the page data (4 byte timestamp + 2 byte page idx + 2 byte page
# count + 4 byte padding/reserved + 256 byte data). Mirrors
# flash-k1.js' programFirmware().

FLASH_PAGE_SIZE = 256


def perform_dfu_handshake(rp: "DfuPort", bl_version: str,
                          rounds: int = 3,
                          timeout: float = 5.0) -> None:
    """
    Phase 4 handshake step. The bootloader broadcasts NOTIFY_DEV_INFO
    until the host replies with NOTIFY_BL_VER carrying the first 4 chars
    of the bootloader version. After `rounds` exchanges the bootloader
    stops broadcasting and is ready to accept PROG_FW pages.

    Reproduces flash-k1.js#performHandshake.
    """
    bl4 = bl_version.encode("ascii", errors="replace")[:4].ljust(4, b"\x00")
    deadline = time.time() + timeout
    sent = 0
    while sent < rounds and time.time() < deadline:
        for msg in rp.poll_messages():
            if msg.msg_type == MSG_NOTIFY_DEV_INFO:
                rp.send(build_packet(MSG_NOTIFY_BL_VER, bl4))
                sent += 1
                time.sleep(0.05)
                break
    if sent < rounds:
        raise DfuError(
            f"DFU handshake incomplete ({sent}/{rounds} rounds). "
            f"The bootloader stopped broadcasting too early or the wrong "
            f"port was opened."
        )
    # Drain any leftover broadcasts so they don't confuse PROG_FW_RESP.
    rp.poll_messages()


def _build_prog_fw_message(timestamp: int, page_index: int,
                           page_count: int, page_data: bytes) -> bytes:
    """Construct the inner data for a PROG_FW packet (12+256 bytes)."""
    if len(page_data) > FLASH_PAGE_SIZE:
        raise ValueError(f"page must be <= {FLASH_PAGE_SIZE} bytes")
    if len(page_data) < FLASH_PAGE_SIZE:
        page_data = page_data + b"\x00" * (FLASH_PAGE_SIZE - len(page_data))
    return (
        struct.pack("<IHH", timestamp & 0xFFFFFFFF,
                    page_index & 0xFFFF, page_count & 0xFFFF)
        + b"\x00" * 4               # padding/reserved
        + page_data
    )


def flash_firmware(rp: "DfuPort", firmware: bytes, *,
                   on_progress=None,
                   on_log=None,
                   max_retries: int = 3,
                   timeout_per_page: float = 3.0) -> None:
    """
    Phase 4 — write `firmware` to the connected bootloader. Caller MUST
    have already:
      1. Run `wait_for_dev_info` to obtain `bl_version`
      2. Called `assert_safe_to_flash(bl_version, target)` to confirm
         the firmware is for this bootloader's family
      3. Called `perform_dfu_handshake(rp, bl_version)` to finalize
         the bootloader handover

    `on_progress(page_done, page_total)` and `on_log(msg)` are optional
    callbacks. Each page write is retried up to `max_retries` times on
    timeout or non-zero error code.

    Raises DfuError on any unrecoverable error.
    """
    log = on_log or (lambda _: None)
    progress = on_progress or (lambda *_args: None)

    page_count = (len(firmware) + FLASH_PAGE_SIZE - 1) // FLASH_PAGE_SIZE
    if page_count == 0:
        raise DfuError("empty firmware")
    timestamp = int(time.time()) & 0xFFFFFFFF
    log(f"Flashing {len(firmware)} bytes in {page_count} pages "
        f"(timestamp 0x{timestamp:08X})")

    page_index = 0
    retries = 0
    while page_index < page_count:
        progress(page_index, page_count)

        offset = page_index * FLASH_PAGE_SIZE
        chunk = firmware[offset:offset + FLASH_PAGE_SIZE]
        inner = _build_prog_fw_message(timestamp, page_index, page_count, chunk)
        rp.send(build_packet(MSG_PROG_FW, inner))

        deadline = time.time() + timeout_per_page
        ack = False
        last_err: int | None = None
        while time.time() < deadline:
            for msg in rp.poll_messages():
                if msg.msg_type == MSG_NOTIFY_DEV_INFO:
                    continue
                if msg.msg_type != MSG_PROG_FW_RESP or len(msg.data) < 8:
                    continue
                resp_idx, err_code = struct.unpack_from("<HH", msg.data, 4)
                if resp_idx != page_index:
                    continue
                if err_code != 0:
                    last_err = err_code
                    break
                ack = True
                break
            if ack or last_err is not None:
                break

        if ack:
            retries = 0
            page_index += 1
            continue

        retries += 1
        if retries > max_retries:
            raise DfuError(
                f"page {page_index}/{page_count} failed after "
                f"{max_retries} retries"
                + (f" (last error code 0x{last_err:04X})" if last_err is not None else " (timeout)")
            )
        log(f"Page {page_index + 1}/{page_count} retry #{retries}"
            + (f" (err 0x{last_err:04X})" if last_err is not None else " (timeout)"))

    progress(page_count, page_count)
    log("Flash complete.")


def assert_safe_to_flash(bl_version: str, firmware_target: str) -> None:
    """
    Phase-3 anti-brick gate. Call this BEFORE any flash write.

    Raises DfuError when:
      * the bootloader version is unknown (refuse out of caution)
      * the firmware_target name is not one of the canonical constants
      * the bootloader is not in the allowlist for this target

    The bootloader/firmware combinations that have been observed to
    permanently brick the radio are explicitly enumerated in the doc
    string of `ALLOWED_BOOTLOADERS_BY_TARGET`.
    """
    bl = bl_version.strip()
    if firmware_target not in ALLOWED_BOOTLOADERS_BY_TARGET:
        valid = ", ".join(sorted(ALLOWED_BOOTLOADERS_BY_TARGET))
        raise DfuError(
            f"unknown firmware_target {firmware_target!r}; "
            f"expected one of: {valid}"
        )
    allowed = ALLOWED_BOOTLOADERS_BY_TARGET[firmware_target]
    if bl in allowed:
        return  # safe

    if not bl:
        raise DfuError(
            "no bootloader version detected — cannot validate flash "
            "target. Re-enter DFU mode and try again."
        )
    if bl not in BOOTLOADER_TO_MODEL:
        raise DfuError(
            f"unknown bootloader version {bl!r}. Refusing to flash "
            f"{firmware_target} firmware because we cannot prove the "
            f"radio is compatible. Add this bootloader to "
            f"BOOTLOADER_TO_MODEL only after confirming on hardware."
        )
    detected_model = BOOTLOADER_TO_MODEL[bl]
    raise DfuError(
        f"BRICK PROTECTION: refusing to flash {firmware_target} "
        f"firmware onto bootloader {bl} ({detected_model}).\n"
        f"This bootloader expects "
        f"{target_for_bootloader(bl) or 'an unknown'} firmware. "
        f"Flashing the wrong family has been observed to permanently "
        f"brick the radio (different MCU / flash layout)."
    )


# ============================================================ #
#  Port discovery (best-effort)                                #
# ============================================================ #

def find_dfu_port() -> str | None:
    """
    Best-effort scan for a port that *might* be the radio in DFU. The
    enumerated VID:PID is usually the same as in normal mode for the K5
    family (CH340/CH9102 cable) but can change for the K1/K5V3 native
    USB-C bridges. Caller should always confirm by attempting a
    handshake.
    """
    candidates: list[str] = []
    for p in serial.tools.list_ports.comports():
        desc = (p.description or "").lower()
        hwid = (p.hwid or "").upper()
        if "VID:PID=36B7" in hwid:
            candidates.append(p.device)
        elif "ch340" in desc or "ch9102" in desc:
            candidates.append(p.device)
        elif "usb-serial" in desc or "usb serial" in desc:
            candidates.append(p.device)
    return candidates[0] if candidates else None
