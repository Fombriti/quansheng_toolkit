# Contributing to quansheng_toolkit

Thanks for your interest! This project lives at the intersection of
open-source HAM radio modding and reverse-engineered Quansheng
hardware. The bar for contributions is "make this safer or more useful
for someone with a real radio in front of them" — we accept everything
from documentation fixes and CSV templates up to new radio profiles
and firmware-flash improvements.

## Quick orientation

```
quansheng_toolkit/
├── kradio/                  # protocol, memory, settings, dfu, firmware
│   ├── protocol.py          # EEPROM read/write protocol (38400 baud)
│   ├── dfu.py               # bootloader handshake + flash write
│   ├── firmware.py          # offline .bin parser
│   ├── memory.py            # F4HWN channel layout
│   ├── memory_uvk5_v1.py    # K5 V1 / K1 stock channel layout
│   ├── settings.py          # F4HWN settings registry
│   ├── settings_uvk5_v1.py  # stock K5/K1 settings registry
│   ├── tones.py             # CTCSS / DTCS tables
│   ├── workflow.py          # high-level orchestration
│   └── models/__init__.py   # RadioProfile registry
├── gui/                     # PySide6 app
│   ├── views/               # one tab per concern
│   ├── workers.py           # QThread workers for radio I/O
│   └── main_window.py       # everything wires up here
├── cli.py                   # argparse subcommands
└── tests/                   # pytest, ~290 tests, no hardware needed
```

If you're touching the wire protocol or anything that could brick a
radio, **please** read the safety section below before you push.

## Setting up

```bash
git clone https://github.com/Fombriti/quansheng_toolkit.git
cd quansheng_toolkit
pip install -e ".[dev]"
pytest -q       # should be green on a fresh checkout
```

The PySide6 GUI is optional for unit tests but required for any GUI
work. Install it with `pip install PySide6`.

## Testing on hardware

The unit tests run entirely offline against synthesized EEPROM images,
but real hardware coverage is what makes this project useful. Before
testing on your radio:

1. **Always dump first.** `python -m quansheng_toolkit read -o backup.bin`
   gives you a guaranteed rollback. Keep it. Don't lose it.
2. **Pristine factory dump if you can.** When a new radio arrives,
   the very first thing to do is dump it before any modification. The
   factory calibration of *that specific unit* lives in there — recovering
   it later is non-trivial, especially on stock 7.00.x firmware where
   cal writes are firmware-blocked (see Known limitations in README).
3. **DFU recovery is your safety net.** Holding PTT while powering the
   radio on always returns you to the bootloader regardless of what's
   in the firmware region. So a bad flash is recoverable; a bad
   calibration write may not be.

When you push a change that touches a write path, document the
hardware test in the PR (radio model, firmware version, what you wrote
where, before/after read-back).

## Adding a new radio profile

A profile is a `RadioProfile` instance in `kradio/models/__init__.py`.
You'll typically need:

1. A firmware-string signature (prefix or full match) so
   `select_profile()` picks it up.
2. EEPROM size + program region size + calibration region offset.
3. A memory module exposing `decode_all_channels`, `addr_*`,
   `patch_channel_in_image`, `parse_scanlist_spec`, `SCAN_LIST_LABELS`.
4. (Optional) A settings module exposing `SETTINGS_REGISTRY`,
   `read_setting`, `apply_setting`, `list_settings`.
5. `verified=False` until you've round-tripped read+write on real
   hardware. Promote to `verified=True` only after confirming.

If the new radio shares an EEPROM layout with an existing profile (e.g.
all stock K5 family share `memory_uvk5_v1`), reuse the modules and
just add a new firmware signature. Don't fork the modules.

## Adding a new bootloader version (DFU flash)

Open `kradio/dfu.py` and update two tables:

```python
BOOTLOADER_TO_MODEL: dict[str, str] = {
    "5.00.01": "UV-K5 V2",
    ...
    "your.new.bl": "your radio name",
}

ALLOWED_BOOTLOADERS_BY_TARGET: dict[str, frozenset[str]] = {
    FLASH_TARGET_K5_K6: frozenset({"5.00.01", "2.00.06"}),
    ...
}
```

The allowlist is intentionally restrictive: any bootloader version not
in `ALLOWED_BOOTLOADERS_BY_TARGET[target]` makes
`assert_safe_to_flash()` raise. Add a new version only after you've
confirmed on hardware that the protocol works for the corresponding
target — flashing K1 firmware to a UV-K5 V2 bootloader is a
permanent brick and the allowlist is the last line of defence.

Update `tests/test_dfu.py` with the new mapping and a brick-protection
test for the new combination.

