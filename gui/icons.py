"""
Inline SVG icon set + helper to render them as themable QIcon objects.

Style: Lucide-inspired (24×24 viewBox, stroke-only, 2px stroke, rounded
linecaps/joins). Each icon's stroke colour is parameterised via a
{color} placeholder so the renderer can recolour at runtime.

Why inline strings? Single-file deployment friendliness — we don't have
to ship .svg files separately, the icons travel with the Python code.
"""
from __future__ import annotations

from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


# ---------------------------------------------------------------- #
# Raw SVG strings. Stroke is `currentColor` will be substituted.   #
# ---------------------------------------------------------------- #

_SVG = {
    "dashboard": """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
             stroke="{color}" stroke-width="2" stroke-linecap="round"
             stroke-linejoin="round">
          <rect x="3" y="3"  width="7" height="9"  rx="1.5"/>
          <rect x="14" y="3" width="7" height="5"  rx="1.5"/>
          <rect x="14" y="12" width="7" height="9" rx="1.5"/>
          <rect x="3" y="16" width="7" height="5"  rx="1.5"/>
        </svg>""",

    "channels": """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
             stroke="{color}" stroke-width="2" stroke-linecap="round"
             stroke-linejoin="round">
          <line x1="8"  y1="6"  x2="21" y2="6"/>
          <line x1="8"  y1="12" x2="21" y2="12"/>
          <line x1="8"  y1="18" x2="21" y2="18"/>
          <circle cx="3.8" cy="6"  r="1.4"/>
          <circle cx="3.8" cy="12" r="1.4"/>
          <circle cx="3.8" cy="18" r="1.4"/>
        </svg>""",

    "layers": """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
             stroke="{color}" stroke-width="2" stroke-linecap="round"
             stroke-linejoin="round">
          <polygon points="12 2 2 7 12 12 22 7 12 2"/>
          <polyline points="2 17 12 22 22 17"/>
          <polyline points="2 12 12 17 22 12"/>
        </svg>""",

    "settings": """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
             stroke="{color}" stroke-width="2" stroke-linecap="round"
             stroke-linejoin="round">
          <line x1="4"  y1="6"  x2="11" y2="6"/>
          <line x1="15" y1="6"  x2="20" y2="6"/>
          <line x1="4"  y1="12" x2="7"  y2="12"/>
          <line x1="11" y1="12" x2="20" y2="12"/>
          <line x1="4"  y1="18" x2="14" y2="18"/>
          <line x1="18" y1="18" x2="20" y2="18"/>
          <circle cx="13" cy="6"  r="2"/>
          <circle cx="9"  cy="12" r="2"/>
          <circle cx="16" cy="18" r="2"/>
        </svg>""",

    "refresh": """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
             stroke="{color}" stroke-width="2" stroke-linecap="round"
             stroke-linejoin="round">
          <polyline points="23 4 23 10 17 10"/>
          <polyline points="1 20 1 14 7 14"/>
          <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10"/>
          <path d="M20.49 15a9 9 0 0 1-14.85 3.36L1 14"/>
        </svg>""",

    "folder-open": """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
             stroke="{color}" stroke-width="2" stroke-linecap="round"
             stroke-linejoin="round">
          <path d="M6 14l-3 6h18l-3-6"/>
          <path d="M3 14V5a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v7"/>
        </svg>""",

    "save": """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
             stroke="{color}" stroke-width="2" stroke-linecap="round"
             stroke-linejoin="round">
          <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>
          <polyline points="17 21 17 13 7 13 7 21"/>
          <polyline points="7 3 7 8 15 8"/>
        </svg>""",

    "send": """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
             stroke="{color}" stroke-width="2" stroke-linecap="round"
             stroke-linejoin="round">
          <line x1="22" y1="2" x2="11" y2="13"/>
          <polygon points="22 2 15 22 11 13 2 9 22 2"/>
        </svg>""",

    "download": """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
             stroke="{color}" stroke-width="2" stroke-linecap="round"
             stroke-linejoin="round">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
          <polyline points="7 10 12 15 17 10"/>
          <line x1="12" y1="15" x2="12" y2="3"/>
        </svg>""",

    "monitor": """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
             stroke="{color}" stroke-width="2" stroke-linecap="round"
             stroke-linejoin="round">
          <rect x="2" y="4" width="20" height="13" rx="2"/>
          <line x1="8"  y1="21" x2="16" y2="21"/>
          <line x1="12" y1="17" x2="12" y2="21"/>
        </svg>""",

    "sun": """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
             stroke="{color}" stroke-width="2" stroke-linecap="round"
             stroke-linejoin="round">
          <circle cx="12" cy="12" r="4"/>
          <line x1="12" y1="2"  x2="12" y2="4"/>
          <line x1="12" y1="20" x2="12" y2="22"/>
          <line x1="2"  y1="12" x2="4"  y2="12"/>
          <line x1="20" y1="12" x2="22" y2="12"/>
          <line x1="4.93" y1="4.93" x2="6.34" y2="6.34"/>
          <line x1="17.66" y1="17.66" x2="19.07" y2="19.07"/>
          <line x1="4.93" y1="19.07" x2="6.34" y2="17.66"/>
          <line x1="17.66" y1="6.34"  x2="19.07" y2="4.93"/>
        </svg>""",

    "moon": """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
             stroke="{color}" stroke-width="2" stroke-linecap="round"
             stroke-linejoin="round">
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
        </svg>""",

    "radio": """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
             stroke="{color}" stroke-width="2" stroke-linecap="round"
             stroke-linejoin="round">
          <path d="M4.93 19.07a10 10 0 0 1 0-14.14"/>
          <path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>
          <path d="M7.76 16.24a6 6 0 0 1 0-8.49"/>
          <path d="M16.24 7.76a6 6 0 0 1 0 8.49"/>
          <circle cx="12" cy="12" r="2"/>
        </svg>""",

    "info": """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
             stroke="{color}" stroke-width="2" stroke-linecap="round"
             stroke-linejoin="round">
          <circle cx="12" cy="12" r="10"/>
          <line x1="12" y1="11" x2="12" y2="16"/>
          <circle cx="12" cy="8" r="0.5" stroke-width="2.5"/>
        </svg>""",

    "alert": """
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none"
             stroke="{color}" stroke-width="2" stroke-linecap="round"
             stroke-linejoin="round">
          <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
          <line x1="12" y1="9"  x2="12" y2="13"/>
          <line x1="12" y1="17" x2="12.01" y2="17"/>
        </svg>""",
}


# ---------------------------------------------------------------- #
# Public API                                                       #
# ---------------------------------------------------------------- #

def svg_icon(name: str, color: str, size: int = 20) -> QIcon:
    """Render an inline SVG glyph into a QIcon, tinted to `color`."""
    template = _SVG.get(name)
    if template is None:
        return QIcon()
    svg = template.format(color=color)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pixmap = QPixmap(QSize(size, size))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    renderer.render(painter)
    painter.end()
    return QIcon(pixmap)


def has_icon(name: str) -> bool:
    return name in _SVG


def all_icon_names() -> list[str]:
    return list(_SVG.keys())
