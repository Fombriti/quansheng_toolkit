"""
High-level workflows: download EEPROM, build patched image, upload.

The workflow is split in three stages on purpose:

  1. download_eeprom()        — open port, read EEPROM, save .bin, close.
  2. patch_image_inplace()    — pure offline byte editing of an EEPROM image.
  3. upload_eeprom_chirp_style() — open port, write 0x0000..PROG_SIZE
                                  sequentially (no reads), reset, close.

This split is required because the F4HWN Fusion 5.4 firmware on the UV-K1
will lock up if you mix reads and writes inside a single session that
followed another open/close. The CHIRP-style "pure write session" is the
only reliable upload pattern observed.

For convenience the three stages are usually invoked from separate Python
processes (read in P1, edit in P2, upload in P3) — exactly mirroring how
CHIRP itself separates "Download" and "Upload" actions.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import protocol as proto
from . import memory as mem


# ------------------------------------------------------------------------- #
# Stage 1: download                                                         #
# ------------------------------------------------------------------------- #

def download_eeprom(rp: proto.RadioPort,
                    progress_cb: Callable[[int, int], None] | None = None) -> bytes:
    """Download the full EEPROM (PROG region + calibration) into memory."""
    return proto.read_block_chunked(
        rp, 0, proto.MEM_SIZE, progress_cb=progress_cb
    )


# ------------------------------------------------------------------------- #
# Stage 2: offline image edit                                               #
# ------------------------------------------------------------------------- #

def patch_scanlist_byte(image: bytearray, ch_idx: int, new_value: int,
                        *, memory_module=None) -> int:
    """
    Patch a single scanlist byte in an EEPROM image. Returns the absolute
    address that was modified.

    `memory_module` selects the layout. F4HWN (`kradio.memory`) stores the
    scanlist in its own byte; K5 V1 (`kradio.memory_uvk5_v1`) packs the
    two scanlist flag bits into the same byte as compander+band, so the
    write must use `patch_scanlist()` to preserve the other bits.
    """
    mm = memory_module if memory_module is not None else mem
    if not 0 <= ch_idx < mm.NUM_CHANNELS:
        raise ValueError(f"channel index out of range: {ch_idx}")
    addr = mm.addr_scanlist_byte(ch_idx)
    cal_start = getattr(mm, "CAL_START", proto.CAL_START)
    if addr >= cal_start:
        raise ValueError(
            f"address 0x{addr:04x} would land in calibration region"
        )
    # K5 V1 packs the 2 scanlist flag bits into the same byte as
    # compander/band (CH_ATTR_SIZE == 1) and exposes patch_scanlist(byte,
    # value). F4HWN stores the scanlist in its own dedicated byte.
    if mm.CH_ATTR_SIZE == 1:
        image[addr] = mm.patch_scanlist(image[addr], new_value)
    else:
        if not 0 <= new_value <= mm.SCAN_ALL:
            raise ValueError(f"scanlist value out of range: {new_value}")
        image[addr] = new_value & 0xFF
    return addr


def patch_session_state(image: bytearray, *,
                        mr_channel_a: int | None = None,
                        screen_channel_a: int | None = None) -> None:
    """Override the boot-time selected channel in an EEPROM image."""
    import struct as _struct
    if mr_channel_a is not None:
        _struct.pack_into("<H", image, 0xA012, mr_channel_a)
    if screen_channel_a is not None:
        _struct.pack_into("<H", image, 0xA010, screen_channel_a)


# ---- CSV-driven channel editing -----------------------------------------

# CHIRP-compatible column order. Reading this file back into CHIRP will
# Just Work; the same columns are also what `import_channels_from_csv`
# accepts (extra columns are silently ignored).
CSV_COLUMNS = [
    "Location", "Name", "Frequency", "Duplex", "Offset",
    "Tone", "rToneFreq", "cToneFreq",
    "DtcsCode", "DtcsPolarity", "RxDtcsCode", "CrossMode",
    "Mode", "TStep", "Skip", "Power", "Comment",
    "URCALL", "RPT1CALL", "RPT2CALL", "DVCODE",
]


def _scanlist_to_chirp_lista(scanlist: int, mm) -> str:
    """Render a profile-aware scanlist value as `LISTA N` for the
    Comment column. Used so a round-trip export → re-import preserves
    scan list assignments via the existing --derive-from-comment path."""
    labels = list(getattr(mm, "SCAN_LIST_LABELS", []))
    if not labels or scanlist <= 0 or scanlist >= len(labels):
        return ""
    return f"LISTA {scanlist}"


def export_channels_to_csv(image: bytes, csv_path, *, memory_module=None,
                            include_empty: bool = False) -> int:
    """
    Write every configured channel from `image` to a CHIRP-compatible
    .csv file. Returns the number of rows written.

    `memory_module` selects the EEPROM layout. Defaults to F4HWN to
    match `import_channels_from_csv`. Pass `kradio.memory_uvk5_v1` for
    stock K1/K5 8KB images.

    Tones are written into the CHIRP `Tone` / `rToneFreq` / `DtcsCode`
    columns when present, falling back to "" / "88.5" / "023" defaults
    that CHIRP accepts on import.
    """
    import csv as _csv
    from . import tones as _tones

    mm = memory_module if memory_module is not None else mem
    channels = mm.decode_all_channels(bytes(image))
    rows: list[dict] = []

    for ch in channels:
        if ch.is_empty and not include_empty:
            continue
        # Map our (rx_tmode, tx_tmode) pair onto CHIRP's single Tone
        # column. CHIRP picks one per row, so we follow its convention:
        #   * both off            → ""
        #   * tx only CTCSS       → "Tone"
        #   * both CTCSS          → "TSQL"
        #   * any DCS             → "DTCS"
        #   * mixed types         → "Cross" (uncommon)
        tx_t = ch.tx_tmode
        rx_t = ch.rx_tmode
        if tx_t == _tones.TMODE_NONE and rx_t == _tones.TMODE_NONE:
            tone_kind = ""
        elif tx_t == _tones.TMODE_TONE and rx_t == _tones.TMODE_NONE:
            tone_kind = "Tone"
        elif tx_t == _tones.TMODE_TONE and rx_t == _tones.TMODE_TONE:
            tone_kind = "TSQL"
        elif tx_t in (_tones.TMODE_DTCS, _tones.TMODE_RDCS) \
                and rx_t in (_tones.TMODE_DTCS, _tones.TMODE_RDCS,
                              _tones.TMODE_NONE):
            tone_kind = "DTCS"
        else:
            tone_kind = "Cross"

        # Strip the suffix " Hz" / "N" / "I" so CHIRP gets bare numbers.
        def _ctcss_freq(label: str) -> str:
            if label.endswith(" Hz"):
                return label[:-3]
            return "88.5"

        def _dcs_code(label: str) -> str:
            if label.startswith("D") and len(label) >= 4:
                return label[1:4]
            return "023"

        rfreq = _ctcss_freq(ch.tx_tone_label) if tx_t == _tones.TMODE_TONE \
            else "88.5"
        cfreq = _ctcss_freq(ch.rx_tone_label) if rx_t == _tones.TMODE_TONE \
            else "88.5"
        dcode = _dcs_code(ch.tx_tone_label) if tx_t in (
            _tones.TMODE_DTCS, _tones.TMODE_RDCS) else "023"
        rxdcode = _dcs_code(ch.rx_tone_label) if rx_t in (
            _tones.TMODE_DTCS, _tones.TMODE_RDCS) else dcode
        # Polarity letter — N (normal) / R (reverse).
        tx_pol = "R" if tx_t == _tones.TMODE_RDCS else "N"
        rx_pol = "R" if rx_t == _tones.TMODE_RDCS else "N"

        # Step: kHz value from STEPS_KHZ table when available.
        step_idx = getattr(ch, "step_idx", 0)
        steps = getattr(mm, "STEPS_KHZ", [])
        tstep = f"{steps[step_idx]:g}" if 0 <= step_idx < len(steps) else "12.5"

        # Power label from the profile's POWER_LEVELS table.
        pwr = getattr(ch, "tx_power", 0)
        pwrs = getattr(mm, "POWER_LEVELS", [])
        power = pwrs[pwr] if 0 <= pwr < len(pwrs) else "LOW1"

        # Embed the scanlist as `LISTA N` in the Comment column so
        # re-import via --derive-from-comment can recover it.
        comment_extras = _scanlist_to_chirp_lista(ch.scanlist, mm)

        row = {
            "Location":     str(ch.index + 1),
            "Name":         ch.name,
            "Frequency":    f"{ch.freq_mhz:.6f}",
            "Duplex":       getattr(ch, "duplex", ""),
            "Offset":       f"{getattr(ch, 'offset_hz', 0) / 1e6:.6f}",
            "Tone":         tone_kind,
            "rToneFreq":    rfreq,
            "cToneFreq":    cfreq,
            "DtcsCode":     dcode,
            "DtcsPolarity": tx_pol + rx_pol,
            "RxDtcsCode":   rxdcode,
            "CrossMode":    "Tone->Tone",  # safe default
            "Mode":         ch.mode,
            "TStep":        tstep,
            "Skip":         "",
            "Power":        power,
            "Comment":      comment_extras,
            "URCALL":       "",
            "RPT1CALL":     "",
            "RPT2CALL":     "",
            "DVCODE":       "",
        }
        rows.append(row)

    from pathlib import Path as _Path
    out = _Path(csv_path)
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = _csv.DictWriter(f, fieldnames=CSV_COLUMNS,
                                 quoting=_csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def import_channels_from_csv(image: bytearray, csv_path,
                             *,
                             derive_scanlist_from_comment: bool = False,
                             clear_missing: bool = False,
                             memory_module=None) -> dict:
    """
    Apply a CHIRP-compatible CSV to an EEPROM image. The CSV is expected to
    have at minimum these columns: Location, Name, Frequency, Mode.
    Optional: Duplex, Offset, Power, ScList, TStep, Comment.

    Strategy:
      * For each row with a 1-based Location, we patch that channel slot.
      * Tone codes (CTCSS / DCS) are NOT touched — the existing record is
        preserved for those fields. Set them via CHIRP if needed.
      * `clear_missing=True` clears all channels NOT listed in the CSV.
      * `derive_scanlist_from_comment=True` parses 'LISTA N' from Comment.

    `memory_module` selects the EEPROM layout to write into:
      * `kradio.memory`           — F4HWN Fusion 5.x (1024 channels, 24 lists)
      * `kradio.memory_uvk5_v1`   — UV-K5/K1 stock (200 channels, 4-state SL)

    Defaults to F4HWN for backwards compatibility with existing callers.

    Returns a summary dict: {'updated': N, 'skipped': N, 'cleared': N}.
    """
    import csv as _csv
    import re as _re
    from pathlib import Path as _Path

    mm = memory_module if memory_module is not None else mem

    rows = list(_csv.DictReader(_Path(csv_path).open(encoding="utf-8-sig")))
    seen_idx: set[int] = set()
    updated = 0
    skipped: list[str] = []

    for row in rows:
        loc = (row.get("Location") or "").strip()
        if not loc.isdigit():
            continue
        idx = int(loc) - 1
        if not 0 <= idx < mm.NUM_CHANNELS:
            skipped.append(f"location out of range: {loc}")
            continue

        name = (row.get("Name") or "").strip() or None
        freq_str = (row.get("Frequency") or "").strip()
        mode = (row.get("Mode") or "").strip().upper() or None

        freq_hz: int | None = None
        if freq_str:
            try:
                freq_hz = int(round(float(freq_str) * 1_000_000))
            except ValueError:
                skipped.append(f"row {loc}: bad frequency {freq_str!r}")
                continue

        # Optional duplex / offset
        duplex = (row.get("Duplex") or "").strip()
        offset_str = (row.get("Offset") or "").strip()
        offset_hz = 0
        if offset_str:
            try:
                offset_hz = int(round(float(offset_str) * 1_000_000))
            except ValueError:
                pass

        # Power: CHIRP exports strings like "0.1W", "1W", "5W"; map roughly.
        power_str = (row.get("Power") or "").strip()
        tx_power = _power_str_to_idx(power_str)

        # Scan list: explicit ScList column wins; else parse Comment.
        scl: int | None = None
        scl_raw = row.get("ScList") or row.get("Scanlist") or row.get("Scan List")
        if not scl_raw and derive_scanlist_from_comment:
            comment = row.get("Comment", "") or ""
            m = _re.search(r"LISTA\s+(\d+)", comment, _re.IGNORECASE)
            if m:
                scl_raw = m.group(1)
        if scl_raw:
            try:
                scl = mm.parse_scanlist_spec(scl_raw)
            except ValueError as e:
                skipped.append(f"row {loc}: {e}")

        # Tones: CHIRP exports rxTone/cToneFreq/rToneFreq/DtcsCode + Tone +
        # CrossMode. Build rx/tx tone strings from those columns.
        rx_tone, tx_tone = _csv_row_to_tones(row)

        try:
            mm.patch_channel_in_image(
                image,
                idx=idx,
                name=name,
                freq_hz=freq_hz,
                mode=mode,
                scanlist=scl,
                rx_tone=rx_tone,
                tx_tone=tx_tone,
                duplex=duplex if duplex in ("+", "-") else "",
                offset_hz=offset_hz,
                tx_power=tx_power,
            )
            seen_idx.add(idx)
            updated += 1
        except (ValueError, KeyError) as e:
            skipped.append(f"row {loc}: {e}")

    cleared = 0
    if clear_missing:
        for i in range(mm.NUM_CHANNELS):
            if i not in seen_idx:
                # Only clear slots that were not empty already.
                rec_addr = mm.addr_channel(i)
                if image[rec_addr:rec_addr + 4] != b"\xFF\xFF\xFF\xFF":
                    mm.clear_channel_in_image(image, i)
                    cleared += 1

    return {"updated": updated, "skipped": skipped, "cleared": cleared}


def _csv_row_to_tones(row: dict) -> tuple[str | None, str | None]:
    """
    Map a CHIRP CSV row to (rx_tone, tx_tone) strings, or (None, None)
    when the row carries no tone columns at all (preserves whatever the
    image already has). Recognised fields:
      Tone        — TX tone mode: "", "Tone", "TSQL", "DTCS", "Cross"
      rToneFreq   — TX CTCSS in Hz
      cToneFreq   — RX CTCSS in Hz (used by TSQL)
      DtcsCode    — TX DTCS code
      RxDtcsCode  — RX DTCS code (used by Cross w/ DTCS RX)
      DtcsPolarity — "NN" / "NR" / "RN" / "RR" (TX,RX polarity)
      CrossMode   — for Tone == "Cross"
    """
    if not any(k in row for k in ("Tone", "rToneFreq", "cToneFreq",
                                   "DtcsCode", "CrossMode")):
        return None, None

    tone_kind = (row.get("Tone") or "").strip()
    rfreq = (row.get("rToneFreq") or "").strip()
    cfreq = (row.get("cToneFreq") or "").strip()
    dtcs = (row.get("DtcsCode") or "").strip().lstrip("0") or "0"
    rxdtcs = (row.get("RxDtcsCode") or "").strip().lstrip("0") or dtcs
    pol = (row.get("DtcsPolarity") or "NN").strip().upper()
    tx_pol = "I" if (pol[:1] or "N") == "R" else "N"
    rx_pol = "I" if (pol[1:2] or "N") == "R" else "N"

    if tone_kind in ("", "OFF", "NONE"):
        return "OFF", "OFF"
    if tone_kind == "Tone":
        # CTCSS on TX, none on RX
        return "OFF", (f"{rfreq}" if rfreq else "OFF")
    if tone_kind == "TSQL":
        # CTCSS both ways (RX uses cToneFreq when present, else rToneFreq)
        rx = cfreq or rfreq
        return (f"{rx}" if rx else "OFF",
                f"{rfreq}" if rfreq else "OFF")
    if tone_kind == "DTCS":
        return f"D{dtcs}{rx_pol}", f"D{dtcs}{tx_pol}"
    if tone_kind == "Cross":
        cross = (row.get("CrossMode") or "Tone->Tone").strip()
        # tx side
        if cross.startswith("Tone"):
            tx_part = f"{rfreq}" if rfreq else "OFF"
        elif cross.startswith("DTCS"):
            tx_part = f"D{dtcs}{tx_pol}"
        else:
            tx_part = "OFF"
        rx_after = cross.split("->", 1)[1] if "->" in cross else ""
        if rx_after.startswith("Tone"):
            rx_part = f"{cfreq or rfreq}" if (cfreq or rfreq) else "OFF"
        elif rx_after.startswith("DTCS"):
            rx_part = f"D{rxdtcs}{rx_pol}"
        else:
            rx_part = "OFF"
        return rx_part, tx_part
    # Unknown Tone string — leave the channel's current tones as-is.
    return None, None


def _power_str_to_idx(power_str: str) -> int:
    """
    Map a CHIRP-style power label to an index 0..7. CHIRP exports values
    like "0.1W", "1W", "5W". The radio's encoding is implementation-defined;
    we use a coarse mapping that matches what the F4HWN driver uses.
    """
    s = (power_str or "").strip().upper().replace(" ", "")
    coarse = {
        "USER": 0,
        "0.1W": 1, "LOW1": 1,
        "0.5W": 2, "LOW2": 2,
        "1W":   3, "LOW3": 3,
        "2W":   4, "LOW4": 4,
        "3W":   5, "LOW5": 5,
        "4W":   6, "MID":  6,
        "5W":   7, "HIGH": 7,
    }
    return coarse.get(s, 2)  # default to ~LOW2 (~0.5W)


# ------------------------------------------------------------------------- #
# Stage 3: upload (CHIRP-style)                                             #
# ------------------------------------------------------------------------- #

def upload_eeprom_chirp_style(
    rp: proto.RadioPort,
    image: bytes,
    *,
    prog_size: int | None = None,
    progress_cb: Callable[[int, int], None] | None = None,
    do_reset: bool = True,
    verify: bool = True,
    verify_retries: int = 2,
    verify_progress_cb: Callable[[int, int], None] | None = None,
) -> int:
    """
    Sequential upload of the program region (0x0000..prog_size) followed by
    a reset packet, mirroring what the CHIRP driver `do_upload()` function
    does. This is the only upload pattern that has been observed reliable
    on F4HWN Fusion 5.4 / UV-K1.

    `prog_size` defaults to F4HWN's `proto.PROG_SIZE` (0xA171 / ~41 KB).
    Pass a smaller value when uploading to a stock K5 V1 / K1 layout
    (`prog_size=0x1D00` / 7424 bytes — never write past 0x1D00 because
    that is the calibration region for those firmwares).

    The image must be at least `prog_size` bytes long; bytes beyond
    that boundary are NEVER written.

    When `verify=True` (default), the function reads the prog region back
    after the upload and diffs it against the source image; any 64-byte
    blocks that didn't persist are re-uploaded (up to `verify_retries`
    times). The reset packet is delayed until verification passes.
    Empirically a single block can silently fail to persist (~1 in 700
    on F4HWN 5.4 / 38400 baud), so verify is on by default.

    Raises `RuntimeError` if blocks remain dirty after all retry rounds.
    """
    if prog_size is None:
        prog_size = proto.PROG_SIZE
    if len(image) < prog_size:
        raise ValueError(
            f"image too short ({len(image)} < {prog_size})"
        )

    # CHIRP uses a 4-second timeout during upload. Enforce that here so
    # tweaks at port-open time don't surprise us.
    rp.port.timeout = 4.0

    BLOCK = proto.MEM_BLOCK
    end = prog_size
    n_blocks = (end + BLOCK - 1) // BLOCK

    def _write_block(addr: int) -> None:
        chunk = min(BLOCK, end - addr)
        proto.write_mem(rp, addr, image[addr:addr + chunk])

    # ---- 1) initial sequential upload ---------------------------------
    addr, blocks_done = 0, 0
    while addr < end:
        _write_block(addr)
        addr += BLOCK
        blocks_done += 1
        if progress_cb:
            progress_cb(min(blocks_done, n_blocks), n_blocks)

    # ---- 2) verify-and-retry loop -------------------------------------
    if verify:
        for attempt in range(verify_retries + 1):
            readback = proto.read_block_chunked(
                rp, 0, end, progress_cb=verify_progress_cb
            )
            # Find dirty 64-byte blocks
            dirty = [
                a for a in range(0, end, BLOCK)
                if readback[a:a + BLOCK] != image[a:a + min(BLOCK, end - a)]
            ]
            if not dirty:
                break
            if attempt >= verify_retries:
                raise RuntimeError(
                    f"verify failed: {len(dirty)} block(s) of "
                    f"{n_blocks} did not persist after "
                    f"{verify_retries + 1} attempts. "
                    f"First dirty offset: 0x{dirty[0]:04x}"
                )
            # Retry just the dirty blocks
            for a in dirty:
                _write_block(a)

    # ---- 3) reset (only after verify passes) --------------------------
    if do_reset:
        try:
            proto.reset_radio(rp)
        except Exception:
            # Reset is a fire-and-forget — failing to round-trip it is non-fatal.
            pass

    return blocks_done


# ------------------------------------------------------------------------- #
# Convenience: backups                                                      #
# ------------------------------------------------------------------------- #

def save_backup(image: bytes, directory: Path, label: str = "auto") -> Path:
    """Save an EEPROM snapshot with a timestamped name."""
    import datetime as _dt
    directory.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = directory / f"eeprom_{ts}_{label}.bin"
    out.write_bytes(image)
    return out
