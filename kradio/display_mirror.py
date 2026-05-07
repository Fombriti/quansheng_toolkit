"""
Display Mirror — live framebuffer streaming from F4HWN radios.

F4HWN ships an `ENABLE_FEAT_F4HWN_SCREENSHOT` feature that, when the
host sends a tiny keepalive packet, broadcasts the radio's 128×64
monochrome framebuffer over the serial line at ~10 Hz. This module
implements the host side: keepalive sender + frame parser. Wire it
to a Qt canvas and you get a real-time mirror of the radio display.

Protocol (reverse-engineered from the published F4HWN display-mirror implementation
which is itself based on https://github.com/armel/k5viewer):

    Host → Radio:  0x55 0xAA 0x00 0x00     (keepalive, every ~1s)

    Radio → Host:  [0xFF]                  (optional sync byte)
                   0xAA 0x55                (frame magic)
                   <type:1>                 (0x01 = full, 0x02 = diff)
                   <len:2 BE>               (payload length)
                   <payload:len bytes>
                   <trailer:1>              (presumed checksum, ignored)

    Full frame (type=0x01, len=1024): payload IS the framebuffer.
        Bit (y * 128 + x) → byte (idx >> 3), bit (idx & 7).

    Diff frame (type=0x02, len = N * 9):
        Each 9-byte chunk: <byte_group_idx:1> <data:8>
        Replaces framebuffer[idx*8 : idx*8+8] = data[1:9].

Threading: this module runs on a background thread (the GUI uses a
QThread; CLI/tests can use threading.Thread). Frames are delivered
via a callback. Stopping is cooperative — set `running` to False.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Iterator

from . import protocol as proto


DISPLAY_WIDTH = 128
DISPLAY_HEIGHT = 64
FRAMEBUFFER_SIZE = DISPLAY_WIDTH * DISPLAY_HEIGHT // 8   # = 1024 bytes

KEEPALIVE_PACKET = bytes([0x55, 0xAA, 0x00, 0x00])
KEEPALIVE_INTERVAL_SEC = 1.0

FRAME_MAGIC = bytes([0xAA, 0x55])
FRAME_TYPE_FULL = 0x01
FRAME_TYPE_DIFF = 0x02


@dataclass
class ParsedFrame:
    """One framebuffer-update message extracted from the serial stream."""
    type_: int             # 0x01 full, 0x02 diff
    payload: bytes
    trailer: int


def parse_frames(buf: bytearray) -> tuple[list[ParsedFrame], bytearray]:
    """Pull as many complete frames as possible out of `buf`.

    Returns (frames, leftover). Leftover is whatever bytes are at the
    front of the buffer that don't yet form a complete frame — pass it
    back in on the next call (concatenated with newly-read bytes).
    """
    frames: list[ParsedFrame] = []
    pos = 0
    n = len(buf)
    while pos < n:
        # Optional 0xFF sync byte before the magic.
        magic_pos = pos
        if buf[pos] == 0xFF and pos + 1 < n:
            magic_pos = pos + 1
        # Need at least 2 magic + 1 type + 2 len + ?? + 1 trailer
        if magic_pos + 5 >= n:
            break
        if buf[magic_pos] != FRAME_MAGIC[0] or buf[magic_pos + 1] != FRAME_MAGIC[1]:
            # Not a frame at this position. Drop one byte and resync.
            pos += 1
            continue
        # We have AA 55. Read header.
        hdr_pos = magic_pos
        type_ = buf[hdr_pos + 2]
        payload_len = (buf[hdr_pos + 3] << 8) | buf[hdr_pos + 4]
        total_len = (hdr_pos - pos) + 5 + payload_len + 1
        if pos + total_len > n:
            # Frame not fully buffered yet — wait for more bytes.
            break
        payload = bytes(buf[hdr_pos + 5 : hdr_pos + 5 + payload_len])
        trailer = buf[hdr_pos + 5 + payload_len]
        frames.append(ParsedFrame(type_=type_, payload=payload, trailer=trailer))
        pos += total_len
    return frames, bytearray(buf[pos:])


def apply_frame(framebuffer: bytearray, frame: ParsedFrame) -> bool:
    """Mutate `framebuffer` in place from `frame`. Returns True if the
    caller should redraw."""
    if frame.type_ == FRAME_TYPE_FULL:
        if len(frame.payload) != FRAMEBUFFER_SIZE:
            return False
        framebuffer[:] = frame.payload
        return True
    if frame.type_ == FRAME_TYPE_DIFF:
        # Each chunk: <byte_group_idx><8 bytes>
        data = frame.payload
        i = 0
        changed = False
        while i + 8 < len(data):
            idx = data[i]
            base = idx * 8
            if base + 8 <= len(framebuffer):
                framebuffer[base : base + 8] = data[i + 1 : i + 9]
                changed = True
            i += 9
        return changed
    return False


def framebuffer_to_pixels(framebuffer: bytes) -> list[list[bool]]:
    """Decode a 1024-byte framebuffer into a 64×128 pixel matrix.

    `pixels[y][x] = True` if the pixel is on. Bit layout per the F4HWN
    screenshot feature: `bitIdx = y * 128 + x`, byte `bitIdx // 8`,
    bit `bitIdx & 7`.
    """
    pixels = [[False] * DISPLAY_WIDTH for _ in range(DISPLAY_HEIGHT)]
    for y in range(DISPLAY_HEIGHT):
        for x in range(DISPLAY_WIDTH):
            bit_idx = y * DISPLAY_WIDTH + x
            byte = framebuffer[bit_idx >> 3]
            pixels[y][x] = bool((byte >> (bit_idx & 7)) & 1)
    return pixels


class DisplayMirror:
    """Threaded keepalive + frame reader for an open `RadioPort`.

    Usage (typical, from a GUI worker):

        mirror = DisplayMirror(rp, on_frame=lambda fb: emit_signal(fb))
        mirror.start()
        ...
        mirror.stop()

    The `on_frame` callback receives a copy of the latest 1024-byte
    framebuffer whenever the radio sends a full or diff update.
    """

    def __init__(
        self,
        rp: proto.RadioPort,
        on_frame: Callable[[bytes], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._rp = rp
        self._on_frame = on_frame
        self._on_error = on_error
        self._running = False
        self._thread: threading.Thread | None = None
        self._framebuffer = bytearray(FRAMEBUFFER_SIZE)

    @property
    def running(self) -> bool:
        return self._running

    @property
    def framebuffer(self) -> bytes:
        """Return a snapshot of the current framebuffer."""
        return bytes(self._framebuffer)

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="DisplayMirror", daemon=True
        )
        self._thread.start()

    def stop(self, timeout_sec: float = 2.0) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=timeout_sec)
            self._thread = None

    def _loop(self) -> None:
        # We own the port for the duration of this loop. The radio expects
        # a quiet period (no CHIRP-style packets) followed by keepalives.
        try:
            self._rp.port.timeout = 0.05  # 50 ms read poll
            buf = bytearray()
            last_keepalive = 0.0
            while self._running:
                now = time.monotonic()
                if now - last_keepalive >= KEEPALIVE_INTERVAL_SEC:
                    try:
                        self._rp.port.write(KEEPALIVE_PACKET)
                    except Exception as e:
                        if self._on_error:
                            self._on_error(e)
                        return
                    last_keepalive = now

                # Read whatever's queued on the port.
                try:
                    in_waiting = self._rp.port.in_waiting
                except Exception:
                    in_waiting = 0
                if in_waiting:
                    chunk = self._rp.port.read(in_waiting)
                    if chunk:
                        buf.extend(chunk)
                else:
                    # Short blocking read to avoid 100% CPU.
                    chunk = self._rp.port.read(64)
                    if chunk:
                        buf.extend(chunk)

                # Cap buffer size — desync recovery.
                if len(buf) > 8192:
                    buf = buf[-4096:]

                frames, leftover = parse_frames(buf)
                buf = leftover
                for fr in frames:
                    if apply_frame(self._framebuffer, fr) and self._on_frame:
                        self._on_frame(bytes(self._framebuffer))
        except Exception as e:
            if self._on_error:
                self._on_error(e)
        finally:
            self._running = False


def iter_frames(rp: proto.RadioPort,
                stop_event: threading.Event | None = None) -> Iterator[bytes]:
    """Synchronous generator wrapping `DisplayMirror` for CLI/test use.

    Yields a 1024-byte framebuffer each time the radio sends an update.
    Caller can stop by setting `stop_event`.
    """
    if stop_event is None:
        stop_event = threading.Event()
    queue: list[bytes] = []
    cv = threading.Condition()

    def push(fb: bytes) -> None:
        with cv:
            queue.append(fb)
            cv.notify()

    mirror = DisplayMirror(rp, on_frame=push)
    mirror.start()
    try:
        while not stop_event.is_set():
            with cv:
                if not queue:
                    cv.wait(timeout=0.5)
                if queue:
                    yield queue.pop(0)
    finally:
        mirror.stop()
