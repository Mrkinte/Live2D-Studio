"""PyInstaller entry point (equivalent to ``python -m live2d_studio``)."""

from __future__ import annotations

import os
import sys

# Windowed builds have no console; keep live2d-py's banner and Qt warnings
# from crashing on a missing stdout/stderr.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

from live2d_studio.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