## Code style

- Python 3.11+; we use type hints where they help the reader, not
  exhaustively. Don't bring in `typing.cast` or runtime checks just
  to satisfy a linter.
- 4-space indent, snake_case for functions and modules, PascalCase for
  classes.
- No `from __future__ import annotations` requirement — write the
  hints normally.
- Comments: explain *why*, not *what*. `# clear the busy bit` is
  noise; `# bit 7 is "is_calibrated", clear before re-cal write`
  is useful.
- One concept per commit; commit messages explain the change in
  plain English. The first line is a short summary; the body
  describes the motivation and any non-obvious tradeoff.

## Safety policy

This is the part that matters.

- **Never enable a destructive operation by default.** DFU writes,
  calibration restores, and full-image uploads must require explicit
  user confirmation (typed string, triple-confirm dialog, or a
  documented `--yes-i-understand` CLI flag). Do not paper over a
  protection check with a hardcoded `True`.
- **The anti-brick allowlist is sacrosanct.** If you're tempted to
  add a fallback `if version not in BOOTLOADER_TO_MODEL: ...try
  anyway`, the answer is no. Add the version explicitly after
  confirming it works.
- **Read-only operations should stay read-only forever.** `info`,
  `read`, `firmware-info`, `dfu-info`, `show-settings`, `list` —
  these never write. Reviewers will reject any PR that introduces a
  side effect into one of these.
- **Cal regions are special.** The K5 V3 stock 7.00.x firmware
  silently swallows cal writes; your test must read back what you
  wrote *without* a reset to confirm the bytes actually persisted.
  ACKs alone don't prove anything.

If you spot a safety issue in the existing code, please open an issue
or PR even if you're not sure it's a problem — better to over-flag
than to find out the hard way.

## Found a new bootloader-to-firmware mapping?

We track these in `BOOTLOADER_TO_MODEL`. Right now it covers the
documented the upstream K5/K1 tooling set (`2.00.06`, `5.00.01`, `7.00.07`,
`7.02.02`, `7.03.01..03`). If your radio reports a different
bootloader version, please open an issue with:

1. The exact bootloader string from `dfu-info` output
2. The exact firmware version from `info` output (normal mode)
3. The radio model on the back label
4. (Optional) which family the firmware files for your radio belong
   to — K5/K6/5R, K5 V3, or K1

We'll add the mapping once we have a confident pairing.

## F4HWN K1: the channel-attribute trap

For posterity, the bug that cost an evening on the UV-K1(8) v3:

When you write a channel via `apply-full` to a radio whose flash has
either uninitialised bytes (`0xFF 0xFF`) or the upstream empty-slot
marker (`0x07 0x00`) at the channel-attribute address (`0x8000 + N*2`),
the F4HWN K1 firmware will silently treat the slot as **excluded**
(invisible to V/M, ChName / ChDel return NULL) — even if the channel
record at `0x0000+` and the channel name at `0x4000+` look fine.

The cause: the 16-bit `ChannelAttributes_t` struct
(`App/misc.h` in `armel/uv-k1-k5v3-firmware-custom`) packs
`band:3, compander:2, unused:2, exclude:1, scanlist:8`. Bit 7 of byte
0 is `exclude`. If you only OR the new band into the existing byte,
the prior 0xFF or 0x07 leaves bit 7 set → channel hidden.

**Rule:** when writing channel attributes, always overwrite both
bytes from scratch. Do not preserve prior bits via OR. Empty slots
should use `0x07 0x00` (band=7 = "uninitialised" marker, same as
what `SETTINGS_UpdateChannel()` writes for fresh channels), not
`0xFF 0xFF`.

The toolkit fix lives in `kradio/memory.py::patch_channel_in_image`
with a regression test in `tests/test_memory.py::TestChannelAttributeBitClean`.
If you add a new memory module for a different radio, mirror this
pattern: read freq → derive band → write the full attribute word
with `exclude=0` and `compander=0` unless you have a reason.

## Pull request checklist

- [ ] `pytest -q` green
- [ ] If you touched the wire protocol or any write path, you tested
      on real hardware and noted the result in the PR description.
- [ ] If you added a new radio profile, `verified` flag matches reality.
- [ ] No new `print()` statements in the library code (use logging
      or the GUI's status bar).
- [ ] No GUI-only behaviour without a CLI equivalent (or vice versa)
      unless there's a good reason.
- [ ] `README.md` updated if user-visible behaviour changed.

## License

Apache 2.0 (see `LICENSE`). By contributing you agree your changes
ship under the same license. Acknowledgements go in `NOTICE`.
