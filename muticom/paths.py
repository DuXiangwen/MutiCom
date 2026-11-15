"""资源路径。开发态和 PyInstaller 打包态的唯一差别就在这里。"""

from __future__ import annotations

import sys
from pathlib import Path


def resources_dir() -> Path:
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) if base else Path(__file__).parent


def icon_dir() -> Path:
    return resources_dir() / "icons"
