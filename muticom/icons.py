"""SVG 图标：单色授权 + 运行时染色 + 缓存。

图标作者只画黑色（`stroke="#000"`），上色在运行时做：
`QSvgRenderer` 渲染出带 alpha 的黑色形状 → `CompositionMode_SourceIn` 把不透明像素整体替换成目标色。
SourceIn 会保留抗锯齿边缘的 alpha，所以不会有毛边，也不需要给每个颜色画一份 SVG。

Must：`QIcon` 必须自己填 `Mode.Disabled`。不填的话 Qt 会把 normal 图做 50% 混合，
在浅色主题下变成一坨糊掉的灰。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from .paths import icon_dir

ICON_SIZE = 24
ICON_SIZE_LARGE = 32
SUPERSAMPLE = 2   # 位图按 2 倍渲染，交给 QIcon 缩放


class IconSet:
    def __init__(self, folder: Path) -> None:
        self._dir = folder
        self._renderers: dict[str, QSvgRenderer] = {}
        self._pixmaps: dict[tuple, QPixmap] = {}
        self._icons: dict[tuple, QIcon] = {}

    # ------------------------------------------------------------------ 基础
    def _renderer(self, name: str) -> QSvgRenderer:
        r = self._renderers.get(name)
        if r is None:
            r = QSvgRenderer(str(self._dir / f"{name}.svg"))
            self._renderers[name] = r
        return r

    def available(self, name: str) -> bool:
        return (self._dir / f"{name}.svg").exists()

    def pixmap(self, name: str, color: str, size: int = ICON_SIZE) -> QPixmap:
        """返回 **2 倍尺寸、不带 devicePixelRatio** 的位图。

        为什么不设 DPR：QIcon 拿到带 DPR 的 pixmap 后，在 QToolButton 上会按"设备尺寸"取图，
        结果只画出左上四分之一（图标看起来像被裁掉一半）。改成 2 倍超采样 + 让 QIcon 自己缩放：
        1x 屏下 48→24 平滑降采样，2x 屏下正好 1:1，两边都清晰。
        手动 drawPixmap 的地方记得给目标矩形，否则会按 2 倍画大。
        """
        key = (name, color, size)
        cached = self._pixmaps.get(key)
        if cached is not None:
            return cached

        px = max(1, size * SUPERSAMPLE)
        pm = QPixmap(px, px)
        pm.fill(Qt.GlobalColor.transparent)

        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        renderer = self._renderer(name)
        if renderer.isValid():
            renderer.render(p, QRectF(0, 0, px, px))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        p.fillRect(QRectF(0, 0, px, px), QColor(color))
        p.end()

        self._pixmaps[key] = pm
        return pm

    def icon(self, name: str, color: str, disabled: str, size: int = ICON_SIZE) -> QIcon:
        key = (name, color, disabled, size)
        cached = self._icons.get(key)
        if cached is not None:
            return cached
        ic = QIcon()
        normal = self.pixmap(name, color, size)
        ic.addPixmap(normal, QIcon.Mode.Normal)
        ic.addPixmap(self.pixmap(name, _active(color), size), QIcon.Mode.Active)
        ic.addPixmap(self.pixmap(name, disabled, size), QIcon.Mode.Disabled)
        self._icons[key] = ic
        return ic

    def stateful(
        self,
        name: str,
        normal: str,
        on: str,
        disabled: str,
        size: int = ICON_SIZE,
    ) -> QIcon:
        """带选中态的图标：checkable 按钮按下时 QAbstractButton 会向 QIcon 要 State::On。"""
        key = (name, normal, on, disabled, size, "stateful")
        cached = self._icons.get(key)
        if cached is not None:
            return cached
        ic = QIcon()
        ic.addPixmap(self.pixmap(name, normal, size), QIcon.Mode.Normal, QIcon.State.Off)
        ic.addPixmap(self.pixmap(name, _active(normal), size), QIcon.Mode.Active, QIcon.State.Off)
        ic.addPixmap(self.pixmap(name, on, size), QIcon.Mode.Normal, QIcon.State.On)
        ic.addPixmap(self.pixmap(name, _active(on), size), QIcon.Mode.Active, QIcon.State.On)
        ic.addPixmap(self.pixmap(name, disabled, size), QIcon.Mode.Disabled, QIcon.State.Off)
        ic.addPixmap(self.pixmap(name, disabled, size), QIcon.Mode.Disabled, QIcon.State.On)
        self._icons[key] = ic
        return ic


def _active(color: str) -> str:
    """hover 时让图标再深一点（浅色主题下压暗，深色主题下提亮）。"""
    c = QColor(color)
    if c.lightness() < 128:
        return c.darker(125).name()
    return c.lighter(125).name()


icons = IconSet(icon_dir())

__all__ = ["icons", "ICON_SIZE", "ICON_SIZE_LARGE", "QSize"]
