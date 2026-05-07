# Bundled firmwares

These are the firmware images we ship with the toolkit so users can
flash a known-good build with one click from the Firmware tab — no
file picker, no version hunting. Two families:

* **F4HWN** open-source custom firmware (Apache-2.0)
* **Quansheng** vendor stock firmware (proprietary, freely distributed
  by the manufacturer)

| File | Variant | Vendor | MCU | Target | Source |
|------|---------|--------|-----|--------|--------|
| `f4hwn_fusion_5.4.0_k1_k5v3.bin` ★ | F4HWN Fusion 5.4.0 | F4HWN (Apache-2.0) | PY32F071 | `k5_v3` / `k1` | [armel/uv-k1-k5v3-firmware-custom v5.4.0](https://github.com/armel/uv-k1-k5v3-firmware-custom/releases/tag/v5.4.0) |
| `f4hwn_fusion_5.3.1_k1_k5v3.bin` | F4HWN Fusion 5.3.1 | F4HWN (Apache-2.0) | PY32F071 | `k5_v3` / `k1` | [armel/uv-k1-k5v3-firmware-custom v5.3.1](https://github.com/armel/uv-k1-k5v3-firmware-custom/releases/tag/v5.3.1) |
| `f4hwn_fusion_4.3.2_k5v3.bin` | F4HWN Fusion 4.3.2 | F4HWN (Apache-2.0) | PY32F071 | `k5_v3` | armel/uv-k1-k5v3-firmware-custom |
| `f4hwn_4.3_k5_k6_basic.bin` ★ | F4HWN 4.3 K5/K6 standard | F4HWN (Apache-2.0) | DP32G030 | `k5_k6` | [armel/uv-k5-firmware-custom v4.3](https://github.com/armel/uv-k5-firmware-custom/releases/tag/v4.3) |
| `f4hwn_4.3_k5_k6_bandscope.bin` | F4HWN 4.3 K5/K6 + bandscope | F4HWN (Apache-2.0) | DP32G030 | `k5_k6` | [armel/uv-k5-firmware-custom v4.3](https://github.com/armel/uv-k5-firmware-custom/releases/tag/v4.3) |
| `f4hwn_4.3_k5_k6_broadcast.bin` | F4HWN 4.3 K5/K6 + extended FM broadcast | F4HWN (Apache-2.0) | DP32G030 | `k5_k6` | [armel/uv-k5-firmware-custom v4.3](https://github.com/armel/uv-k5-firmware-custom/releases/tag/v4.3) |
| `stock_k1_7.03.01.bin` | Quansheng UV-K1 Stock 7.03.01 | Quansheng | PY32F071 | `k1` | [Quansheng official downloads](https://en.qsfj.com/support/downloads/) |
| `stock_k1_7.02.02.bin` | Quansheng UV-K1 Stock 7.02.02 | Quansheng | PY32F071 | `k1` | [Quansheng official downloads](https://en.qsfj.com/support/downloads/) |
| `stock_k5v3_7.00.11.bin` | Quansheng UV-K5 V3 Stock 7.00.11 | Quansheng | PY32F071 | `k5_v3` | [Quansheng official downloads](https://en.qsfj.com/support/downloads/) |

★ = recommended starting point for that radio family.

The `manifest.json` next to these files is the machine-readable index
the GUI's Firmware tab reads at startup. To add a new firmware: drop
the `.bin` here, append an entry to `manifest.json` (the `id`,
`filename`, `target`, `compatible_targets`, `mcu`, `supports`,
`release_url`, `license` keys are required) and bump
`schema_version` only if you broke a field name.

## Licenses & credits

**F4HWN firmwares** are open-source custom firmware by Armel and
contributors, distributed under the Apache 2.0 license. The `.bin`
files in this directory are exact copies of the upstream GitHub
release assets; sha256 hashes in `manifest.json` match the upstream
digests. Source repositories:

* https://github.com/armel/uv-k1-k5v3-firmware-custom (PY32F071 family)
* https://github.com/armel/uv-k5-firmware-custom (DP32G030 family)

**Quansheng stock firmwares** are made by Fujian Quansheng Electronics
Co., Ltd. (the radio's manufacturer) and distributed for free from
[their official downloads page](https://en.qsfj.com/support/downloads/)
to anyone who owns one of their radios. We bundle them here for
convenience — the binaries are unmodified copies of the official
release files. All credit, copyright and authorship belong to
Quansheng. Re-distribution is informal: if Quansheng's legal
department prefers us not to mirror their files, we'll switch to a
download-on-demand model immediately. The `vendor` field in
`manifest.json` makes the source explicit in the GUI; users see a
clear `[Quansheng stock]` tag on these entries.

If you're a maintainer of any bundled firmware and would prefer we
not redistribute the binaries, please open an issue and we'll fall
back to fetching them at runtime instead.

## How the GUI uses this

The Firmware tab populates a "Bundled firmware" dropdown from
`manifest.json`. When the user picks one:

* The target is auto-set from `manifest["target"]` — no separate
  family-picker dialog
* The anti-brick allowlist (`kradio.dfu.assert_safe_to_flash`) still
  runs against the live bootloader version, so a bundled firmware
  can't be flashed onto an incompatible radio
* The user can still load a custom `.bin` from disk for ad-hoc
  flashing — bundled is just a shortcut

## Updating

When a new F4HWN release lands, drop the `.bin` here, append the
entry, mark the previous one with `is_recommended: false`, and the
new one with `is_recommended: true`. Bump `pyproject.toml` minor
version when changing the bundle so a `pip --upgrade` brings users
to the new flash payload.
