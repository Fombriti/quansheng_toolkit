"""
Theme system: dark and light palettes (Catppuccin Mocha & Latte) + a
ThemeManager that supports three modes — System (auto-detect), Light,
and Dark — and produces the QSS stylesheet.

The two palettes share the same set of named slots so any view that uses
`Palette` works identically in both modes. Switching is a one-line call
to `ThemeManager.set_mode()`.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication


class ThemeMode(str, Enum):
    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


@dataclass(frozen=True)
class Palette:
    name: str

    # Background tiers
    base: str
    mantle: str
    crust: str
    surface0: str
    surface1: str
    surface2: str

    # Text tiers
    text: str
    subtext1: str
    subtext0: str
    overlay: str

    # Accents
    blue: str
    sapphire: str
    teal: str
    green: str
    yellow: str
    peach: str
    red: str
    maroon: str
    pink: str
    mauve: str
    lavender: str

    # Layout style — drives which QSS template is used.
    # "studio"  — soft cards, sidebar nav, sans-serif
    # "cockpit" — top tab bar, instrument panels, monospace numbers
    style_kind: str = "studio"

    # Whether the palette is light-themed (drives contrast choices in QSS).
    is_light: bool = False

    # Cockpit-specific colors. For studio palettes these are unused.
    panel: str = ""        # instrument panel surface
    border_warm: str = ""  # instrument panel border (typically warm tone)
    led_on: str = ""       # status LED ON colour
    led_off: str = ""      # status LED OFF colour
    # Foreground colour to use on top of accent-coloured surfaces (primary
    # buttons, active tabs). Dark themes typically want the page bg here so
    # text appears as "negative space"; light themes want pure white for
    # maximum contrast against bright accents.
    fg_on_accent: str = ""

    # Semantic helpers (computed from the tiers above)
    @property
    def primary(self) -> str: return self.blue
    @property
    def primary_hover(self) -> str: return self.lavender
    @property
    def warning(self) -> str: return self.yellow
    @property
    def danger(self) -> str: return self.red
    @property
    def success(self) -> str: return self.green


# Catppuccin Mocha — dark
DARK = Palette(
    name="dark",
    base="#1e1e2e",
    mantle="#181825",
    crust="#11111b",
    surface0="#313244",
    surface1="#45475a",
    surface2="#585b70",
    text="#cdd6f4",
    subtext1="#bac2de",
    subtext0="#a6adc8",
    overlay="#7f849c",
    blue="#89b4fa",
    sapphire="#74c7ec",
    teal="#94e2d5",
    green="#a6e3a1",
    yellow="#f9e2af",
    peach="#fab387",
    red="#f38ba8",
    maroon="#eba0ac",
    pink="#f5c2e7",
    mauve="#cba6f7",
    lavender="#b4befe",
)

# Catppuccin Latte — light
LIGHT = Palette(
    name="light",
    base="#eff1f5",
    mantle="#e6e9ef",
    crust="#dce0e8",
    surface0="#ccd0da",
    surface1="#bcc0cc",
    surface2="#acb0be",
    text="#4c4f69",
    subtext1="#5c5f77",
    subtext0="#6c6f85",
    overlay="#8c8fa1",
    blue="#1e66f5",
    sapphire="#209fb5",
    teal="#179299",
    green="#40a02b",
    yellow="#df8e1d",
    peach="#fe640b",
    red="#d20f39",
    maroon="#e64553",
    pink="#ea76cb",
    mauve="#8839ef",
    lavender="#7287fd",
)


# Tokyo Night — moody dark with rich blues and purples.
TOKYO_NIGHT = Palette(
    name="tokyo-night",
    base="#1a1b26",
    mantle="#16161e",
    crust="#0f0f17",
    surface0="#24283b",
    surface1="#2f334d",
    surface2="#414868",
    text="#c0caf5",
    subtext1="#a9b1d6",
    subtext0="#9aa5ce",
    overlay="#565f89",
    blue="#7aa2f7",
    sapphire="#7dcfff",
    teal="#73daca",
    green="#9ece6a",
    yellow="#e0af68",
    peach="#ff9e64",
    red="#f7768e",
    maroon="#db4b4b",
    pink="#bb9af7",
    mauve="#bb9af7",
    lavender="#7aa2f7",
)

# Nord — cool, desaturated, scandinavian dark.
NORD = Palette(
    name="nord",
    base="#2e3440",
    mantle="#272b36",
    crust="#1f242c",
    surface0="#3b4252",
    surface1="#434c5e",
    surface2="#4c566a",
    text="#eceff4",
    subtext1="#e5e9f0",
    subtext0="#d8dee9",
    overlay="#7b88a1",
    blue="#88c0d0",
    sapphire="#81a1c1",
    teal="#8fbcbb",
    green="#a3be8c",
    yellow="#ebcb8b",
    peach="#d08770",
    red="#bf616a",
    maroon="#bf616a",
    pink="#b48ead",
    mauve="#b48ead",
    lavender="#88c0d0",
)

# GitHub Dark — neutral grays, blue accent, corporate-clean.
GITHUB_DARK = Palette(
    name="github-dark",
    base="#0d1117",
    mantle="#010409",
    crust="#000000",
    surface0="#161b22",
    surface1="#21262d",
    surface2="#30363d",
    text="#c9d1d9",
    subtext1="#b1bac4",
    subtext0="#8b949e",
    overlay="#6e7681",
    blue="#58a6ff",
    sapphire="#39c5cf",
    teal="#56d4dd",
    green="#56d364",
    yellow="#e3b341",
    peach="#f0883e",
    red="#f85149",
    maroon="#da3633",
    pink="#db61a2",
    mauve="#bc8cff",
    lavender="#58a6ff",
)

# Synthwave '84 — neon magenta and cyan on deep purple. Loud + fun.
SYNTHWAVE = Palette(
    name="synthwave-84",
    base="#241b2f",
    mantle="#1f1830",
    crust="#191428",
    surface0="#34294f",
    surface1="#463465",
    surface2="#5a4480",
    text="#f7f7f8",
    subtext1="#dee2e6",
    subtext0="#b9b3c5",
    overlay="#856aaf",
    blue="#36f9f6",
    sapphire="#03edf9",
    teal="#72f1b8",
    green="#72f1b8",
    yellow="#fede5d",
    peach="#fe4450",
    red="#fe4450",
    maroon="#ff7edb",
    pink="#ff7edb",
    mauve="#ff7edb",
    lavender="#ff7edb",
)

# Solarized Dark — classic warm-toned terminal aesthetic.
SOLARIZED_DARK = Palette(
    name="solarized-dark",
    base="#002b36",
    mantle="#073642",
    crust="#012a35",
    surface0="#0a3a47",
    surface1="#586e75",
    surface2="#657b83",
    text="#eee8d5",
    subtext1="#e0d6b9",
    subtext0="#93a1a1",
    overlay="#839496",
    blue="#268bd2",
    sapphire="#2aa198",
    teal="#2aa198",
    green="#859900",
    yellow="#b58900",
    peach="#cb4b16",
    red="#dc322f",
    maroon="#cb4b16",
    pink="#d33682",
    mauve="#6c71c4",
    lavender="#268bd2",
)


# ----------------------------------------------------------- #
# Cockpit palettes — instrument-panel aesthetic, 5 variants.   #
# ----------------------------------------------------------- #

COCKPIT_AMBER = Palette(
    name="cockpit-amber",
    style_kind="cockpit",
    base="#0d0e10",
    mantle="#16171a",
    crust="#070809",
    surface0="#131418",
    surface1="#1c1e22",
    surface2="#252830",
    text="#c8e0d0",
    subtext1="#a8c0b0",
    subtext0="#88a888",
    overlay="#5a5040",
    blue="#ffaa33",        # accent — amber
    sapphire="#67e0ff",
    teal="#5dff8e",
    green="#5dff8e",
    yellow="#ffd75d",
    peach="#ff8e3d",
    red="#ff5050",
    maroon="#cc4040",
    pink="#ff9ec0",
    mauve="#bb88aa",
    lavender="#ffaa33",
    is_light=False, fg_on_accent="#0d0e10",
    panel="#131418",
    border_warm="#3a3022",
    led_on="#5dff8e",
    led_off="#444",
)

COCKPIT_AMBER_LIGHT = Palette(
    name="cockpit-amber-light",
    style_kind="cockpit",
    base="#fbf3e3", mantle="#f5e9c8", crust="#ead9a8",
    surface0="#ffffff", surface1="#f0e3c0", surface2="#d8c090",
    text="#1a1410", subtext1="#3a2a1a", subtext0="#7a5530",
    overlay="#a98a48",
    blue="#b56a00",        # deep amber for contrast on cream
    sapphire="#0a7aa3", teal="#0a8050",
    green="#2da050", yellow="#b58900", peach="#d06820",
    red="#c00020", maroon="#a01818", pink="#cc6090",
    mauve="#9a5085", lavender="#b56a00",
    is_light=True, fg_on_accent="#ffffff",
    panel="#ffffff",
    border_warm="#b08550",
    led_on="#1f9a3a", led_off="#c8bca0",
)

COCKPIT_CYAN_JET = Palette(
    name="cockpit-cyan-jet",
    style_kind="cockpit",
    base="#070d12", mantle="#0e161e", crust="#040810",
    surface0="#0e161e", surface1="#162028", surface2="#1e2a36",
    text="#cfe8f5", subtext1="#a8c8da", subtext0="#7faabd",
    overlay="#3a5066",
    blue="#67e0ff",        # accent — cyan
    sapphire="#67e0ff", teal="#5dff8e",
    green="#5dff8e", yellow="#ffd75d", peach="#ff8e3d",
    red="#ff5050", maroon="#cc4040", pink="#80b8d8",
    mauve="#90a8d0", lavender="#67e0ff",
    is_light=False, fg_on_accent="#070d12",
    panel="#0e161e",
    border_warm="#173144",
    led_on="#5dff8e",
    led_off="#2a2a2a",
)

COCKPIT_CYAN_JET_LIGHT = Palette(
    name="cockpit-cyan-jet-light",
    style_kind="cockpit",
    base="#eef5fb", mantle="#dceaf5", crust="#c8dcec",
    surface0="#ffffff", surface1="#e2eef8", surface2="#bcd0e0",
    text="#0a1822", subtext1="#26384a", subtext0="#456480",
    overlay="#7898b0",
    blue="#0668a0",
    sapphire="#0668a0", teal="#0a8050",
    green="#2d9a4a", yellow="#b58900", peach="#cc6800",
    red="#c00020", maroon="#a01818", pink="#5a8aac",
    mauve="#5070a0", lavender="#0668a0",
    is_light=True, fg_on_accent="#ffffff",
    panel="#ffffff",
    border_warm="#7090b0",
    led_on="#1f9a3a", led_off="#c0d0dc",
)

COCKPIT_RED_TACTICAL = Palette(
    name="cockpit-red-tactical",
    style_kind="cockpit",
    base="#0d0808", mantle="#1a1010", crust="#080404",
    surface0="#1a1010", surface1="#231818", surface2="#2c1e1e",
    text="#e8d4d4", subtext1="#c8a8a8", subtext0="#b08080",
    overlay="#604040",
    blue="#ff5050", sapphire="#ff8e3d", teal="#ffaa33",
    green="#ffaa33", yellow="#ffd75d", peach="#ff8e3d",
    red="#ff7070", maroon="#ff5050", pink="#ff90b0",
    mauve="#cc7090", lavender="#ff5050",
    is_light=False, fg_on_accent="#0d0808",
    panel="#1a1010",
    border_warm="#3a1c1c",
    led_on="#ffaa33",
    led_off="#3a1818",
)

COCKPIT_RED_TACTICAL_LIGHT = Palette(
    name="cockpit-red-tactical-light",
    style_kind="cockpit",
    base="#fbf2f2", mantle="#f5e2e2", crust="#ead0d0",
    surface0="#ffffff", surface1="#f0dada", surface2="#d8b8b8",
    text="#2a0a0a", subtext1="#4a1a1a", subtext0="#7a3030",
    overlay="#a06060",
    blue="#a80020",
    sapphire="#cc5800", teal="#a86000",
    green="#1f9a3a", yellow="#b58900", peach="#cc4800",
    red="#a80020", maroon="#7a0010", pink="#c84878",
    mauve="#a04060", lavender="#a80020",
    is_light=True, fg_on_accent="#ffffff",
    panel="#ffffff",
    border_warm="#b06060",
    led_on="#cc6000", led_off="#d8c0c0",
)

COCKPIT_PHOSPHOR = Palette(
    name="cockpit-phosphor",
    style_kind="cockpit",
    base="#040a06", mantle="#0a1410", crust="#020806",
    surface0="#0a1410", surface1="#0f1c16", surface2="#15281e",
    text="#c8f5d0", subtext1="#a0d0a8", subtext0="#5fa770",
    overlay="#3a5a40",
    blue="#5dff8e", sapphire="#9dff5d", teal="#5dff8e",
    green="#5dff8e", yellow="#dfff5d", peach="#aaff5d",
    red="#ff5d5d", maroon="#cc4040", pink="#9deea0",
    mauve="#88c090", lavender="#5dff8e",
    is_light=False, fg_on_accent="#040a06",
    panel="#0a1410",
    border_warm="#193827",
    led_on="#9dff5d",
    led_off="#2a3a30",
)

COCKPIT_PHOSPHOR_LIGHT = Palette(
    name="cockpit-phosphor-light",
    style_kind="cockpit",
    base="#f1faf3", mantle="#dff0e3", crust="#c8e0cd",
    surface0="#ffffff", surface1="#e2f0e6", surface2="#b8d8bf",
    text="#082a14", subtext1="#1a4828", subtext0="#3a7050",
    overlay="#608878",
    blue="#0a7a3a",
    sapphire="#0a8050", teal="#0a8050",
    green="#1f9a3a", yellow="#7a8500", peach="#807000",
    red="#c00020", maroon="#7a0010", pink="#508870",
    mauve="#608070", lavender="#0a7a3a",
    is_light=True, fg_on_accent="#ffffff",
    panel="#ffffff",
    border_warm="#609880",
    led_on="#0a7a3a", led_off="#bcd4c4",
)

COCKPIT_SYNTHWAVE = Palette(
    name="cockpit-synthwave",
    style_kind="cockpit",
    base="#0e0820", mantle="#180f30", crust="#080418",
    surface0="#180f30", surface1="#221540", surface2="#2c1c54",
    text="#e8d4f5", subtext1="#c8a0e0", subtext0="#a08fc0",
    overlay="#5a3a80",
    blue="#ff7edb", sapphire="#36f9f6", teal="#36f9f6",
    green="#5dff8e", yellow="#fede5d", peach="#fe9e64",
    red="#fe4450", maroon="#fe6080", pink="#ff7edb",
    mauve="#bb9af7", lavender="#ff7edb",
    is_light=False, fg_on_accent="#0e0820",
    panel="#180f30",
    border_warm="#3a1a52",
    led_on="#36f9f6",
    led_off="#3a1a40",
)

COCKPIT_SYNTHWAVE_LIGHT = Palette(
    name="cockpit-synthwave-light",
    style_kind="cockpit",
    base="#fbf2fb", mantle="#f0dcec", crust="#e0c8dc",
    surface0="#ffffff", surface1="#f0dcec", surface2="#d8b0d0",
    text="#1a0822", subtext1="#3a1850", subtext0="#603878",
    overlay="#9070a0",
    blue="#a8208c",
    sapphire="#0a7aa3", teal="#0a8050",
    green="#1f9a3a", yellow="#a07000", peach="#cc4800",
    red="#a8208c", maroon="#7a1060", pink="#c050a8",
    mauve="#7a3098", lavender="#a8208c",
    is_light=True, fg_on_accent="#ffffff",
    panel="#ffffff",
    border_warm="#a87098",
    led_on="#0a7aa3", led_off="#dcc8d8",
)


# Public registry of named themes (every concrete palette by stable id).
NAMED_THEMES: dict[str, Palette] = {
    # Cockpit families — explicit dark+light pairs.
    "cockpit-amber":              COCKPIT_AMBER,           # alias = dark
    "cockpit-amber-dark":         COCKPIT_AMBER,
    "cockpit-amber-light":        COCKPIT_AMBER_LIGHT,
    "cockpit-cyan-jet":           COCKPIT_CYAN_JET,
    "cockpit-cyan-jet-dark":      COCKPIT_CYAN_JET,
    "cockpit-cyan-jet-light":     COCKPIT_CYAN_JET_LIGHT,
    "cockpit-red-tactical":       COCKPIT_RED_TACTICAL,
    "cockpit-red-tactical-dark":  COCKPIT_RED_TACTICAL,
    "cockpit-red-tactical-light": COCKPIT_RED_TACTICAL_LIGHT,
    "cockpit-phosphor":           COCKPIT_PHOSPHOR,
    "cockpit-phosphor-dark":      COCKPIT_PHOSPHOR,
    "cockpit-phosphor-light":     COCKPIT_PHOSPHOR_LIGHT,
    "cockpit-synthwave":          COCKPIT_SYNTHWAVE,
    "cockpit-synthwave-dark":     COCKPIT_SYNTHWAVE,
    "cockpit-synthwave-light":    COCKPIT_SYNTHWAVE_LIGHT,
    # Studio fallbacks (legacy).
    "catppuccin-mocha":           DARK,
    "catppuccin-latte":           LIGHT,
    "tokyo-night":                TOKYO_NIGHT,
    "nord":                       NORD,
    "github-dark":                GITHUB_DARK,
    "synthwave-84":               SYNTHWAVE,
    "solarized-dark":             SOLARIZED_DARK,
}

THEME_DISPLAY_NAMES: dict[str, str] = {
    "cockpit-amber":              "Cockpit · Amber (Dark)",
    "cockpit-amber-dark":         "Cockpit · Amber (Dark)",
    "cockpit-amber-light":        "Cockpit · Amber (Light)",
    "cockpit-cyan-jet-dark":      "Cockpit · Cyan Jet (Dark)",
    "cockpit-cyan-jet-light":     "Cockpit · Cyan Jet (Light)",
    "cockpit-red-tactical-dark":  "Cockpit · Red Tactical (Dark)",
    "cockpit-red-tactical-light": "Cockpit · Red Tactical (Light)",
    "cockpit-phosphor-dark":      "Cockpit · Phosphor (Dark)",
    "cockpit-phosphor-light":     "Cockpit · Phosphor (Light)",
    "cockpit-synthwave-dark":     "Cockpit · Synthwave (Dark)",
    "cockpit-synthwave-light":    "Cockpit · Synthwave (Light)",
    "catppuccin-mocha":           "Studio · Catppuccin Mocha",
    "catppuccin-latte":           "Studio · Catppuccin Latte (light)",
    "tokyo-night":                "Studio · Tokyo Night",
    "nord":                       "Studio · Nord",
    "github-dark":                "Studio · GitHub Dark",
    "synthwave-84":               "Studio · Synthwave '84",
    "solarized-dark":             "Studio · Solarized Dark",
}


# Theme families — dark/light variant pairs that the Toolkit Settings UI
# exposes as a single Family combo + Light/Dark/Auto mode toggle.
THEME_FAMILIES: dict[str, dict] = {
    "cockpit-amber":         {"label": "Cockpit · Amber",
                              "dark":  "cockpit-amber-dark",
                              "light": "cockpit-amber-light"},
    "cockpit-cyan-jet":      {"label": "Cockpit · Cyan Jet",
                              "dark":  "cockpit-cyan-jet-dark",
                              "light": "cockpit-cyan-jet-light"},
    "cockpit-red-tactical":  {"label": "Cockpit · Red Tactical",
                              "dark":  "cockpit-red-tactical-dark",
                              "light": "cockpit-red-tactical-light"},
    "cockpit-phosphor":      {"label": "Cockpit · Phosphor",
                              "dark":  "cockpit-phosphor-dark",
                              "light": "cockpit-phosphor-light"},
    "cockpit-synthwave":     {"label": "Cockpit · Synthwave",
                              "dark":  "cockpit-synthwave-dark",
                              "light": "cockpit-synthwave-light"},
    "studio-catppuccin":     {"label": "Studio · Catppuccin",
                              "dark":  "catppuccin-mocha",
                              "light": "catppuccin-latte"},
    "studio-tokyo-night":    {"label": "Studio · Tokyo Night",
                              "dark":  "tokyo-night",
                              "light": None},
    "studio-nord":           {"label": "Studio · Nord",
                              "dark":  "nord",
                              "light": None},
    "studio-github":         {"label": "Studio · GitHub Dark",
                              "dark":  "github-dark",
                              "light": None},
    "studio-synthwave-84":   {"label": "Studio · Synthwave '84",
                              "dark":  "synthwave-84",
                              "light": None},
    "studio-solarized":      {"label": "Studio · Solarized Dark",
                              "dark":  "solarized-dark",
                              "light": None},
}


def named_for_family(family: str, mode: str) -> str:
    """
    Resolve (family, mode) to a concrete named theme. `mode` is "auto",
    "light" or "dark". For "auto" the OS color scheme is consulted.
    Falls back gracefully if a family lacks a light variant.
    """
    fam = THEME_FAMILIES.get(family)
    if fam is None:
        return "cockpit-amber-dark"
    light = fam.get("light")
    dark = fam.get("dark") or "cockpit-amber-dark"

    if mode == "auto":
        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Light and light is not None:
            return light
        return dark
    if mode == "light":
        return light if light is not None else dark
    return dark


def stylesheet(p: Palette) -> str:
    """Top-level dispatcher: pick the right QSS template for the palette."""
    if p.style_kind == "cockpit":
        return stylesheet_cockpit(p)
    return stylesheet_studio(p)


def stylesheet_studio(p: Palette) -> str:
    """Render the application QSS for a given palette (Studio layout)."""
    # Light themes need slightly different shadows/borders to look right.
    is_light = p.name == "light"
    border = p.surface1 if is_light else p.surface0
    nav_hover = p.surface0
    nav_checked_bg = p.surface0 if is_light else p.surface1
    table_alt = p.mantle if is_light else p.base
    table_bg = p.base if is_light else p.mantle
    table_border = p.surface1 if is_light else p.surface0
    input_bg = p.base if is_light else p.mantle
    input_border = p.surface1 if is_light else p.surface1

    return f"""
    /* ---------- Base reset ---------- */
    * {{
        color: {p.text};
    }}
    QWidget {{
        background-color: {p.base};
        color: {p.text};
        font-family: "Inter", "Segoe UI Variable", "Segoe UI", "Helvetica Neue", system-ui, sans-serif;
        font-size: 14px;
    }}
    QMainWindow, QDialog {{ background-color: {p.base}; }}

    /* ---------- Sidebar ---------- */
    #Sidebar {{
        background-color: {p.mantle};
        border: none;
        border-right: 1px solid {border};
    }}
    #SidebarBrand {{
        color: {p.lavender};
        font-size: 18px;
        font-weight: 700;
        padding: 22px 20px 4px 20px;
        letter-spacing: 0.2px;
    }}
    #SidebarSubtitle {{
        color: {p.overlay};
        font-size: 12px;
        padding: 0 20px 18px 20px;
        letter-spacing: 0.3px;
    }}
    #SidebarTitle {{
        color: {p.subtext1};
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 1.5px;
        padding: 14px 22px 8px 22px;
    }}

    QPushButton#NavButton {{
        background-color: transparent;
        color: {p.subtext0};
        text-align: left;
        padding: 8px 10px;
        margin: 1px 4px;
        border: none;
        border-radius: 8px;
        font-size: 13px;
        font-weight: 500;
    }}
    QPushButton#NavButton:hover {{
        background-color: {nav_hover};
        color: {p.text};
    }}
    QPushButton#NavButton:checked {{
        background-color: {nav_checked_bg};
        color: {p.lavender};
        font-weight: 600;
    }}

    /* ---------- Content area ---------- */
    #ContentRoot {{ background-color: {p.base}; }}

    QLabel#PageHeading {{
        font-size: 26px;
        font-weight: 700;
        color: {p.text};
        padding: 0;
        margin: 0;
        letter-spacing: -0.4px;
    }}
    QLabel#PageSubheading {{
        font-size: 14px;
        color: {p.overlay};
        padding-top: 4px;
    }}

    /* ---------- Cards ---------- */
    QFrame#Card {{
        background-color: {p.mantle if is_light else p.surface0};
        border-radius: 12px;
        border: 1px solid {border};
    }}
    QLabel#CardTitle {{
        color: {p.subtext0};
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 1.5px;
    }}
    QLabel#CardValue {{
        color: {p.text};
        font-size: 18px;
        font-weight: 600;
    }}
    QLabel#StatBig {{
        color: {p.lavender};
        font-size: 30px;
        font-weight: 700;
        letter-spacing: -0.5px;
    }}
    QLabel#StatLabel {{
        color: {p.overlay};
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 1.5px;
    }}

    /* ---------- Inputs ---------- */
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background-color: {input_bg};
        color: {p.text};
        border: 1px solid {input_border};
        border-radius: 8px;
        padding: 7px 12px;
        font-size: 14px;
        selection-background-color: {p.blue};
        selection-color: {p.base};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 1px solid {p.blue};
    }}
    QLineEdit:disabled, QComboBox:disabled {{
        color: {p.overlay};
        background-color: {p.surface0};
    }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
    QComboBox QAbstractItemView {{
        background-color: {input_bg};
        border: 1px solid {input_border};
        selection-background-color: {p.surface1};
        selection-color: {p.text};
        outline: none;
        padding: 4px;
    }}

    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{
        width: 18px; height: 18px;
        border: 1.5px solid {p.surface2};
        border-radius: 5px;
        background-color: {input_bg};
    }}
    QCheckBox::indicator:checked {{
        background-color: {p.blue};
        border-color: {p.blue};
    }}

    /* ---------- Buttons ---------- */
    QPushButton#PrimaryBtn {{
        background-color: {p.blue};
        color: {'#ffffff' if is_light else p.base};
        border: none;
        border-radius: 8px;
        padding: 10px 20px;
        font-weight: 600;
        font-size: 14px;
    }}
    QPushButton#PrimaryBtn:hover {{ background-color: {p.lavender}; }}
    QPushButton#PrimaryBtn:disabled {{
        background-color: {p.surface1};
        color: {p.overlay};
    }}

    QPushButton#SecondaryBtn {{
        background-color: {p.surface0 if not is_light else p.mantle};
        color: {p.text};
        border: 1px solid {p.surface1};
        border-radius: 8px;
        padding: 10px 18px;
        font-weight: 500;
        font-size: 14px;
    }}
    QPushButton#SecondaryBtn:hover {{
        background-color: {p.surface1};
    }}
    QPushButton#SecondaryBtn:disabled {{ color: {p.overlay}; }}

    QPushButton#GhostBtn {{
        background-color: transparent;
        color: {p.subtext0};
        border: none;
        border-radius: 8px;
        padding: 8px 12px;
    }}
    QPushButton#GhostBtn:hover {{
        background-color: {p.surface0};
        color: {p.text};
    }}

    QPushButton#DangerBtn {{
        background-color: {p.red};
        color: #ffffff;
        border: none;
        border-radius: 8px;
        padding: 10px 18px;
        font-weight: 600;
    }}
    QPushButton#DangerBtn:hover {{ background-color: {p.maroon}; }}

    /* ---------- Tables ---------- */
    QTableView, QTableWidget {{
        background-color: {table_bg};
        alternate-background-color: {table_alt};
        gridline-color: {border};
        border: 1px solid {table_border};
        border-radius: 10px;
        selection-background-color: {p.surface1};
        selection-color: {p.text};
        font-size: 14px;
    }}
    QTableView::item, QTableWidget::item {{
        padding: 8px 6px;
        border: none;
    }}

    QHeaderView::section {{
        background-color: {p.surface0 if not is_light else p.mantle};
        color: {p.subtext0};
        padding: 10px 12px;
        border: none;
        border-bottom: 1px solid {border};
        font-weight: 700;
        font-size: 11px;
        letter-spacing: 1.5px;
    }}
    QHeaderView::section:first {{ border-top-left-radius: 10px; }}
    QHeaderView::section:last {{ border-top-right-radius: 10px; }}

    /* ---------- Scrollbars ---------- */
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 4px 0;
    }}
    QScrollBar::handle:vertical {{
        background: {p.surface1};
        min-height: 30px;
        border-radius: 5px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {p.surface2}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 10px;
        margin: 0 4px;
    }}
    QScrollBar::handle:horizontal {{
        background: {p.surface1};
        min-width: 30px;
        border-radius: 5px;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    /* ---------- Progress bar ---------- */
    QProgressBar {{
        background-color: {p.surface0};
        border: none;
        border-radius: 5px;
        height: 10px;
        text-align: center;
        color: {p.text};
        font-size: 11px;
    }}
    QProgressBar::chunk {{
        background-color: {p.blue};
        border-radius: 5px;
    }}

    /* ---------- Status bar ---------- */
    QStatusBar {{
        background-color: {p.crust};
        color: {p.subtext0};
        border-top: 1px solid {border};
        padding: 4px 12px;
        font-size: 13px;
    }}
    QStatusBar QLabel {{ background: transparent; }}

    /* ---------- Menu ---------- */
    QMenu {{
        background-color: {p.mantle};
        color: {p.text};
        border: 1px solid {p.surface1};
        border-radius: 8px;
        padding: 4px;
    }}
    QMenu::item {{
        padding: 7px 24px;
        border-radius: 6px;
    }}
    QMenu::item:selected {{
        background-color: {p.surface1};
    }}
    QMenu::separator {{
        height: 1px;
        background: {p.surface0};
        margin: 4px 8px;
    }}

    /* ---------- Tooltips ---------- */
    QToolTip {{
        background-color: {p.crust};
        color: {p.text};
        border: 1px solid {p.surface1};
        padding: 6px 10px;
        border-radius: 6px;
        font-size: 13px;
    }}

    /* ---------- Group box ----------
       IMPORTANT: do NOT set font-size / letter-spacing on QGroupBox itself;
       those properties are inherited by every child label and that's what
       was making form text look cramped. Style the title pseudo-element
       only. */
    QGroupBox {{
        background-color: {p.mantle if is_light else p.surface0};
        border: 1px solid {border};
        border-radius: 12px;
        padding: 36px 18px 18px 18px;
        margin-top: 8px;
    }}
    QGroupBox::title {{
        color: {p.subtext0};
        /* `subcontrol-origin: padding` places the title INSIDE the
           border, in the box's padding-top area. This avoids the
           classic Qt cosmetic bug where `::margin` positioning
           sliced through the border at the top-left corner. The
           36px padding-top on QGroupBox above already reserves room
           for this header. */
        subcontrol-origin: padding;
        subcontrol-position: top left;
        padding: 0;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 1.5px;
    }}

    /* Form labels inside group boxes: ensure they don't shrink. */
    QGroupBox QLabel {{
        font-size: 14px;
        font-weight: 500;
        letter-spacing: 0;
        color: {p.text};
    }}
    QGroupBox QLabel#FormHelp {{
        color: {p.overlay};
        font-size: 12px;
        font-weight: 400;
    }}

    /* ---------- Scroll area ---------- */
    QScrollArea {{
        background: transparent;
        border: none;
    }}
    QScrollArea > QWidget > QWidget {{
        background: transparent;
    }}
    QAbstractScrollArea {{
        background: transparent;
    }}
    """


def stylesheet_cockpit(p: Palette) -> str:
    """
    Cockpit QSS — instrument-panel aesthetic. Top tab bar, sharp corners,
    monospace big numbers, thick warm borders. All widget classes the
    studio QSS covers are also styled here so existing views render
    consistently.
    """
    accent = p.blue
    accent_dim = p.subtext0
    body = p.text
    panel = p.panel or p.surface0
    border = p.border_warm or p.overlay
    led_on = p.led_on or p.green
    led_off = p.led_off or "#444"
    bg = p.base
    mantle = p.mantle
    # Foreground for text/icons drawn on top of the accent colour.
    on_accent = p.fg_on_accent or (p.base if not p.is_light else "#ffffff")

    return f"""
    /* ---------- Reset ---------- */
    * {{ color: {body}; }}
    QWidget {{
        background: {bg};
        color: {body};
        font-family: "Segoe UI", system-ui;
        font-size: 13px;
    }}
    QMainWindow, QDialog {{ background-color: {bg}; }}

    /* ---------- Top tab bar ---------- */
    #TopBar {{
        background: {mantle};
        border: none;
        border-bottom: 2px solid {accent};
    }}
    #SidebarBrand {{
        color: {accent};
        font-family: "Cascadia Mono", "Consolas", monospace;
        font-size: 17px; font-weight: 700; letter-spacing: 5px;
        padding: 14px 22px;
    }}
    #SidebarSubtitle {{
        color: {accent_dim};
        font-family: "Cascadia Mono", "Consolas", monospace;
        font-size: 11px; letter-spacing: 3px;
        padding: 14px 22px 14px 0;
    }}

    QPushButton#NavButton {{
        background: transparent; color: {accent_dim};
        font-family: "Cascadia Mono", "Consolas", monospace; font-size: 11px;
        font-weight: 700; letter-spacing: 2px; border: none;
        padding: 10px 10px; min-width: 60px;
    }}
    QPushButton#NavButton:hover {{ color: {body}; background: {panel}; }}
    QPushButton#NavButton:checked {{
        color: {accent}; background: {panel};
        border-top: 2px solid {accent};
        border-bottom: 2px solid {bg};
    }}

    /* ---------- Content root ---------- */
    #ContentRoot {{ background-color: {bg}; }}

    /* ---------- Page heading ---------- */
    QLabel#PageHeading {{
        font-family: "Cascadia Mono", "Consolas", monospace;
        color: {body};
        font-size: 22px; font-weight: 700;
        letter-spacing: 3px;
    }}
    QLabel#PageSubheading {{
        font-family: "Cascadia Mono", "Consolas", monospace;
        font-size: 12px; letter-spacing: 2px;
        color: {accent_dim};
        padding-top: 6px;
    }}

    /* ---------- Cards / instrument panels ---------- */
    QFrame#Card {{
        background: {panel};
        border: 2px solid {border};
        border-radius: 0;
    }}
    QLabel#CardTitle {{
        color: {accent};
        font-family: "Cascadia Mono", "Consolas", monospace;
        font-size: 11px; font-weight: 700; letter-spacing: 3px;
    }}
    QLabel#CardValue {{
        color: {body}; font-size: 18px; font-weight: 600;
    }}
    QLabel#StatBig {{
        color: {accent};
        font-family: "Cascadia Mono", "Consolas", monospace;
        font-size: 38px; font-weight: 700;
    }}
    QLabel#StatLabel {{
        color: {accent}; font-family: "Cascadia Mono", "Consolas", monospace;
        font-size: 10px; font-weight: 700; letter-spacing: 3px;
    }}

    /* ---------- Inputs ---------- */
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: {panel}; color: {body};
        border: 1px solid {border}; border-radius: 0;
        padding: 7px 10px;
        font-family: "Cascadia Mono", "Consolas", monospace;
        font-size: 13px;
        selection-background-color: {accent}; selection-color: {bg};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 1px solid {accent};
    }}
    QLineEdit:disabled, QComboBox:disabled {{
        color: {accent_dim}; background: {mantle};
    }}
    QComboBox::drop-down {{ border: none; width: 20px; }}
    QComboBox QAbstractItemView {{
        background: {panel}; border: 1px solid {border};
        selection-background-color: {p.surface2};
        selection-color: {body}; outline: none; padding: 0;
        font-family: "Cascadia Mono", "Consolas", monospace;
    }}

    QCheckBox {{ spacing: 8px; font-family: "Cascadia Mono", "Consolas", monospace; }}
    QCheckBox::indicator {{
        width: 16px; height: 16px;
        border: 1.5px solid {border};
        border-radius: 0;
        background: {panel};
    }}
    QCheckBox::indicator:checked {{
        background: {accent}; border-color: {accent};
    }}

    /* ---------- Buttons ---------- */
    QPushButton#PrimaryBtn {{
        background: {accent}; color: {on_accent};
        border: 2px solid {accent}; border-radius: 0;
        padding: 9px 18px;
        font-family: "Cascadia Mono", "Consolas", monospace;
        font-weight: 700; font-size: 12px; letter-spacing: 2px;
    }}
    QPushButton#PrimaryBtn:hover {{ background: {body}; color: {on_accent}; }}
    QPushButton#PrimaryBtn:disabled {{
        background: {panel}; color: {accent_dim};
        border-color: {border};
    }}

    QPushButton#SecondaryBtn {{
        background: transparent; color: {body};
        border: 1px solid {border}; border-radius: 0;
        padding: 9px 16px;
        font-family: "Cascadia Mono", "Consolas", monospace;
        font-weight: 600; font-size: 12px; letter-spacing: 2px;
    }}
    QPushButton#SecondaryBtn:hover {{ background: {panel}; color: {accent}; border-color: {accent}; }}
    QPushButton#SecondaryBtn:disabled {{ color: {accent_dim}; }}

    QPushButton#GhostBtn {{
        background: transparent; color: {accent_dim};
        border: none; border-radius: 0; padding: 8px 12px;
        font-family: "Cascadia Mono", "Consolas", monospace;
    }}
    QPushButton#GhostBtn:hover {{ color: {body}; background: {panel}; }}

    QPushButton#DangerBtn {{
        background: {p.red}; color: #ffffff;
        border: none; border-radius: 0; padding: 9px 18px;
        font-family: "Cascadia Mono", "Consolas", monospace;
        font-weight: 700; letter-spacing: 2px;
    }}
    QPushButton#DangerBtn:hover {{ background: {p.maroon}; }}

    /* ---------- Tables ---------- */
    QTableView, QTableWidget {{
        background: {panel};
        alternate-background-color: {p.surface1};
        gridline-color: {border};
        border: 2px solid {border}; border-radius: 0;
        selection-background-color: {p.surface2};
        selection-color: {body};
        font-family: "Cascadia Mono", "Consolas", monospace;
        font-size: 13px;
    }}
    QTableView::item, QTableWidget::item {{ padding: 6px 8px; border: none; }}
    QHeaderView::section {{
        background: {mantle}; color: {accent};
        padding: 10px 12px; border: none;
        border-bottom: 1px solid {border};
        font-family: "Cascadia Mono", "Consolas", monospace;
        font-weight: 700; font-size: 10px; letter-spacing: 3px;
    }}

    /* ---------- Scrollbars ---------- */
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 4px 0; }}
    QScrollBar::handle:vertical {{ background: {border}; min-height: 30px; border-radius: 0; }}
    QScrollBar::handle:vertical:hover {{ background: {accent}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0 4px; }}
    QScrollBar::handle:horizontal {{ background: {border}; min-width: 30px; border-radius: 0; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    /* ---------- Progress bar ---------- */
    QProgressBar {{
        background: {panel}; border: 1px solid {border}; border-radius: 0;
        height: 8px; text-align: center; color: {body};
        font-family: "Cascadia Mono", "Consolas", monospace; font-size: 10px;
    }}
    QProgressBar::chunk {{ background: {accent}; }}

    /* ---------- Status bar ---------- */
    QStatusBar {{
        background: {p.crust}; color: {accent_dim};
        border-top: 1px solid {border};
        font-family: "Cascadia Mono", "Consolas", monospace;
        font-size: 12px; padding: 4px 12px;
    }}
    QStatusBar QLabel {{ background: transparent; }}

    /* ---------- Menu / tooltip ---------- */
    QMenu {{ background: {panel}; color: {body};
             border: 1px solid {border}; border-radius: 0; padding: 0;
             font-family: "Cascadia Mono", "Consolas", monospace; }}
    QMenu::item {{ padding: 8px 24px; border-radius: 0; }}
    QMenu::item:selected {{ background: {p.surface2}; color: {accent}; }}
    QMenu::separator {{ height: 1px; background: {border}; margin: 2px 8px; }}
    QToolTip {{
        background: {p.crust}; color: {body};
        border: 1px solid {border}; padding: 6px 10px; border-radius: 0;
        font-family: "Cascadia Mono", "Consolas", monospace; font-size: 12px;
    }}

    /* ---------- Group box (instrument cluster) ---------- */
    QGroupBox {{
        background: {panel};
        border: 2px solid {border}; border-radius: 0;
        padding: 36px 18px 18px 18px; margin-top: 8px;
    }}
    QGroupBox::title {{
        color: {accent};
        /* See modern-theme comment: position title inside padding so
           the 2-px border draws cleanly around the corner instead of
           being clipped under the title. */
        subcontrol-origin: padding;
        subcontrol-position: top left;
        padding: 0;
        font-family: "Cascadia Mono", "Consolas", monospace;
        font-size: 11px; font-weight: 700; letter-spacing: 3px;
    }}
    QGroupBox QLabel {{
        font-family: "Segoe UI", system-ui;
        font-size: 14px; font-weight: 500; color: {body};
        letter-spacing: 0;
    }}

    /* ---------- Scroll area ---------- */
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QAbstractScrollArea {{ background: transparent; }}
    """


def qpalette_for(p: Palette) -> QPalette:
    """Build a Qt QPalette from a Palette so native widgets pick up theme."""
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(p.base))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(p.text))
    pal.setColor(QPalette.ColorRole.Base, QColor(p.mantle))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(p.base))
    pal.setColor(QPalette.ColorRole.Text, QColor(p.text))
    pal.setColor(QPalette.ColorRole.Button, QColor(p.surface0))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(p.text))
    pal.setColor(QPalette.ColorRole.BrightText, QColor(p.text))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(p.surface1))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(p.text))
    pal.setColor(QPalette.ColorRole.Link, QColor(p.blue))
    pal.setColor(QPalette.ColorRole.LinkVisited, QColor(p.mauve))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(p.crust))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(p.text))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(p.overlay))
    return pal


# ============================================================ #
#  ThemeManager: System / Light / Dark with auto-detect        #
# ============================================================ #


class ThemeManager(QObject):
    """
    Owns the active theme. Three modes:
      - SYSTEM: follows the OS color scheme; updates automatically when it
        changes (Qt 6.5+ via QStyleHints.colorSchemeChanged).
      - LIGHT / DARK: explicit, ignores the OS.
    The chosen mode is persisted via QSettings under "theme/mode".
    """

    paletteChanged = Signal()  # emitted whenever the resolved palette flips

    SETTINGS_GROUP = "theme"
    SETTINGS_KEY = "mode"
    SETTINGS_NAMED_KEY = "named_theme"
    SETTINGS_FAMILY_KEY = "family"

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._mode = self._load_mode()
        self._family: str = self._load_family()
        self._named: str | None = self._compute_named_from_family_mode()
        # Listen to OS theme changes (Qt 6.5+).
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(self._on_system_changed)

    # ---- Family API -------------------------------------------------------

    @property
    def family(self) -> str:
        return self._family

    def set_family(self, family: str) -> None:
        if family not in THEME_FAMILIES:
            return
        if family == self._family:
            return
        self._family = family
        self._save_family(family)
        self._refresh_named_from_family_mode()

    # ---- public API -------------------------------------------------------

    @property
    def mode(self) -> ThemeMode:
        return self._mode

    @property
    def palette(self) -> Palette:
        return self._resolve(self._mode)

    def set_mode(self, mode: ThemeMode) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        self._save_mode(mode)
        # Recompute the active palette using the new mode + current family.
        self._refresh_named_from_family_mode()

    @property
    def named(self) -> str | None:
        """Active named theme override, or None if using mode-driven palette."""
        return self._named

    def set_named(self, theme_name: str | None) -> None:
        """Pick a specific named theme from NAMED_THEMES (or None to clear)."""
        if theme_name is not None and theme_name not in NAMED_THEMES:
            raise ValueError(f"unknown theme: {theme_name}")
        if theme_name == self._named:
            return
        self._named = theme_name
        self._save_named(theme_name)
        self.paletteChanged.emit()

    def apply(self, app: QApplication) -> None:
        """Apply the current palette/stylesheet to the QApplication."""
        p = self.palette
        app.setPalette(qpalette_for(p))
        app.setStyleSheet(stylesheet(p))

    # ---- internals --------------------------------------------------------

    def _resolve(self, mode: ThemeMode) -> Palette:
        # Named theme override wins.
        if self._named is not None and self._named in NAMED_THEMES:
            return NAMED_THEMES[self._named]
        if mode == ThemeMode.LIGHT:
            return LIGHT
        if mode == ThemeMode.DARK:
            return DARK
        # SYSTEM: ask Qt for the OS color scheme.
        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Light:
            return LIGHT
        # Treat Unknown as Dark (sensible default for our radio app).
        return DARK

    def _on_system_changed(self, _scheme: Qt.ColorScheme) -> None:
        if self._mode == ThemeMode.SYSTEM:
            self._refresh_named_from_family_mode()

    def _mode_str(self) -> str:
        return {
            ThemeMode.SYSTEM: "auto",
            ThemeMode.LIGHT:  "light",
            ThemeMode.DARK:   "dark",
        }.get(self._mode, "dark")

    def _compute_named_from_family_mode(self) -> str:
        return named_for_family(self._family, self._mode_str())

    def _refresh_named_from_family_mode(self) -> None:
        new_named = self._compute_named_from_family_mode()
        if new_named != self._named:
            self._named = new_named
            self._save_named(new_named)
        self.paletteChanged.emit()

    def _load_family(self) -> str:
        s = QSettings()
        s.beginGroup(self.SETTINGS_GROUP)
        try:
            v = s.value(self.SETTINGS_FAMILY_KEY, "")
        finally:
            s.endGroup()
        v = (v or "").strip()
        if v in THEME_FAMILIES:
            return v
        # Migrate from legacy "named" setting.
        legacy = self._load_named()
        if legacy:
            for fam_id, fam in THEME_FAMILIES.items():
                if legacy in (fam.get("dark"), fam.get("light")):
                    return fam_id
        return "cockpit-amber"

    def _save_family(self, family: str) -> None:
        s = QSettings()
        s.beginGroup(self.SETTINGS_GROUP)
        s.setValue(self.SETTINGS_FAMILY_KEY, family)
        s.endGroup()

    def _load_mode(self) -> ThemeMode:
        s = QSettings()
        s.beginGroup(self.SETTINGS_GROUP)
        raw = s.value(self.SETTINGS_KEY, ThemeMode.SYSTEM.value)
        s.endGroup()
        try:
            return ThemeMode(raw)
        except ValueError:
            return ThemeMode.SYSTEM

    def _save_mode(self, mode: ThemeMode) -> None:
        s = QSettings()
        s.beginGroup(self.SETTINGS_GROUP)
        s.setValue(self.SETTINGS_KEY, mode.value)
        s.endGroup()

    def _load_named(self) -> str | None:
        s = QSettings()
        s.beginGroup(self.SETTINGS_GROUP)
        try:
            # Default new installs to the Cockpit · Amber preset.
            v = s.value(self.SETTINGS_NAMED_KEY, "cockpit-amber")
        finally:
            s.endGroup()
        v = (v or "").strip()
        return v if v in NAMED_THEMES else "cockpit-amber"

    def _save_named(self, name: str | None) -> None:
        s = QSettings()
        s.beginGroup(self.SETTINGS_GROUP)
        s.setValue(self.SETTINGS_NAMED_KEY, name or "")
        s.endGroup()
