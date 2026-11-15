"""横向排列的会话列区域。

列宽策略：**等宽自适应**。容器最小宽只钉在硬下限（168px × 列数）上，而不是钉在"上次拖出来的宽度"上
—— 后者正是以前 3 列就撑出横向滚动条的根因。窗口够宽时 QSplitter 会把每列等分撑满；
窗口窄到装不下 168px×n 才出现滚动条（物理上没别的办法）。
用户拖动分隔条会退出等宽模式并记住自己的宽度；双击分隔条恢复等宽。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QSplitter,
    QWidget,
)

from .app_config import ColumnConfig
from .colorizer import RuleEngine
from .session_column import SessionColumn

MARGIN = 12          # 容器左右边距
HANDLE = 8           # 列间距（handle 透明，视觉上就是一道沟）
PREF_MIN = 200       # 舒适下限：低于它子面板进入紧凑档
HARD_MIN = 168       # 硬下限：低于它才允许横向滚动


class ColumnArea(QScrollArea):
    columnAdded = Signal(str)
    columnRemoved = Signal(str)
    columnChanged = Signal(str)          # 某列配置变了，外层安排存盘
    equalModeChanged = Signal(bool)
    saveLogRequested = Signal(str)
    managePresetsRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._columns: list[SessionColumn] = []
        self._last_ports: list = []
        self._pending_autopick: list[str] = []
        self._engine = RuleEngine()
        self._equal = True

        self._container = QWidget()
        self._container.setObjectName("columnsContainer")
        lay = QHBoxLayout(self._container)
        lay.setContentsMargins(MARGIN, 12, MARGIN, 12)
        lay.setSpacing(0)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setHandleWidth(HANDLE)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setOpaqueResize(True)

        lay.addWidget(self._splitter, 1)
        self.setWidget(self._container)

        self._user_dragging = False
        self._splitter.splitterMoved.connect(self._on_splitter_moved)

    # ------------------------------------------------------------------ 列管理
    @property
    def columns(self) -> list[SessionColumn]:
        return list(self._columns)

    @property
    def equal_mode(self) -> bool:
        return self._equal

    def set_equal_mode(self, on: bool, silent: bool = False) -> None:
        if self._equal == on:
            return
        self._equal = on
        if on:
            self._relayout()
        if not silent:
            self.equalModeChanged.emit(on)

    def set_engine(self, engine: RuleEngine) -> None:
        self._engine = engine

    def add_column(self, cfg: ColumnConfig | None = None) -> SessionColumn:
        col = SessionColumn(cfg or ColumnConfig(), self._engine, self._splitter)
        col.closeRequested.connect(self.remove_column)
        col.changed.connect(self.columnChanged)
        col.saveLogRequested.connect(self.saveLogRequested)
        col.managePresetsRequested.connect(self.managePresetsRequested)
        col.stateChanged.connect(self._on_state_changed)

        self._splitter.addWidget(col)
        self._splitter.setStretchFactor(self._splitter.count() - 1, 1)
        self._columns.append(col)
        self._install_handles()

        self._relabel()
        self._refresh_all()
        if cfg is None or not cfg.params.port:
            self._pending_autopick.append(col.cfg.uid)
            self._apply_autopicks()
        self._relayout()
        self.columnAdded.emit(col.cfg.uid)
        QTimer.singleShot(0, self._scroll_to_end)
        return col

    def _install_handles(self) -> None:
        for i in range(1, self._splitter.count()):
            handle = self._splitter.handle(i)
            if handle is not None:
                handle.installEventFilter(self)
                handle.setToolTip("拖动调整列宽，双击恢复等宽")

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.MouseButtonDblClick:
            self.set_equal_mode(True)
            return True
        return super().eventFilter(obj, event)

    def _scroll_to_end(self) -> None:
        bar = self.horizontalScrollBar()
        bar.setValue(bar.maximum())

    def remove_column(self, uid: str) -> None:
        if len(self._columns) <= 1:
            return
        for idx, col in enumerate(self._columns):
            if col.cfg.uid == uid:
                self._columns.pop(idx)
                col.shutdown()
                col.setParent(None)
                col.deleteLater()
                self._relabel()
                self._refresh_all()
                self._relayout()
                self.columnRemoved.emit(uid)
                return

    def clear(self) -> None:
        for col in list(self._columns):
            col.shutdown()
            col.setParent(None)
            col.deleteLater()
        self._columns.clear()

    def _relabel(self) -> None:
        only = len(self._columns) == 1
        for i, col in enumerate(self._columns, start=1):
            col.set_index(i)
            col.btn_close.setEnabled(not only)
            col.btn_close.setToolTip("至少保留一列" if only else "关闭这一列")

    def _on_state_changed(self, uid: str, _connected: bool) -> None:
        self._refresh_all()
        self.columnChanged.emit(uid)

    # ------------------------------------------------------------------ 宽度
    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        n = len(self._columns)
        if n == 0:
            self._container.setMinimumWidth(0)
            return
        # 只钉硬下限 —— 这是"显示全"的关键
        self._container.setMinimumWidth(HARD_MIN * n + HANDLE * (n - 1) + 2 * MARGIN)
        if self._equal:
            self._splitter.setSizes([1] * n)

    def _on_splitter_moved(self, _pos: int, _index: int) -> None:
        # 窗口自身缩放也会触发 splitterMoved，只有鼠标按着拖才算用户改列宽
        if QApplication.mouseButtons() & Qt.MouseButton.LeftButton and self._equal:
            self._equal = False
            self.equalModeChanged.emit(False)

    def sizes(self) -> list[int]:
        return list(self._splitter.sizes())

    def restore_sizes(self, sizes: list[int], equal: bool = True) -> None:
        self._equal = equal
        if sizes and not equal:
            self._splitter.setSizes([int(s) for s in sizes[: len(self._columns)]])
        self._relayout()

    # ------------------------------------------------------------------ 串口/占用
    def _refresh_all(self) -> None:
        occupied: dict[str, tuple[str, str]] = {}
        for col in self._columns:
            if col.is_connected and col.port:
                occupied[col.port] = (col.cfg.uid, col.display_name)
        for col in self._columns:
            col.set_ports(self._last_ports, occupied)

    def _apply_autopicks(self) -> None:
        """给「端口为空」的新列挑一个别的列还没用的口。

        程序启动时列是在扫描线程之前创建的，那会儿还不知道有哪些串口，
        所以这里挂个待办列表，等第一次拿到端口列表后再补。
        """
        if not self._last_ports or not self._pending_autopick:
            return
        for uid in list(self._pending_autopick):
            col = next((c for c in self._columns if c.cfg.uid == uid), None)
            if col is None:
                self._pending_autopick.remove(uid)
                continue
            used = {c.cfg.params.port for c in self._columns if c is not col and c.cfg.params.port}
            free = [p.device for p in self._last_ports if p.device not in used]
            if free and (not col.cfg.params.port or col.cfg.params.port in used):
                col.panel.select_port(free[0])
            self._pending_autopick.remove(uid)

    def occupied_ports(self) -> dict[str, str]:
        return {c.port: c.cfg.uid for c in self._columns if c.is_connected and c.port}

    # ------------------------------------------------------------------ 广播
    def apply_ports(self, ports) -> None:
        self._last_ports = list(ports)
        self._refresh_all()
        self._apply_autopicks()

    def apply_engine(self, engine: RuleEngine, replay: bool = False) -> None:
        self._engine = engine
        for col in self._columns:
            col.apply_engine(engine, replay)

    def set_presets(self, presets) -> None:
        for col in self._columns:
            col.set_presets(presets)

    def shutdown(self) -> None:
        for col in self._columns:
            col.shutdown()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(PREF_MIN * 2 + 120, 600)

    def wheelEvent(self, event) -> None:  # noqa: N802
        """列多到要横向滚动时，竖直滚轮也横向滚。"""
        bar = self.horizontalScrollBar()
        delta = event.angleDelta()
        if bar.maximum() > 0 and delta.y() and not delta.x():
            bar.setValue(bar.value() - delta.y())
            event.accept()
            return
        super().wheelEvent(event)
