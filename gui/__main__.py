"""Entry point: `python -m quansheng_toolkit.gui`."""
from __future__ import annotations

import sys

from .app import run


if __name__ == "__main__":
    sys.exit(run())
