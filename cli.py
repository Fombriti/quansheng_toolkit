"""
Command-line interface for the kradio toolkit.

Workflow for changing scan-list assignments (or any EEPROM-resident setting):

  1. python -m quansheng_toolkit read -o current.bin
  2. python -m quansheng_toolkit make-bin -i current.bin --csv f.csv \
         --derive-from-comment -o patched.bin --show
  3. python -m quansheng_toolkit apply-full --eeprom patched.bin

Each step runs in its own Python process. This matches what CHIRP does
internally and avoids the F4HWN Fusion 5.4 firmware lockup that triggers
when reads and writes are mixed in a single session.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import fnmatch
import re
import sys
from pathlib import Path
from typing import Optional

from .kradio import protocol as proto
from .kradio import memory as mem
from .kradio import settings as setmod
from .kradio import workflow as wf
from .kradio.models import select_profile


try:
    from rich.console import Console
    from rich.table import Table
    from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn
    HAVE_RICH = True
except ImportError:
    HAVE_RICH = False

if HAVE_RICH:
    if sys.platform == "win32":
        # Force UTF-8 stdout on Windows so rich's Unicode output doesn't
        # die on cp1252 consoles.
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass
    console = Console(legacy_windows=False)
else:
    console = None


# ------------------------------------------------------------------------- #
# Output helpers                                                            #
# ------------------------------------------------------------------------- #

def info(msg: str) -> None:
    if HAVE_RICH:
        console.print(f"[cyan]*[/cyan] {msg}")
    else:
        print(f"* {msg}")


def warn(msg: str) -> None:
    if HAVE_RICH:
        console.print(f"[yellow]![/yellow] {msg}")
    else:
        print(f"! {msg}", file=sys.stderr)


def err(msg: str) -> None:
    if HAVE_RICH:
        console.print(f"[red]X[/red] {msg}")
    else:
        print(f"X {msg}", file=sys.stderr)


def ok(msg: str) -> None:
    if HAVE_RICH:
        console.print(f"[green]OK[/green] {msg}")
    else:
        print(f"OK {msg}")


# ------------------------------------------------------------------------- #
# Range parsing                                                             #
# ------------------------------------------------------------------------- #

def parse_range_spec(spec: str) -> list[int]:
    """
    Parse user range strings:
      "1-17"      -> [1..17]
      "5"         -> [5]
      "1,3,5-7"   -> [1,3,5,6,7]
      "all"       -> []  (callers should treat this specially)
    """
    if spec.strip().lower() == "all":
        return []
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return sorted(out)


# ------------------------------------------------------------------------- #
# Channel selection (offline)                                               #
# ------------------------------------------------------------------------- #

def select_channels(channels: list[mem.Channel],
                    *,
                    by_index: Optional[str] = None,
                    by_name: Optional[str] = None,
                    only_configured: bool = True) -> list[mem.Channel]:
    """Filter channels by 1-based index range or filename-glob name."""
    pool = [c for c in channels if not c.is_empty] if only_configured else channels
    if by_index is not None:
        if by_index.lower() == "all":
            return pool
        wanted = set(parse_range_spec(by_index))
        return [c for c in pool if (c.index + 1) in wanted]
    if by_name:
        return [c for c in pool if fnmatch.fnmatch(c.name, by_name)]
    return pool


# ------------------------------------------------------------------------- #
# Pretty rendering                                                          #
# ------------------------------------------------------------------------- #

def print_changes_table(changes, *, memory_module=None) -> None:
    mm = memory_module if memory_module is not None else mem
    if HAVE_RICH:
        tbl = Table(title=f"Pending changes — {len(changes)}",
                    header_style="bold")
        tbl.add_column("Ch", justify="right")
        tbl.add_column("Name")
        tbl.add_column("Freq (MHz)", justify="right")
        tbl.add_column("From")
        tbl.add_column("→")
        tbl.add_column("To")
        for c, new in changes:
            tbl.add_row(
                str(c.index + 1), c.name,
                f"{c.freq_mhz:.4f}",
                c.scanlist_label, "→",
                mm.scanlist_label(new),
            )
        console.print(tbl)
    else:
        for c, new in changes:
            print(f"  ch{c.index + 1:4d} {c.name:12s} {c.freq_mhz:10.4f}  "
                  f"{c.scanlist_label} -> {mm.scanlist_label(new)}")


def print_channels_table(channels, *, memory_module=None) -> None:
    mm = memory_module if memory_module is not None else mem
    configured = [c for c in channels if not c.is_empty]
    # Highlight: last slot (ALL on F4HWN, SL1+SL2 on K5 V1) is yellow,
    # any in-list slot is green, OFF is dim.
    labels = list(getattr(mm, "SCAN_LIST_LABELS", []))
    max_slot = len(labels) - 1 if labels else 25
    if HAVE_RICH:
        tbl = Table(title=f"Channels ({len(configured)} configured)",
                    header_style="bold")
        tbl.add_column("#", justify="right", style="dim")
        tbl.add_column("Name")
        tbl.add_column("Freq (MHz)", justify="right")
        tbl.add_column("Mode")
        tbl.add_column("ScanList")
        for c in configured:
            if c.scanlist == max_slot and max_slot > 0:
                color = "yellow"
            elif 1 <= c.scanlist < max_slot:
                color = "green"
            else:
                color = "dim"
            tbl.add_row(
                str(c.index + 1),
                c.name,
                f"{c.freq_mhz:.4f}",
                c.mode,
                f"[{color}]{c.scanlist_label}[/{color}]",
            )
        console.print(tbl)
    else:
        print(f"{'Ch':>4}  {'Name':12s}  {'Freq (MHz)':>11s}  "
              f"{'Mode':4s}  ScList")
        for c in configured:
            print(f"{c.index + 1:4d}  {c.name:12s}  {c.freq_mhz:11.4f}  "
                  f"{c.mode:4s}  {c.scanlist_label}")


# ------------------------------------------------------------------------- #
# CSV-driven change builder                                                 #
# ------------------------------------------------------------------------- #

def changes_from_csv(channels, csv_path: Path, *,
                     derive_from_comment: bool,
                     memory_module=None) -> tuple[list, list[str]]:
    """
    Build a list of `(channel, new_scanlist)` tuples from a CSV.
    Recognised columns: Name, ScList (or Scanlist, Scan List).
    With --derive-from-comment, scrape 'LISTA N' from the Comment column.
    Returns (changes, names_not_found_in_radio).
    """
    mm = memory_module if memory_module is not None else mem
    by_name = {c.name: c for c in channels if not c.is_empty and c.name}
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8-sig")))
    changes = []
    not_found: list[str] = []

    for row in rows:
        name = (row.get("Name") or "").strip()
        if not name:
            continue
        ch = by_name.get(name)
        if ch is None:
            not_found.append(name)
            continue
        scn_raw = row.get("ScList") or row.get("Scanlist") or row.get("Scan List")
        if (not scn_raw) and derive_from_comment:
            comment = row.get("Comment", "") or ""
            mobj = re.search(r"LISTA\s+(\d+)", comment, re.IGNORECASE)
            if mobj:
                scn_raw = mobj.group(1)
        if not scn_raw:
            continue
        try:
            new_value = mm.parse_scanlist_spec(scn_raw)
        except ValueError as e:
            warn(f"row '{name}': {e}")
            continue
        if ch.scanlist != new_value:
            changes.append((ch, new_value))
    return changes, not_found


# ------------------------------------------------------------------------- #
# Sub-commands                                                              #
# ------------------------------------------------------------------------- #

def cmd_info(args) -> int:
    rp = proto.open_radio(args.port)
    fw = proto.hello(rp)
    profile = select_profile(fw)
    mm = profile.memory_module
    labels = list(getattr(mm, "SCAN_LIST_LABELS", []))
    sl_summary = (
        ", ".join(labels) if labels and len(labels) <= 6
        else f"{profile.num_scan_lists} lists + OFF + ALL"
    )
    ok(f"Connected. Firmware: {fw}  ({profile.name})")
    print(f"  Port:         {rp.port.port}")
    print(f"  EEPROM size:  {profile.mem_size} bytes (0x{profile.mem_size:04x})")
    print(f"  Channels:     {profile.num_channels}")
    print(f"  Scan lists:   {sl_summary}")
    if not profile.verified:
        print(f"  Status:       experimental (not yet verified on hardware)")
    return 0


def cmd_read(args) -> int:
    from .kradio.models import select_profile

    rp = proto.open_radio(args.port)
    fw = proto.hello(rp)
    info(f"Firmware: {fw}")
    profile = select_profile(fw)
    info(f"Profile: {profile.name}  ({profile.mem_size} bytes)")
    mem_size = profile.mem_size

    if HAVE_RICH:
        with Progress(
            TextColumn("[bold]Download EEPROM"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total} bytes"),
            TimeRemainingColumn(),
        ) as p:
            t = p.add_task("read", total=mem_size)
            data = proto.read_block_chunked(
                rp, 0, mem_size,
                progress_cb=lambda done, _t: p.update(t, completed=done),
            )
            p.update(t, completed=mem_size)
    else:
        print(f"Downloading EEPROM ({mem_size} bytes)...")
        data = proto.read_block_chunked(rp, 0, mem_size)
        print("done.")

    out = Path(args.output) if args.output else \
        wf.save_backup(data, Path("backups"), "manual")
    if args.output:
        out.write_bytes(data)
    ok(f"EEPROM saved to: {out}  ({len(data)} bytes)")
    return 0


def cmd_list(args) -> int:
    from .kradio.models import select_profile, F4HWN_FUSION_5X

    label_override = None
    if args.from_file:
        eeprom = Path(args.from_file).read_bytes()
        info(f"EEPROM image: {args.from_file}")
        # Pick layout by file size: 8 KB → stock 8 KB family, otherwise F4HWN.
        profile, label_override = _resolve_profile_for_image(eeprom, "auto")
    else:
        rp = proto.open_radio(args.port)
        fw = proto.hello(rp)
        info(f"Firmware: {fw}")
        profile = select_profile(fw)
        eeprom = proto.read_block_chunked(rp, 0, profile.mem_size)

    info(f"Profile: {label_override or profile.name}")
    mm = profile.memory_module

    channels = mm.decode_all_channels(eeprom)
    if hasattr(mm, "decode_listnames"):
        listnames = mm.decode_listnames(eeprom)
        if any(listnames):
            named = [(i + 1, n) for i, n in enumerate(listnames) if n]
            if named:
                print("List names:", ", ".join(f"L{i}={n!r}" for i, n in named))
    print_channels_table(channels, memory_module=mm)
    return 0


def cmd_show_settings(args) -> int:
    """
    Read-only dump of every named setting the active profile knows about,
    grouped roughly. Works on both F4HWN and the 8 KB stock K1/K5/K6
    family — picks the profile from firmware string when reading from a
    radio, or from image size when reading from a .bin.
    """
    from .kradio.models import select_profile

    label_override = None
    if args.from_file:
        eeprom = Path(args.from_file).read_bytes()
        info(f"EEPROM image: {args.from_file}")
        profile, label_override = _resolve_profile_for_image(eeprom, "auto")
    else:
        rp = proto.open_radio(args.port)
        fw = proto.hello(rp)
        info(f"Firmware: {fw}")
        profile = select_profile(fw)
        eeprom = proto.read_block_chunked(rp, 0, profile.mem_size)

    sm = profile.settings_module
    if sm is None:
        err(f"No settings registry shipped for {profile.name}.")
        return 2

    info(f"Profile: {label_override or profile.name}")
    print()

    # Iterate the registry alphabetically; each entry's current value via
    # read_setting() — works identically for F4HWN and the stock family.
    specs = sm.list_settings()
    if HAVE_RICH:
        tbl = Table(title=f"Current settings ({len(specs)})",
                    header_style="bold")
        tbl.add_column("Key")
        tbl.add_column("Value")
        tbl.add_column("Description", style="dim")
        for spec in specs:
            try:
                v = sm.read_setting(eeprom, spec.name)
            except Exception as e:                  # noqa: BLE001
                v = f"<error: {e}>"
            tbl.add_row(spec.name, str(v), spec.description)
        console.print(tbl)
    else:
        for spec in specs:
            try:
                v = sm.read_setting(eeprom, spec.name)
            except Exception as e:                  # noqa: BLE001
                v = f"<error: {e}>"
            print(f"  {spec.name:24s} = {v}")

    return 0


def _resolve_profile_for_image(image: bytes, profile_arg: str):
    """
    Pick the radio profile + a display label for the CLI output.

    Returns `(profile, label_override)` — the second element is a string
    used in place of `profile.name` in the CLI's "Profile: …" line, or
    `None` if the profile's own name is fine to display.

    K1 stock (firmware 7.03.x) and K5/K6 stock (firmware 7.00.x and
    family) share an IDENTICAL 8 KB EEPROM layout — same offsets, same
    decoders, same encoders. Both profiles in `kradio.models` point at
    the same memory_module and settings_module. So when we have only
    a .bin file and no firmware string, we cannot tell them apart from
    bytes alone — the produced output is the same regardless.

    To stay honest in the CLI message, the auto path returns a generic
    "stock 8 KB family" label. Explicit `--profile {k1stock,k5stock}`
    is honoured and the profile's own name is shown.
    """
    from .kradio.models import F4HWN_FUSION_5X, UVK1_STOCK, UVK5_STOCK
    if profile_arg in ("f4hwn", "fusion"):
        return F4HWN_FUSION_5X, None
    if profile_arg in ("k1stock", "k1", "uvk1"):
        return UVK1_STOCK, None
    if profile_arg in ("k5stock", "k5v1", "uvk5v1", "k5", "k6"):
        return UVK5_STOCK, None
    if profile_arg == "stock":
        # Generic stock alias — pick whichever (modules are identical),
        # but show a clear family label.
        return UVK1_STOCK, "Quansheng stock 8 KB (K1 7.03 / K5/K6 7.00)"
    # auto
    if len(image) <= 0x2000:
        # 8 KB — could be K1 stock or K5/K6 stock. Same modules, same
        # produced bytes; show a family-honest label.
        return UVK1_STOCK, "Quansheng stock 8 KB (K1 7.03 / K5/K6 7.00)"
    return F4HWN_FUSION_5X, None


def cmd_make_bin(args) -> int:
    src = Path(args.input)
    image = bytearray(src.read_bytes())

    profile, label_override = _resolve_profile_for_image(
        bytes(image), args.profile
    )
    mm = profile.memory_module
    sm = profile.settings_module
    display_name = label_override or profile.name
    info(f"Profile: {display_name}  (mem 0x{profile.mem_size:04x})")

    if len(image) < profile.mem_size:
        err(f"EEPROM image too short for {profile.name} "
            f"({len(image)} < {profile.mem_size})")
        return 2

    channels = mm.decode_all_channels(bytes(image))

    if args.csv:
        changes, not_found = changes_from_csv(
            channels, Path(args.csv),
            derive_from_comment=args.derive_from_comment,
            memory_module=mm,
        )
        if not_found:
            warn(f"{len(not_found)} CSV rows have no matching channel in image:")
            for n in not_found[:10]:
                print(f"  - {n}")
            if len(not_found) > 10:
                print(f"  ... +{len(not_found) - 10}")
    else:
        if args.scanlist is None or args.ch is None:
            changes = []
        else:
            new_value = mm.parse_scanlist_spec(args.scanlist)
            indices = [i - 1 for i in parse_range_spec(args.ch)]
            sel = [c for c in channels if c.index in indices]
            changes = [(c, new_value) for c in sel if c.scanlist != new_value]

    if changes:
        info(f"Pending scanlist-only changes: {len(changes)}")
        if args.show:
            print_changes_table(changes, memory_module=mm)
        for ch, new_value in changes:
            wf.patch_scanlist_byte(image, ch.index, new_value, memory_module=mm)

    # Optional full channel import (frequency/name/mode/scanlist).
    if args.channels_csv:
        result = wf.import_channels_from_csv(
            image, Path(args.channels_csv),
            derive_scanlist_from_comment=args.derive_from_comment,
            clear_missing=args.clear_missing_channels,
            memory_module=mm,
        )
        info(f"Channel import: {result['updated']} updated, "
             f"{len(result['skipped'])} skipped, "
             f"{result['cleared']} cleared")
        for s in result["skipped"][:10]:
            warn(f"  - {s}")
        if len(result["skipped"]) > 10:
            print(f"  ... +{len(result['skipped']) - 10}")

    # Optional override of the boot channel. patch_session_state uses
    # F4HWN's 0xA010/0xA012 layout — refuse on K5 V1 stock until we wire
    # up the matching helper for that profile.
    if args.boot_channel is not None:
        if mm is not mem:
            warn("--boot-channel is currently F4HWN-only; ignored on this profile.")
        else:
            wf.patch_session_state(
                image, mr_channel_a=args.boot_channel - 1,
                screen_channel_a=args.boot_channel - 1,
            )
            info(f"Boot channel forced to ch{args.boot_channel}")

    # Apply --set key=value pairs (settings region edits) using the
    # active profile's settings registry.
    if args.set and sm is None:
        warn(f"--set is ignored: {profile.name} has no settings registry.")
    else:
        for kv in args.set or []:
            if "=" not in kv:
                err(f"--set expects key=value, got {kv!r}")
                return 2
            key, value = kv.split("=", 1)
            try:
                sm.apply_setting(image, key.strip(), value.strip())
            except (KeyError, ValueError) as e:
                err(f"--set {kv!r}: {e}")
                return 2
            info(f"setting {key.strip()} = {value.strip()!r}")

    out = Path(args.output)
    out.write_bytes(bytes(image))
    ok(f"Patched image saved: {out}  ({len(image)} bytes)")
    print()
    print("To apply this image to the radio (CHIRP-style upload):")
    print(f"  python -m quansheng_toolkit apply-full --eeprom {out}")
    return 0


def cmd_list_settings(args) -> int:
    """Show every named setting that `make-bin --set key=value` accepts."""
    if args.profile == "k5v1":
        from .kradio import settings_uvk5_v1 as active_setmod
        title_suffix = " — K5 V1 / K1 stock"
    else:
        active_setmod = setmod
        title_suffix = " — F4HWN Fusion 5.x"
    specs = active_setmod.list_settings()
    if HAVE_RICH:
        tbl = Table(title=f"Writable settings ({len(specs)}){title_suffix}",
                    header_style="bold")
        tbl.add_column("Key")
        tbl.add_column("Type")
        tbl.add_column("Range / choices")
        tbl.add_column("Description")
        for s in specs:
            if s.kind == "int":
                rng = f"{s.bounds[0]}..{s.bounds[1]}"
            elif s.kind == "enum":
                rng = " | ".join(s.bounds)
            elif s.kind == "bool":
                rng = "on / off"
            elif s.kind == "str":
                rng = f"<= {s.length} chars"
            else:
                rng = "?"
            tbl.add_row(s.name, s.kind, rng, s.description)
        console.print(tbl)
    else:
        for s in specs:
            print(f"{s.name:24s}  {s.kind:5s}  {s.description}")
    return 0


def cmd_dfu_flash(args) -> int:
    """
    Flash a firmware .bin to a radio in DFU mode. Wraps:
      1. wait_for_dev_info → bootloader version + UID
      2. assert_safe_to_flash(bl_version, --target) → anti-brick gate
      3. perform_dfu_handshake → tell bootloader we know its version
      4. flash_firmware → page-by-page write loop with retry

    Triple-confirmation by default — pass `--yes-i-understand` to skip
    the typed-confirmation prompt (intended only for CI / batch flashing
    scripts that already vetted the target match).
    """
    from .kradio import dfu, firmware as fw_mod

    src = Path(args.eeprom)
    if not src.is_file():
        err(f"firmware file not found: {src}")
        return 2

    info_obj = fw_mod.parse_firmware_file(src)
    info(f"Firmware:  {src.name}")
    info(f"  size:    {info_obj.size_bytes} bytes "
         f"({'raw' if info_obj.is_raw else 'packed'}; "
         f"version: {info_obj.version_string})")
    fw_bytes = fw_mod.unpack_firmware(src.read_bytes())
    info(f"  decoded: {len(fw_bytes)} bytes ({len(fw_bytes) // 256 + 1} pages)")

    port_name = args.port or dfu.find_dfu_port()
    if not port_name:
        err("No serial port found. Put the radio in DFU mode and connect USB.")
        return 2

    info(f"Port:      {port_name}  (38400 8N1)")
    info(f"Target:    {args.target}")

    rp = dfu.open_dfu(port_name)
    try:
        info("Listening for bootloader broadcasts…")
        bl = dfu.wait_for_dev_info(rp, timeout=args.timeout)
        info(f"Bootloader: {bl.bl_version}  UID: {bl.uid_hex}")

        # Phase 3 gate — refuses the wrong-family flash before touching anything.
        try:
            dfu.assert_safe_to_flash(bl.bl_version, args.target)
        except dfu.DfuError as e:
            err(str(e))
            return 2

        if not args.yes_i_understand:
            print()
            print("┌──────────────────────────────────────────────────────┐")
            print("│  ABOUT TO FLASH FIRMWARE — IRREVERSIBLE WITHOUT       │")
            print("│  AN OFFICIAL FIRMWARE FILE FOR YOUR RADIO            │")
            print("└──────────────────────────────────────────────────────┘")
            print(f"  Source:    {src}")
            print(f"  Target:    {args.target}")
            print(f"  Bootloader detected: {bl.bl_version} "
                  f"({dfu.identify_model(bl.bl_version)})")
            print()
            answer = input(
                'Type the literal word "FLASH" to proceed (anything else aborts): '
            ).strip()
            if answer != "FLASH":
                err("aborted")
                return 1

        info("Performing handshake…")
        dfu.perform_dfu_handshake(rp, bl.bl_version)

        info("Writing firmware (this takes ~30-60 seconds)…")

        def _on_progress(done: int, total: int) -> None:
            pct = (done / total) * 100 if total else 0
            print(f"\r  page {done}/{total}  ({pct:.1f}%)", end="", flush=True)

        def _on_log(msg: str) -> None:
            print(f"\n  {msg}")

        dfu.flash_firmware(rp, fw_bytes,
                           on_progress=_on_progress, on_log=_on_log)
        print()
        ok("Flash complete. The radio should now reboot into the new firmware.")
        return 0
    except dfu.DfuError as e:
        print()
        err(str(e))
        return 2
    finally:
        try:
            rp.port.close()
        except Exception:
            pass


def cmd_dfu_info(args) -> int:
    """
    Connect to the bootloader at 38400 baud and report UID +
    bootloader version + identified model. Read-only — no flash is
    touched. Use this before any firmware operation to confirm the
    radio is in DFU mode and identify which family it belongs to.
    """
    from .kradio import dfu

    try:
        port_name = args.port or dfu.find_dfu_port()
        if not port_name:
            err("No serial port found. Plug the radio and put it in DFU mode "
                "(power off, then power on while holding PTT).")
            return 2
        info(f"Opening DFU port: {port_name}  (38400 8N1)")
        info(f"Listening for bootloader broadcasts (timeout: {args.timeout}s)…")
        rp = dfu.open_dfu(port_name)
        try:
            bl = dfu.wait_for_dev_info(rp, timeout=args.timeout)
        finally:
            try:
                rp.port.close()
            except Exception:
                pass
        model = dfu.identify_model(bl.bl_version)
        ok(f"Bootloader detected: {bl.bl_version}  →  {model}")
        print(f"  UID: {bl.uid_hex}")
        if bl.bl_version in dfu.BLOCKED_FOR_K1_FAMILY:
            warn(f"⚠ This bootloader ({bl.bl_version}) is on the K1/K5V3 "
                 f"BRICK-LIST — flashing K1 or K5V3 firmware onto it would "
                 f"destroy the radio. Only K5/K6 firmware is safe here.")
        return 0
    except Exception as e:                                  # noqa: BLE001
        err(str(e))
        return 2


def cmd_firmware_info(args) -> int:
    """Inspect a Quansheng firmware .bin file. Read-only; no radio."""
    from .kradio import firmware as fw_mod

    p = Path(args.file)
    if not p.is_file():
        err(f"Not a file: {p}")
        return 2
    try:
        info_obj = fw_mod.parse_firmware_file(p)
    except Exception as e:                              # noqa: BLE001
        err(f"Could not parse {p}: {e}")
        return 2

    fmt = (
        "raw ARM binary" if info_obj.is_raw
        else ("packed + CRC" if info_obj.has_crc else "packed (no CRC)")
    )
    if HAVE_RICH:
        tbl = Table(title=f"Firmware: {p.name}", header_style="bold")
        tbl.add_column("Field")
        tbl.add_column("Value")
        tbl.add_row("Path",            str(info_obj.path))
        tbl.add_row("File size",       f"{info_obj.size_bytes} bytes")
        tbl.add_row("Format",          fmt)
        tbl.add_row("CRC validates",   "yes" if info_obj.crc_valid else
                                       ("n/a" if not info_obj.has_crc else "NO"))
        tbl.add_row("Version string",  info_obj.version_string)
        tbl.add_row("Decoded size",    f"{info_obj.decoded_size} bytes "
                                       f"(0x{info_obj.decoded_size:04X})")
        tbl.add_row("Fits K5/K6 (≤60 KB)", "yes" if info_obj.fits_k5_k6 else "NO")
        tbl.add_row("Fits K1/K5 V3 (≤96 KB)",
                    "yes" if info_obj.fits_k1_k5v3 else "NO — too large")
        console.print(tbl)
    else:
        print(f"  Path:                {info_obj.path}")
        print(f"  File size:           {info_obj.size_bytes} bytes")
        print(f"  Format:              {fmt}")
        print(f"  CRC validates:       "
              f"{'yes' if info_obj.crc_valid else 'n/a' if not info_obj.has_crc else 'NO'}")
        print(f"  Version string:      {info_obj.version_string}")
        print(f"  Decoded size:        {info_obj.decoded_size} bytes")
        print(f"  Fits K5/K6 (≤60 KB): {'yes' if info_obj.fits_k5_k6 else 'NO'}")
        print(f"  Fits K1/K5 V3:       {'yes' if info_obj.fits_k1_k5v3 else 'NO'}")
    return 0


def cmd_apply_full(args) -> int:
    from .kradio.models import select_profile

    eeprom_path = Path(args.eeprom)
    image = eeprom_path.read_bytes()
    info(f"Loaded EEPROM image: {eeprom_path}  ({len(image)} bytes)")

    if args.dry_run:
        # No radio attached — pick the profile from the .bin's size
        # (same heuristic as make-bin's auto path).
        guessed_profile, label = _resolve_profile_for_image(image, "auto")
        prog_size = guessed_profile.prog_size
        info(f"Profile (guessed from size): "
             f"{label or guessed_profile.name}  "
             f"(prog 0x{prog_size:04x})")
        if len(image) < prog_size:
            err(f"EEPROM image too short ({len(image)} < {prog_size})")
            return 2
        n_blocks = (prog_size + proto.MEM_BLOCK - 1) // proto.MEM_BLOCK
        info(f"DRY-RUN: would write {n_blocks} blocks "
             f"(0x0000..0x{prog_size:04x})")
        return 0

    rp = proto.open_radio(args.port)
    fw = proto.hello(rp)
    info(f"Firmware: {fw}")

    # Pick the radio profile from the firmware string so we know which
    # program region to write (8 KB stock vs 41 KB F4HWN).
    profile = select_profile(fw)
    info(f"Profile: {profile.name}  (prog 0x{profile.prog_size:04x})")
    if len(image) < profile.prog_size:
        err(f"EEPROM image too short for profile "
            f"({len(image)} < {profile.prog_size})")
        return 2

    verify = not args.no_verify
    try:
        if HAVE_RICH:
            n_blocks = (profile.prog_size + proto.MEM_BLOCK - 1) // proto.MEM_BLOCK
            with Progress(
                TextColumn("[bold]{task.fields[label]}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total} blocks"),
                TimeRemainingColumn(),
            ) as p:
                t_up = p.add_task("up", total=n_blocks, label="Upload (CHIRP-style)")
                t_vf = (
                    p.add_task("vf", total=profile.prog_size, label="Verify (read-back)")
                    if verify else None
                )
                wf.upload_eeprom_chirp_style(
                    rp, image,
                    prog_size=profile.prog_size,
                    progress_cb=lambda done, total: p.update(t_up, completed=done),
                    verify=verify,
                    verify_progress_cb=(
                        (lambda done, total: p.update(t_vf, completed=done))
                        if t_vf is not None else None
                    ),
                )
        else:
            wf.upload_eeprom_chirp_style(
                rp, image, prog_size=profile.prog_size, verify=verify
            )
    except RuntimeError as e:
        err(f"Upload failed verify: {e}")
        return 3

    if verify:
        ok("Upload + readback verified. The radio will reboot.")
    else:
        ok("Upload complete (verify skipped). The radio will reboot.")
    return 0


# ------------------------------------------------------------------------- #
# argparse setup                                                            #
# ------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=(
            "Quansheng UV-K1 / UV-K5 toolkit. Supports F4HWN Fusion 5.x "
            "(45 KB / 1024 channels / 24 scan lists) and stock Quansheng "
            "firmware on UV-K1 7.03.x and UV-K5/K6 7.00.x (8 KB / 200 "
            "channels / 4 scan-list states). Profile is auto-detected "
            "from the firmware string when talking to the radio, or from "
            "image size when working offline."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Standard scan-list workflow:\n"
            "  python -m quansheng_toolkit read -o current.bin\n"
            "  python -m quansheng_toolkit make-bin -i current.bin --csv X.csv \\\n"
            "      --derive-from-comment -o patched.bin --show\n"
            "  python -m quansheng_toolkit apply-full --eeprom patched.bin\n"
            "\n"
            "First-time on a new radio:\n"
            "  python -m quansheng_toolkit info       # check handshake + profile\n"
            "  python -m quansheng_toolkit read -o factory_pristine.bin   # backup\n"
        ),
    )
    p.add_argument("--port", help="Serial port (e.g. COM4, /dev/ttyUSB0). "
                                  "Auto-detected if omitted.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("info", help="Show radio firmware and EEPROM stats")
    sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("read", help="Download the full EEPROM into a .bin file")
    sp.add_argument("--output", "-o",
                    help=".bin output path (default: backups/eeprom_<ts>.bin)")
    sp.set_defaults(func=cmd_read)

    sp = sub.add_parser("list", help="Show configured channels with scan lists")
    sp.add_argument("--from-file", help="Decode an EEPROM .bin instead of the radio")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("show-settings",
                        help="Display the radio's general settings (read-only)")
    sp.add_argument("--from-file", help="Decode an EEPROM .bin instead of the radio")
    sp.set_defaults(func=cmd_show_settings)

    sp = sub.add_parser(
        "make-bin",
        help="Build a patched EEPROM image from a source .bin (offline, no radio)"
    )
    sp.add_argument("--input", "-i", required=True,
                    help="Source EEPROM image (e.g. backup.bin)")
    sp.add_argument("--profile",
                    choices=["auto", "f4hwn", "k1stock", "k5stock",
                             "k5v1", "stock"],
                    default="auto",
                    help="EEPROM layout. 'auto' (default) infers it from "
                         "image size: 8 KB → stock K1/K5 family, "
                         "≥41 KB → F4HWN Fusion 5.x. K1 stock and K5(8) "
                         "stock share an IDENTICAL 8 KB layout; the two "
                         "explicit names exist for label clarity but the "
                         "produced bytes are the same. 'k5v1' is a legacy "
                         "alias for 'k5stock'.")
    sp.add_argument("--csv", help="CSV with Name + ScList (or LISTA N in Comment) "
                                  "for scanlist-only updates")
    sp.add_argument("--channels-csv",
                    help="Full channel import: CHIRP-compatible CSV "
                         "(Location/Name/Frequency/Mode/Duplex/Offset/Power/...)")
    sp.add_argument("--clear-missing-channels", action="store_true",
                    help="With --channels-csv: erase channel slots not in the CSV")
    sp.add_argument("--derive-from-comment", action="store_true",
                    help="If ScList is missing, scrape 'LISTA N' from Comment")
    sp.add_argument("--ch", help="Channel range (e.g. 1-17) — alternative to --csv")
    sp.add_argument("--scanlist", "-s",
                    help="Single scanlist value to set (with --ch)")
    sp.add_argument("--boot-channel", type=int,
                    help="Override boot channel (1-based) — preserves the rest")
    sp.add_argument("--set", action="append", metavar="KEY=VALUE",
                    help="Set a named setting (repeatable). "
                         "See `list-settings` for the full list of keys.")
    sp.add_argument("--output", "-o", required=True, help="Output .bin path")
    sp.add_argument("--show", action="store_true",
                    help="Print a table of the pending changes")
    sp.set_defaults(func=cmd_make_bin)

    sp = sub.add_parser(
        "list-settings",
        help="List all named settings that 'make-bin --set' can change"
    )
    sp.add_argument("--profile",
                    choices=["f4hwn", "k5v1"],
                    default="f4hwn",
                    help="Which registry to list (default: f4hwn)")
    sp.set_defaults(func=cmd_list_settings)

    sp = sub.add_parser(
        "firmware-info",
        help="Inspect a Quansheng firmware .bin file (offline, read-only)"
    )
    sp.add_argument("file", help="firmware file to inspect")
    sp.set_defaults(func=cmd_firmware_info)

    sp = sub.add_parser(
        "dfu-info",
        help="Connect to a radio in DFU mode + identify the bootloader "
             "(read-only; needs the radio in PTT-held boot mode)"
    )
    sp.add_argument("--timeout", type=float, default=15.0,
                    help="Seconds to wait for the first NOTIFY_DEV_INFO "
                         "broadcast (default: 15)")
    sp.set_defaults(func=cmd_dfu_info)

    sp = sub.add_parser(
        "dfu-flash",
        help="Flash a firmware .bin to a radio in DFU mode (DESTRUCTIVE — "
             "triple-confirmation required)"
    )
    sp.add_argument("--eeprom", required=True,
                    help="firmware .bin to flash (raw or packed format)")
    sp.add_argument("--target", required=True,
                    choices=["k5_k6", "k5_v3", "k1"],
                    help="Which radio family this firmware is for. The "
                         "bootloader-version allowlist refuses any "
                         "mismatch before any byte is written.")
    sp.add_argument("--timeout", type=float, default=15.0,
                    help="Seconds to wait for bootloader handshake")
    sp.add_argument("--yes-i-understand", action="store_true",
                    help="Skip the typed 'FLASH' confirmation prompt. ONLY "
                         "use in scripts that already vetted the target.")
    sp.set_defaults(func=cmd_dfu_flash)

    sp = sub.add_parser(
        "apply-full",
        help="CHIRP-style sequential upload of a complete EEPROM .bin"
    )
    sp.add_argument("--eeprom", required=True, help=".bin to upload")
    sp.add_argument("--dry-run", action="store_true",
                    help="Don't actually write to the radio")
    sp.add_argument("--no-verify", action="store_true",
                    help="Skip the readback-and-diff verify step (faster, "
                         "but a silently-dropped block won't be caught)")
    sp.set_defaults(func=cmd_apply_full)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except proto.RadioError as e:
        err(f"Radio error: {e}")
        return 2
    except KeyboardInterrupt:
        err("Interrupted by user.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
