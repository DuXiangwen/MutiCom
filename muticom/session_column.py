"""一路串口的完整列：列头 + 参数面板 + 日志工具条 + 日志区 + 发送区。

列宽决定布局档位（A ≥300 / B 240-299 / C <240），三块子面板各自降级。
只切换控件可见性和尺寸，不重建布局 —— 重建会丢焦点、也会在拖拽时闪。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .app_config import ColumnConfig
from .colorizer import RuleEngine
from .log_view import LogToolBar, LogView
from .port_panel import PortPanel
from .send_panel import SendPanel
from .serial_session import SerialSession
from .ui_kit import ElideLabel, StatusDot, ThemedIconButton

TIER_A, TIER_B, TIER_C = 0, 1, 2


def human_bytes(n: float) -> str:
    if n < 1024:
        return f"{int(n)} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / 1024 / 1024:.2f} MB"


class SessionColumn(QFrame):
    closeRequested = Signal(str)
    stateChanged = Signal(str, bool)
    changed = Signal(str)             # 配置有变化，外层去安排存盘
    saveLogRequested = Signal(str)
    managePresetsRequested = Signal()
    dragStarted = Signal(str)          # 开始拖列头
    dragMoved = Signal(QPoint)         # 全局坐标
    dragFinished = Signal()

    MIN_WIDTH = 168

    def __init__(self, cfg: ColumnConfig, engine: RuleEngine | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("column")
        self.setMinimumWidth(self.MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.cfg = cfg
        self.view = cfg.view
        self._engine = engine or RuleEngine()
        self._index = 1
        self._occupied: dict[str, tuple[str, str]] = {}
        self._rx_total = 0
        self._tx_total = 0
        self._rate = 0.0
        self._connected = False
        self._tier = -1

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 8)
        outer.setSpacing(6)

        # -- 列头 -------------------------------------------------------
        header = QWidget()
        header.setObjectName("colHeader")
        header.setFixedHeight(30)
        header.setCursor(Qt.CursorShape.OpenHandCursor)
        header.setToolTip("按住列头可以拖动，调整列的先后顺序")
        self._header = header
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 4)
        hl.setSpacing(6)

        self.dot = StatusDot()
        self.lbl_title = ElideLabel("串口", Qt.TextElideMode.ElideMiddle)
        self.lbl_title.setObjectName("colTitle")
        self.lbl_live = QLabel("LIVE")
        self.lbl_live.setObjectName("liveChip")
        self.lbl_live.hide()
        self.lbl_stats = ElideLabel("", Qt.TextElideMode.ElideLeft)
        self.lbl_stats.setObjectName("colStats")
        self.lbl_stats.setTextFormat(Qt.TextFormat.PlainText)
        self.btn_close = ThemedIconButton("close", "关闭这一列", role="normal", size=16)
        self.btn_close.setFixedSize(24, 24)
        self.btn_close.clicked.connect(lambda: self.closeRequested.emit(self.cfg.uid))

        hl.addWidget(self.dot)
        hl.addWidget(self.lbl_title, 1)
        hl.addWidget(self.lbl_live)
        hl.addWidget(self.lbl_stats)
        hl.addWidget(self.btn_close)
        outer.addWidget(header)
        # 列头里的文字/圆点不该吃鼠标事件，否则拖不动（✕ 保留自己的点击）
        for w in (self.dot, self.lbl_title, self.lbl_live, self.lbl_stats):
            w.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        header.installEventFilter(self)
        self._drag_origin: QPoint | None = None
        self._dragging = False

        # -- 参数面板 ---------------------------------------------------
        self.panel = PortPanel(cfg.params)
        self.panel.restore_collapsed(cfg.panel_collapsed)
        self.panel.collapsedChanged.connect(self._on_collapsed_changed)
        outer.addWidget(self.panel)

        # -- 日志 -------------------------------------------------------
        self.toolbar = LogToolBar(self.view)
        outer.addWidget(self.toolbar)

        self.log = LogView(self.view, self._engine)
        outer.addWidget(self.log, 1)

        # -- 发送 -------------------------------------------------------
        self.send = SendPanel(cfg.send)
        self.send.set_encoding(self.view.encoding)
        self.send.set_draft(cfg.draft)
        outer.addWidget(self.send)

        # -- 会话 -------------------------------------------------------
        self.session = SerialSession(self)

        self._wire()
        self._refresh_title()
        self._apply_tier(self.width())

    # ------------------------------------------------------------------ 连接
    def _wire(self) -> None:
        self.panel.connectRequested.connect(self._on_connect)
        self.panel.disconnectRequested.connect(self.session.close)
        self.panel.paramsChanged.connect(self._on_params_changed)
        self.panel.dtrChanged.connect(self.session.set_dtr)
        self.panel.rtsChanged.connect(self.session.set_rts)
        self.panel.breakToggled.connect(self.session.set_break)

        self.toolbar.changed.connect(self._on_view_changed)
        self.toolbar.clearRequested.connect(self._on_clear_requested)
        self.toolbar.saveLogRequested.connect(lambda: self.saveLogRequested.emit(self.cfg.uid))
        self.toolbar.copyAllRequested.connect(self._copy_all)

        self.log.autoScrollInterrupted.connect(self._on_autoscroll_interrupted)
        self.log.droppedLines.connect(lambda _n: self._update_stats())

        self.send.sendRequested.connect(self._on_send)
        self.send.managePresetsRequested.connect(self.managePresetsRequested)

        self.session.opened.connect(self._on_opened)
        self.session.closed.connect(self._on_closed)
        self.session.failed.connect(self._on_failed)
        self.session.linesReady.connect(self.log.append_lines)
        self.session.stats.connect(self._on_stats)

    def _on_connect(self) -> None:
        params = self.panel.params()
        if not params.port:
            self.panel.show_error("请先选择串口")
            return
        busy = self._occupied.get(params.port)
        if busy and busy[0] != self.cfg.uid:
            self.panel.show_error(f"{params.port} 正被「{busy[1]}」使用")
            return
        self.cfg.params = params
        self.panel.btn_connect.setEnabled(False)
        self.session.open(params, self.view)

    def _on_opened(self) -> None:
        self._connected = True
        self._rx_total = self._tx_total = 0
        self._rate = 0.0
        params = self.panel.params()
        self.panel.set_connected(True, params.port)
        self.panel.set_line_state(True, True)   # pyserial 打开串口时这两根线本来就是高的
        self.panel.btn_connect.setEnabled(True)
        self.panel.clear_error()
        self.dot.setOn(True)
        self._refresh_title()
        self.log.set_connection_state(True, params.summary())
        self._update_stats()
        self.log.append_system(f"{SYS_JOIN} 已连接 {params.summary()} {SYS_JOIN}")
        self.stateChanged.emit(self.cfg.uid, True)

    def _on_closed(self) -> None:
        if not self._connected:
            self.panel.btn_connect.setEnabled(True)
            return
        self._connected = False
        self.panel.set_connected(False)
        self.panel.btn_connect.setEnabled(True)
        self.dot.setOn(False)
        self._rate = 0.0
        self.log.set_connection_state(False)
        self._refresh_title()
        self._update_stats()
        self.log.append_system(f"{SYS_JOIN} 已断开 {SYS_JOIN}")
        self.stateChanged.emit(self.cfg.uid, False)

    def _on_failed(self, msg: str) -> None:
        self.panel.show_error(msg)
        self.panel.btn_connect.setEnabled(True)
        self.log.append_system(f"[错误] {msg}", "danger")

    def _on_stats(self, rx: int, tx: int, rate: float) -> None:
        self._rx_total, self._tx_total, self._rate = rx, tx, rate
        self._update_stats()

    def _on_send(self, payload: bytes) -> None:
        if self._connected and self.panel.is_break_active:
            self.log.append_system("[提示] Break 保持中，数据发不出去，先点 BRK 释放", "warn")
            return
        if not self._connected:
            self.log.append_system("[提示] 未连接，发送被忽略", "warn")
            return
        if len(payload) > 8 * 1024 * 1024:
            self.log.append_system("[提示] 单次发送上限 8MB，已忽略", "warn")
            return
        self.session.send(payload)

    # ------------------------------------------------------------------ 视图
    def _on_clear_requested(self) -> None:
        self.log.clear_log()
        # 缓冲也要清，不然切 Hex/文本会把刚清掉的数据重新吐回来
        self.session.reset_buffer()

    def _on_collapsed_changed(self, collapsed: bool) -> None:
        self.cfg.panel_collapsed = collapsed
        self.changed.emit(self.cfg.uid)

    def _on_view_changed(self) -> None:
        self.log.set_view(self.view)
        self.log.set_font_size(self.view.font_size)
        self.log.set_max_lines(self.view.max_lines)
        self.send.set_encoding(self.view.encoding)
        if self._connected:
            self.session.apply_view(self.view)
        self.changed.emit(self.cfg.uid)

    def _on_autoscroll_interrupted(self) -> None:
        self.toolbar.set_autoscroll(False)
        self.changed.emit(self.cfg.uid)

    def _on_params_changed(self) -> None:
        if not self._connected:
            self.cfg.params = self.panel.params()
            self._refresh_title()
            self.changed.emit(self.cfg.uid)

    def _copy_all(self) -> None:
        self.log.selectAll()
        self.log.copy()
        cursor = self.log.textCursor()
        cursor.clearSelection()
        self.log.setTextCursor(cursor)

    # ------------------------------------------------------------------ 主题
    def apply_engine(self, engine: RuleEngine, replay: bool = False) -> None:
        """换着色引擎。replay=True 用于改了用户规则（让 worker 重放环形缓冲）；
        切主题走 replay=False + retint，历史和当前都要正确。"""
        self._engine = engine
        self.log.set_engine(engine)
        self.session.apply_rules(engine, replay)
        if not replay:
            self.log.retint(engine)

    # ------------------------------------------------------------------ 外部
    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def port(self) -> str:
        return self.panel.params().port

    @property
    def title(self) -> str:
        return self.lbl_title.full_text()

    @property
    def display_name(self) -> str:
        """占位提示里用的身份，比端口名好认（两列可能都叫 COM254 时不会绕晕）。"""
        return f"第 {self._index} 列"

    def set_index(self, n: int) -> None:
        self._index = n
        self._refresh_title()

    def set_ports(self, ports, occupied: dict[str, tuple[str, str]]) -> None:
        """端口列表和占用情况一起下发 —— 只给其中一样都可能刷出个空下拉框。"""
        self._occupied = occupied
        self.panel.set_ports(ports, occupied, self.cfg.uid)
        self._refresh_title()

    def set_presets(self, presets) -> None:
        self.send.set_presets(presets)

    def config(self) -> ColumnConfig:
        # 逐字段写回，不要换对象：面板手里握着的是同一个 PortParams，
        # 一旦这里 new 一个，面板后续改的就是旧对象，两边各说各话
        # （表现：自动挑口永远觉得"这个口没人用"，新列全挑第一个口）
        latest = self.panel.params()
        for field in ("port", "baudrate", "bytesize", "parity", "stopbits", "flow"):
            setattr(self.cfg.params, field, getattr(latest, field))
        self.cfg.draft = self.send.draft()
        return self.cfg

    def connect_now(self) -> None:
        """启动时自动连接用。"""
        if not self._connected:
            self._on_connect()

    def shutdown(self) -> None:
        self.session.shutdown()

    # ------------------------------------------------------------------ 分档
    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is not self._header:
            return super().eventFilter(obj, event)
        et = event.type()
        if et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint()
            self._dragging = False
            return False
        if et == QEvent.Type.MouseMove and self._drag_origin is not None:
            pos = event.globalPosition().toPoint()
            if not self._dragging:
                if (pos - self._drag_origin).manhattanLength() < 8:
                    return False          # 还没到阈值，继续当普通点击
                self._dragging = True
                self._header.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.dragStarted.emit(self.cfg.uid)
            self.dragMoved.emit(pos)
            return True
        if et == QEvent.Type.MouseButtonRelease and self._drag_origin is not None:
            self._drag_origin = None
            if self._dragging:
                self._dragging = False
                self._header.setCursor(Qt.CursorShape.OpenHandCursor)
                self.dragFinished.emit()
                return True
        return super().eventFilter(obj, event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_tier(event.size().width())

    # 窗口矮于这个值时，参数面板让位给日志区（日志才是主角）
    SHORT_HEIGHT = 560

    def _apply_tier(self, width: int) -> None:
        # 工具条要每次都重算：档位没变但宽度变了（270→240）也会溢出，它有 _active 短路，开销可忽略
        self.toolbar.apply_width(max(0, width - 18))
        tier = TIER_A if width >= 300 else (TIER_B if width >= 240 else TIER_C)
        if tier == self._tier:
            self._sync_collapse()
            return
        self._tier = tier
        self.panel.apply_tier(tier)
        self.send.apply_tier(tier)
        self.lbl_live.setVisible(self._connected and tier == TIER_A)
        self.btn_close.setFixedSize(24, 24)
        self._sync_collapse()
        self._update_stats()

    def _sync_collapse(self) -> None:
        """窄列或矮窗口默认收起；用户点过抽屉头之后以用户为准。"""
        short = self.height() < self.SHORT_HEIGHT
        self.panel.set_auto_collapsed(self._tier >= TIER_C or short)

    # ------------------------------------------------------------------ 内部
    def _refresh_title(self) -> None:
        port = self.panel.params().port
        if self._connected and port:
            text = port
        elif port and self._tier <= TIER_B:
            text = f"{port} · 未连接"
        elif port:
            text = port                      # 窄列：状态靠圆点表示，省下的宽度给速率
        else:
            text = f"串口 {self._index}"
        self.lbl_title.setProperty("state", "live" if self._connected else "idle")
        self.lbl_title.style().unpolish(self.lbl_title)
        self.lbl_title.style().polish(self.lbl_title)
        self.lbl_title.set_full_text(text)
        self.dot.setToolTip("已连接" if self._connected else (port or "未连接"))
        self._update_stats()

    def _update_stats(self) -> None:
        parts: list[str] = []
        if self._tier >= TIER_C:
            if self._connected:
                parts.append(f"{human_bytes(self._rate)}/s")
        elif self._connected or self._rx_total or self._tx_total:
            parts.append(f"RX {human_bytes(self._rx_total)}")
            if self._tier == TIER_A:
                parts.append(f"TX {human_bytes(self._tx_total)}")
            if self._connected:
                parts.append(f"{human_bytes(self._rate)}/s")
            if self.log.dropped:
                parts.append(f"丢弃 {self.log.dropped}")
        text = "  ".join(parts)
        self.lbl_stats.set_full_text(text)
        self.lbl_stats.setToolTip(text or "RX / TX / 速率")
        self._fit_header()

    def _fit_header(self) -> None:
        """先满足标题，剩下的给统计；连 60px 都不到就干脆不显示统计。"""
        inner = max(0, self.width() - 18)
        fixed = 8 + 24 + 6 * 4                     # 状态点 + 关闭按钮 + 4 个间距
        live = 40 if self.lbl_live.isVisible() else 0
        avail = max(0, inner - fixed - live)
        fm = self.lbl_title.fontMetrics()
        need = fm.horizontalAdvance(self.lbl_title.full_text()) + 2
        title_w = min(need, max(60, avail - 60))
        stats_w = max(0, avail - title_w)
        self.lbl_stats.setMaximumWidth(stats_w)
        self.lbl_stats.setVisible(stats_w >= 44 and bool(self.lbl_stats.full_text()))


SYS_JOIN = "——"

__all__ = ["SessionColumn", "human_bytes"]
