"""界面基础件与主题装配。

- `ThemedIconButton`：图标按钮，订阅主题变化信号自动重新染色（切主题不用遍历控件树）。
- `DarkComboBox`：类名沿用历史，现在只是"自绘箭头的下拉框"，下拉三角用 chevron-down 图标画。
- `ElideLabel`：会自动省略号的 QLabel。QLabel 默认的 minimumSizeHint 等于全文宽度，
  这是列宽压不下去的隐形元凶，所以横向 sizePolicy 必须是 Ignored。
- `StatusDot`：paintEvent 画圆点（以前用 setStyleSheet 逐实例刷，切主题不会更新）。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from . import theme
from .icons import ICON_SIZE, icons
from .paths import resources_dir  # noqa: F401  (兼容旧的导入点)

# QComboBox 的文字区 = 宽 - COMBO_CHROME（2 border + 6 左 pad + 4 右 pad + 18 箭头）。
# 改 theme.qss.tmpl 里的 combo padding 时必须同步改这里，否则算出来的最小宽会错。
COMBO_CHROME = 30

__all__ = [
    "COMBO_CHROME",
    "width_for_text",
    "ToolTextButton",
    "apply_theme",
    "make_app_icon",
    "DarkComboBox",
    "StatusDot",
    "ThemedIconButton",
    "ThemedTextButton",
    "ElideLabel",
    "resources_dir",
]


# --------------------------------------------------------------------------- 主题

def width_for_text(widget: QWidget, text: str, chrome: int = COMBO_CHROME) -> int:
    """反推"要容纳这段文字，控件至少该多宽"。比 sizeHint 准，也不受 SizeAdjustPolicy 干扰。"""
    return widget.fontMetrics().horizontalAdvance(text) + chrome + 4


class ToolTextButton(QPushButton):
    """工具条里的文字按钮（Abc / 1E）。宽度按自身文字算，不做魔法数字。"""

    def __init__(self, text: str, tip: str = "", checkable: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("toolText")
        self.setCheckable(checkable)
        self.setToolTip(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)
        self.setFixedWidth(width_for_text(self, text, chrome=12) + 4)


def make_app_icon() -> QIcon:
    """运行时画图标：主色圆角底 + 三根白色竖条（对应三列布局）。"""
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(theme.color("accent")))
    p.drawRoundedRect(QRectF(3, 3, 58, 58), 13, 13)
    p.setBrush(QColor("#ffffff"))
    for i, h in enumerate((26, 17, 32)):
        p.drawRoundedRect(QRectF(15 + i * 12, 47 - h, 8, h), 2.5, 2.5)
    p.end()
    return QIcon(pm)


def apply_theme(app: QApplication, name: str | None = None) -> None:
    theme.apply(app, name)
    app.setWindowIcon(make_app_icon())


# --------------------------------------------------------------------------- 图标按钮

ROLE_COLORS = ("normal", "accent", "danger", "muted")


class ThemedIconButton(QToolButton):
    def __init__(
        self,
        icon_name: str,
        tip: str = "",
        role: str = "normal",
        size: int = ICON_SIZE,
        checkable: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon_name = icon_name
        self._role = role
        self._size = size
        self.setToolTip(tip)
        self.setCheckable(checkable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(size, size))
        self.setFixedSize(size + 6, size + 6)
        theme.bus.changed.connect(self._retheme)
        self._retheme()

    def set_icon_name(self, name: str) -> None:
        self._icon_name = name
        self._retheme()

    def _retheme(self, *_args) -> None:
        t = theme.tokens()
        roles = {
            "normal": t["text.dim"],
            "accent": t["accent"],
            "danger": t["danger"],
            "muted": t["text.faint"],
        }
        base = roles.get(self._role, t["text.dim"])
        on = t["accent"] if self._role == "normal" else base
        self.setIcon(icons.stateful(self._icon_name, base, on, t["text.faint"], self._size))


# --------------------------------------------------------------------------- 下拉框

class ThemedTextButton(QPushButton):
    """带图标的文字按钮（顶栏用）。主题切换时图标跟着换色，文字靠 QSS。"""

    def __init__(
        self,
        text: str,
        icon_name: str = "",
        tip: str = "",
        primary: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self._icon_name = icon_name
        self._primary = primary
        self.setObjectName("primary" if primary else "ghost")
        self.setToolTip(tip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(30)
        self.setIconSize(QSize(16, 16))
        theme.bus.changed.connect(self._retheme)
        self._retheme()

    def _retheme(self, *_args) -> None:
        if not self._icon_name:
            return
        t = theme.tokens()
        color = t["text.on-accent"] if self._primary else t["text.dim"]
        self.setIcon(icons.icon(self._icon_name, color, t["text.faint"], 16))


class DarkComboBox(QComboBox):
    """自绘下拉箭头。别名的历史原因见模块 docstring。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMaxVisibleItems(16)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(6)
        self.setMinimumWidth(0)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if self.count() == 0 and not self.isEditable():
            return
        pm = icons.pixmap("chevron-down", theme.color("text.dim" if self.isEnabled() else "text.faint"), 14)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        x = self.width() - 18.0
        y = (self.height() - 14) / 2.0
        p.drawPixmap(QRectF(x, y, 14, 14), pm, QRectF(0, 0, pm.width(), pm.height()))
        p.end()


# --------------------------------------------------------------------------- 其它

class StatusDot(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(8, 8)
        self._on = False

    def setOn(self, on: bool) -> None:  # noqa: N802
        if self._on != on:
            self._on = on
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.color("ok" if self._on else "dot.off")))
        p.drawEllipse(QRectF(1, 1, 6, 6))
        p.end()


class ElideLabel(QLabel):
    """自动省略的标签：文本超宽时中间/尾部省略，完整内容进 tooltip。"""

    def __init__(
        self,
        text: str = "",
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._full = text
        self._mode = mode
        self._syncing = False
        # Preferred（不是 Ignored）：Ignored 会让它永远分不到宽度，列头的 RX/TX 就是这么消失的；
        # 配合下面 minimumSizeHint 返回 0，才能"既要得到宽度、又能被压扁到省略"。
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        super().setText(text)
        if text:
            self.setToolTip(text)

    def full_text(self) -> str:
        return self._full

    def set_full_text(self, text: str) -> None:
        if text == self._full:
            return
        self._full = text
        self.setToolTip(text)
        self._sync()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(0, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:  # noqa: N802
        # 必须按"全文"算，不能按当前显示的省略文本算 —— 否则宽度先被压到 0 时
        # 文本会被设成空串，sizeHint 从此永远是 0，布局再也不给宽度（自锁）
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self._full) + 4, super().sizeHint().height())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync()

    def _sync(self) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            avail = max(0, self.width() - 2)
            if avail <= 0:
                return                     # 还没被布局分配宽度，别把文本清空
            shown = self.fontMetrics().elidedText(self._full, self._mode, avail)
            if shown != super().text():
                super().setText(shown)
        finally:
            self._syncing = False
