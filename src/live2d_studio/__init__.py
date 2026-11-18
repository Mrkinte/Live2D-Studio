"""Live2D Studio: a Part visibility and motion tooling editor."""

import sys
from pathlib import Path

__version__ = "1.0.0"
_ICON_NAME = "live2d-studio.ico"


def _locate_icon() -> Path:
    local = Path(__file__).resolve().parent / "assets" / _ICON_NAME
    if local.is_file():
        return local

    # 打包器会把数据放在 <dist>/live2d_studio/assets，模块路径在冻结环境里不可靠
    roots = [Path(sys.executable).resolve().parent]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.insert(0, Path(meipass))
    for root in roots:
        candidate = root / "live2d_studio" / "assets" / _ICON_NAME
        if candidate.is_file():
            return candidate
    return local


ICON_PATH = _locate_icon()
