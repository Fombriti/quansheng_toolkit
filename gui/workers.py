"""
QThread workers for blocking radio I/O. Keeps the UI responsive while
EEPROM read/write operations run on a background thread.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal

from ..kradio import dfu
from ..kradio import display_mirror as mirror
from ..kradio import protocol as proto
from ..kradio import workflow as wf
from ..kradio.models import select_profile


class _Signals(QObject):
    """Helper to attach signals to a QThread without subclassing QThread."""
    progress = Signal(int, int)        # current, total
    succeeded = Signal(object)         # arbitrary result payload
    failed = Signal(str)               # error message
    finished = Signal()                # always emitted last


class ReadEepromWorker(QThread):
    """
    Connect to the radio, hello, identify the profile from the firmware
    string, then download EXACTLY the expected EEPROM size for that
    profile. Reading past the radio's actual EEPROM (e.g. 45 KB on a
    radio that only has 8 KB) reliably hangs the firmware on the K5/K1
    family, so this profile-aware sizing is critical.
    """

    def __init__(self, port_name: str | None = None):
        super().__init__()
        self.port_name = port_name
        self.signals = _Signals()

    def run(self) -> None:  # noqa: D401
        rp = None
        try:
            rp = proto.open_radio(self.port_name)
            fw = proto.hello(rp)

            # Pick the profile FIRST so we know how big the EEPROM is.
            profile = select_profile(fw)

            def cb(done: int, total: int) -> None:
                self.signals.progress.emit(done, total)

            data = proto.read_block_chunked(
                rp, 0, profile.mem_size, progress_cb=cb,
            )
            self.signals.succeeded.emit({
                "firmware": fw,
                "data": bytes(data),
                "port": rp.port.port,
                "profile": profile,
            })
        except Exception as e:
            self.signals.failed.emit(str(e))
        finally:
            try:
                if rp is not None:
                    rp.port.close()
            except Exception:
                pass
            self.signals.finished.emit()


class FlashFirmwareWorker(QThread):
    """
    Run the full DFU flash flow on a background thread:
      1. Open DFU port at 38400 baud
      2. Wait for bootloader broadcasts → get bl_version
      3. assert_safe_to_flash(bl_version, target) — anti-brick gate
      4. perform_dfu_handshake — finalize the bootloader handover
      5. flash_firmware — page-by-page write loop with progress

    Caller is expected to have already shown a typed/triple-confirm
    dialog before constructing this worker. Once started, the only way
    to abort is to power-cycle the radio.
    """

    # Progress signal: (page_done, page_total, bl_version_string)
    flash_progress = Signal(int, int)

    def __init__(self, firmware_path: str, target: str,
                 port_name: str | None = None):
        super().__init__()
        self.firmware_path = firmware_path
        self.target = target
        self.port_name = port_name
        self.signals = _Signals()

    def run(self) -> None:  # noqa: D401
        rp = None
        try:
            from ..kradio import firmware as fw_mod
            from pathlib import Path

            data = Path(self.firmware_path).read_bytes()
            firmware = fw_mod.unpack_firmware(data)

            port = self.port_name or dfu.find_dfu_port()
            if not port:
                raise dfu.DfuError(
                    "No serial port found. Make sure the radio is in DFU "
                    "mode (PTT held while powering on)."
                )
            rp = dfu.open_dfu(port)
            bl = dfu.wait_for_dev_info(rp, timeout=15.0)
            dfu.assert_safe_to_flash(bl.bl_version, self.target)
            dfu.perform_dfu_handshake(rp, bl.bl_version)

            n_pages = (len(firmware) + dfu.FLASH_PAGE_SIZE - 1) \
                      // dfu.FLASH_PAGE_SIZE
            self.signals.progress.emit(0, n_pages)

            def _on_progress(done: int, total: int) -> None:
                self.signals.progress.emit(done, total)

            dfu.flash_firmware(rp, firmware, on_progress=_on_progress)

            self.signals.succeeded.emit({
                "port": port,
                "bl_version": bl.bl_version,
                "model": dfu.identify_model(bl.bl_version),
                "target": self.target,
                "pages": n_pages,
                "size": len(firmware),
            })
        except Exception as e:                              # noqa: BLE001
            self.signals.failed.emit(str(e))
        finally:
            try:
                if rp is not None:
                    rp.port.close()
            except Exception:
                pass
            self.signals.finished.emit()


class DfuIdentifyWorker(QThread):
    """
    Open the DFU port at 38400 baud and listen for the bootloader's
    periodic NOTIFY_DEV_INFO broadcasts. Returns UID + bootloader version
    + identified model (UV-K5 V1/V2 / UV-K5 V3 + UV-K1(8) v3 Mini Kong /
    UV-K1 / unknown).

    SAFE — read-only. Used to confirm the radio is in DFU and to gate
    the destructive flash button against the bootloader-version
    allowlist.
    """

    def __init__(self, port_name: str | None = None):
        super().__init__()
        self.port_name = port_name
        self.signals = _Signals()

    def run(self) -> None:  # noqa: D401
        rp = None
        try:
            port = self.port_name or dfu.find_dfu_port()
            if not port:
                raise dfu.DfuError(
                    "No serial port found. Make sure the radio is powered on "
                    "in DFU mode (hold PTT while powering on) with USB "
                    "connected."
                )
            rp = dfu.open_dfu(port)
            info_obj = dfu.wait_for_dev_info(rp, timeout=15.0)
            self.signals.succeeded.emit({
                "port": port,
                "uid": info_obj.uid_hex,
                "bl_version": info_obj.bl_version,
                "model": dfu.identify_model(info_obj.bl_version),
            })
        except Exception as e:
            self.signals.failed.emit(str(e))
        finally:
            try:
                if rp is not None:
                    rp.port.close()
            except Exception:
                pass
            self.signals.finished.emit()


class RestoreCalibrationWorker(QThread):
    """
    Write a calibration dump back to the radio using the same CHIRP-style
    sequential write loop as the program region. The radio is reset on
    completion.

    Profile-aware: the calibration region depends on the connected radio.
      * F4HWN Fusion 5.x   → 0xB000..0xB190  (400 bytes)
      * K5 V1 / K1 stock   → 0x1D00..0x2000  (768 bytes)
    The size of `calibration_bytes` is what tells the worker which one
    we're writing — caller validates against the active profile before
    constructing the worker, then re-validates here against the actual
    radio's profile after hello().

    This is the ONE place in the toolkit that intentionally writes to
    the calibration region. The caller is expected to have already done:
      * file-size sanity check
      * triple confirmation via QMessageBox
    """

    def __init__(self, calibration_bytes: bytes, port_name: str | None = None,
                 *, reset_after: bool = True):
        super().__init__()
        accepted = (0x190, 0x200, 0x300)   # F4HWN, stock 8KB family, legacy
        if len(calibration_bytes) not in accepted:
            raise ValueError(
                f"calibration must be 400 bytes (F4HWN), 512 bytes "
                f"(stock K1/K5 V1) or 768 bytes (legacy dump format); "
                f"got {len(calibration_bytes)}"
            )
        # Legacy 768-byte dumps from older builds covered 0x1D00..0x2000;
        # the real cal is the LAST 512 bytes (0x1E00..0x2000). Strip the
        # leading 0xFF padding so we never write to the reserved region.
        if len(calibration_bytes) == 0x300:
            calibration_bytes = calibration_bytes[-0x200:]
        self.data = bytes(calibration_bytes)
        self.port_name = port_name
        self.reset_after = reset_after
        self.signals = _Signals()

    def run(self) -> None:  # noqa: D401
        rp = None
        try:
            rp = proto.open_radio(self.port_name)
            fw = proto.hello(rp)
            rp.port.timeout = 4.0

            profile = select_profile(fw)
            cal_start, mem_size = profile.cal_start, profile.mem_size
            expected = mem_size - cal_start
            if len(self.data) != expected:
                raise ValueError(
                    f"calibration file is {len(self.data)} bytes but the "
                    f"connected radio ({profile.name}) expects "
                    f"{expected} bytes — wrong file or wrong radio."
                )

            BLOCK = proto.MEM_BLOCK
            addr = cal_start
            end = mem_size
            n_blocks = (end - addr + BLOCK - 1) // BLOCK
            done = 0
            while addr < end:
                chunk = min(BLOCK, end - addr)
                offset = addr - cal_start
                proto.write_mem(rp, addr, self.data[offset:offset + chunk])
                addr += chunk
                done += 1
                self.signals.progress.emit(done, n_blocks)

            if self.reset_after:
                try:
                    proto.reset_radio(rp)
                except Exception:
                    pass

            self.signals.succeeded.emit({
                "firmware": fw, "blocks": n_blocks,
                "profile": profile.name,
                "region": (cal_start, mem_size),
            })
        except Exception as e:
            self.signals.failed.emit(str(e))
        finally:
            try:
                if rp is not None:
                    rp.port.close()
            except Exception:
                pass
            self.signals.finished.emit()


class DumpCalibrationWorker(QThread):
    """
    Read the calibration region of the connected radio. The program region
    is not touched. The address range is picked from the active profile:
      * F4HWN Fusion 5.x   → 0xB000..0xB190  (400 bytes)
      * K5 V1 / K1 stock   → 0x1D00..0x2000  (768 bytes)
    """

    def __init__(self, port_name: str | None = None):
        super().__init__()
        self.port_name = port_name
        self.signals = _Signals()

    def run(self) -> None:  # noqa: D401
        rp = None
        try:
            rp = proto.open_radio(self.port_name)
            fw = proto.hello(rp)
            profile = select_profile(fw)
            cal_start, mem_size = profile.cal_start, profile.mem_size
            length = mem_size - cal_start

            def cb(done: int, total: int) -> None:
                self.signals.progress.emit(done, total)

            data = proto.read_block_chunked(
                rp, cal_start, length, progress_cb=cb,
            )
            self.signals.succeeded.emit({
                "firmware": fw, "data": bytes(data),
                "profile": profile.name,
                "region": (cal_start, mem_size),
            })
        except Exception as e:
            self.signals.failed.emit(str(e))
        finally:
            try:
                if rp is not None:
                    rp.port.close()
            except Exception:
                pass
            self.signals.finished.emit()


# Profile cal-region sizes used to validate calibration files at
# construction time, before we've talked to the radio.
_CAL_SIZES_BY_PROFILE = {
    "f4hwn": 0xB190 - 0xB000,    # 400
    "k5v1":  0x2000 - 0x1D00,    # 768
}


class UploadEepromWorker(QThread):
    """
    Connect, hello, run a CHIRP-style sequential upload of the given image,
    then optionally send the reset packet. Image must be at least
    `prog_size` bytes long; pass the active profile's `prog_size`
    explicitly so the worker writes exactly the program region the radio
    expects (8 KB / 0x1D00 for K5 V1 stock vs 41 KB / 0xA171 for F4HWN).
    """

    def __init__(self, image: bytes, port_name: str | None = None,
                 *, reset_after: bool = True,
                 prog_size: int | None = None):
        super().__init__()
        self.image = bytes(image)
        self.port_name = port_name
        self.reset_after = reset_after
        self.prog_size = prog_size
        self.signals = _Signals()

    def run(self) -> None:  # noqa: D401
        rp = None
        try:
            rp = proto.open_radio(self.port_name)
            fw = proto.hello(rp)

            def cb(done: int, total: int) -> None:
                self.signals.progress.emit(done, total)

            blocks = wf.upload_eeprom_chirp_style(
                rp, self.image,
                prog_size=self.prog_size,
                progress_cb=cb, do_reset=self.reset_after,
            )
            self.signals.succeeded.emit({"firmware": fw, "blocks": blocks})
        except Exception as e:
            self.signals.failed.emit(str(e))
        finally:
            try:
                if rp is not None:
                    rp.port.close()
            except Exception:
                pass
            self.signals.finished.emit()


class _MirrorSignals(QObject):
    """Signals for DisplayMirrorWorker — separate from _Signals because the
    payload + cadence are different (frames stream continuously)."""
    frame = Signal(bytes)              # 1024-byte framebuffer
    started = Signal()
    stopped = Signal()
    failed = Signal(str)


class DisplayMirrorWorker(QThread):
    """
    Owns the serial port for the duration of a display-mirror session
    and pumps framebuffer updates back to the GUI via the `frame`
    signal.

    The worker uses kradio.display_mirror.DisplayMirror under the hood
    (which spawns its own keepalive+read thread). This QThread mostly
    serves to (a) own the port lifecycle and (b) emit Qt signals onto
    the GUI thread.
    """

    def __init__(self, port_name: str | None = None):
        super().__init__()
        self.port_name = port_name
        self.signals = _MirrorSignals()
        self._stop_requested = False
        self._mirror: mirror.DisplayMirror | None = None

    def request_stop(self) -> None:
        self._stop_requested = True
        if self._mirror is not None:
            self._mirror.stop()

    def run(self) -> None:  # noqa: D401
        rp = None
        try:
            rp = proto.open_radio(self.port_name)
            # Don't do hello() here — F4HWN's screenshot stream uses a
            # different protocol than the CHIRP-style EEPROM commands,
            # and a hello packet would corrupt the stream parser.

            done_event = __import__("threading").Event()

            def on_frame(fb: bytes) -> None:
                self.signals.frame.emit(fb)

            def on_error(e: Exception) -> None:
                self.signals.failed.emit(str(e))
                done_event.set()

            self._mirror = mirror.DisplayMirror(
                rp, on_frame=on_frame, on_error=on_error
            )
            self._mirror.start()
            self.signals.started.emit()

            # Wait until the user requests stop or an error fires.
            while not self._stop_requested and not done_event.is_set():
                if not self._mirror.running:
                    break
                done_event.wait(timeout=0.2)

        except Exception as e:
            self.signals.failed.emit(str(e))
        finally:
            try:
                if self._mirror is not None:
                    self._mirror.stop()
            except Exception:
                pass
            try:
                if rp is not None:
                    rp.port.close()
            except Exception:
                pass
            self.signals.stopped.emit()
