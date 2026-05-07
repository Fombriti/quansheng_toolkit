"""Entrypoint used by PyInstaller --onefile builds.

Lives at the repo root so PyInstaller treats it as a plain script and
imports the GUI through its absolute path. The standard
`gui/__main__.py` entrypoint keeps using a relative import for the
`python -m quansheng_toolkit.gui` workflow — that one works because
the package context is already present.
"""
from __future__ import annotations

import sys

from quansheng_toolkit.gui.app import run


if __name__ == "__main__":
    sys.exit(run())
