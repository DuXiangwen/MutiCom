"""单列的串口参数面板 —— 可折叠抽屉。

结构固定为「抽屉头 + 主体」：
    [⌄] 串口参数                       ← 展开时的头（整行可点）
    [›] COM253 · 115200 8N1 · 已连接   ← 折叠时的头，同一控件换了文案
主体内部按列宽换三套排布（见 _SPEC_A/B/C），窄列改成单列铺满、去掉文字标签。

所有宽度都按字体度量算（width_for_text），不写死像素 —— 写死过一次，把「数据流控」
和「UTF-8」都切了。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QMouseEvent, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .app_config import Flow, Parity, PortParams
from .icons import icons
from .port_scanner import PortInfo
from .ui_kit import DarkComboBox, ElideLabel, ThemedIconButton, width_for_text

BAUDRATES = (9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600, 1000000, 2000000, 3000000)
BYTESIZES = (5, 6, 7, 8)
STOPBITS = (("1", 1.0), ("1.5", 1.5), ("2", 2.0))
PARITIES = (("无", Parity.NONE), ("偶", Parity.EVEN), ("奇", Parity.ODD), ("Mark", Parity.MARK), ("Space", Parity.SPACE))
FLOWS = (("无", Flow.NONE), ("RTS/CTS", Flow.RTSCTS), ("XON/XOFF", Flow.XONXOFF))

TIER_A, TIER_B, TIER_C = 0, 1, 2

def _label(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("fieldLabel")
    lab.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    lab.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
    return lab


class PanelHeader(QWidget):
    """抽屉头：chevron + 标题/摘要。整行可点。"""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("panelHeader")
        self.setFixedHeight(26)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 0, 4, 0)
        lay.setSpacing(6)
        self.chevron = QLabel()
        self.chevron.setFixedSize(14, 14)
        self.title = ElideLabel("串口参数", Qt.TextElideMode.ElideMiddle)
        self.title.setObjectName("panelTitle")
        lay.addWidget(self.chevron)
        lay.addWidget(self.title, 1)
        self._collapsed = False
        theme.bus.changed.connect(self._refresh_icon)
        self._refresh_icon()

    def set_text(self, text: str, collapsed: bool) -> None:
        self._collapsed = collapsed
        self.title.setObjectName("panelTitle")
        self.title.setProperty("collapsed", "true" if collapsed else "false")
        self.title.set_full_text(text)
        self.title.style().unpolish(self.title)
        self.title.style().polish(self.title)
        self._refresh_icon()

    def _refresh_icon(self, *_args) -> None:
        name = "chevron-right" if self._collapsed else "chevron-down"
        pm: QPixmap = icons.pixmap(name, theme.color("text.dim"), 14)
        # 位图是 2 倍超采样，QLabel 不会自动缩，这里必须显式缩放，否则只显示左上四分之一
        self.chevron.setPixmap(
            pm.scaled(14, 14, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class PortPanel(QFrame):
    connectRequested = Signal()
    disconnectRequested = Signal()
    paramsChanged = Signal()
    dtrChanged = Signal(bool)
    rtsChanged = Signal(bool)
    breakToggled = Signal(bool)
    collapsedChanged = Signal(bool)      # 用户点了抽屉头，外层负责持久化

    def __init__(self, params: PortParams, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        self._params = params
        self._connected = False
        self._ports: list[PortInfo] = []
        self._tier = -1
        self._collapsed = False
        self._auto = False
        self._user_choice: bool | None = None
        self._occupied: dict[str, tuple[str, str]] = {}
        self._own_uid = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 8)
        outer.setSpacing(6)

        # -- 抽屉头 -------------------------------------------------------
        self.header = PanelHeader()
        self.btn_connect_mini = ThemedIconButton("plug", "连接", size=16)
        self.btn_connect_mini.setFixedSize(26, 26)
        head_row = QHBoxLayout()
        head_row.setContentsMargins(0, 0, 0, 0)
        head_row.setSpacing(4)
        head_row.addWidget(self.header, 1)
        head_row.addWidget(self.btn_connect_mini)
        outer.addLayout(head_row)

        # -- 主体 ---------------------------------------------------------
        self.body = QWidget()
        body_lay = QVBoxLayout(self.body)
        body_lay.setContentsMargins(0, 0, 0, 0)
        body_lay.setSpacing(6)

        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(6)
        self.grid.setVerticalSpacing(6)

        self._labels: list[QLabel] = []

        # 端口 + 刷新
        self.combo_port = self._make_combo("COM253", expanding=True)
        self.combo_port.setToolTip("选择串口（列表每 3 秒自动刷新）")
        self.combo_port.setProperty("elideOk", True)
        self.btn_refresh = ThemedIconButton("refresh", "立即重新扫描串口", size=16)
        self.cell_port = QWidget()
        port_row = QHBoxLayout(self.cell_port)
        port_row.setContentsMargins(0, 0, 0, 0)
        port_row.setSpacing(4)
        port_row.addWidget(self.combo_port, 1)
        port_row.addWidget(self.btn_refresh)
        self.cell_port.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.l_port = _label("端口号")
        self._labels.append(self.l_port)

        self.lbl_detail = ElideLabel("", Qt.TextElideMode.ElideMiddle)
        self.lbl_detail.setObjectName("hint")
        self.lbl_detail.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        # 波特率
        self.combo_baud = self._make_combo("2000000", expanding=True)
        self.combo_baud.setEditable(True)
        for b in BAUDRATES:
            self.combo_baud.addItem(str(b))
        self.combo_baud.setCurrentText(str(params.baudrate))
        self.combo_baud.setToolTip("波特率（可以直接输入任意值）")
        self.l_baud = _label("波特率")
        self._labels.append(self.l_baud)

        # 流控 / 停止位
        self.combo_flow = self._make_combo("XON/XOFF", expanding=True)
        for text, value in FLOWS:
            self.combo_flow.addItem(text, value.value)
        self.combo_flow.setToolTip("数据流控")
        self.l_flow = _label("数据流控")
        self._labels.append(self.l_flow)

        self.combo_stopbits = self._make_combo("1.5", expanding=False)
        self.combo_stopbits.setToolTip("停止位")
        for text, value in STOPBITS:
            self.combo_stopbits.addItem(text, value)
        self.l_stop = _label("停止位")
        self._labels.append(self.l_stop)

        # 校验位 / 数据位
        self.combo_parity = self._make_combo("Space", expanding=True)
        for text, value in PARITIES:
            self.combo_parity.addItem(text, value.value)
        self.combo_parity.setToolTip("校验位")
        self.l_par = _label("校验位")
        self._labels.append(self.l_par)

        self.combo_bytesize = self._make_combo("8", expanding=False)
        self.combo_bytesize.setToolTip("数据位")
        for n in BYTESIZES:
            self.combo_bytesize.addItem(str(n), n)
        self.l_byte = _label("数据位")
        self._labels.append(self.l_byte)

        # 控制线
        self.btn_dtr = self._small_button(
            "DTR", "DTR 电平。很多板子把它接 RESET，点亮=拉高（可能复位板子）", True
        )
        self.btn_rts = self._small_button(
            "RTS", "RTS 电平。常接 EN/BOOT0，或用于硬件流控，点亮=拉高", True
        )
        self.btn_break = self._small_button(
            "BRK", "保持 Break（把 TX 拉低）；再点一下释放。保持期间发不出数据", True
        )
        self.cell_line = QWidget()
        line_row = QHBoxLayout(self.cell_line)
        line_row.setContentsMargins(0, 0, 0, 0)
        line_row.setSpacing(4)
        for b in (self.btn_dtr, self.btn_rts, self.btn_break):
            line_row.addWidget(b, 1)
        self.cell_line.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        body_lay.addLayout(self.grid)

        self.lbl_hint = QLabel("")
        self.lbl_hint.setObjectName("errorHint")
        self.lbl_hint.setWordWrap(True)
        self.lbl_hint.hide()
        body_lay.addWidget(self.lbl_hint)

        self.btn_connect = QPushButton("连接")
        self.btn_connect.setObjectName("primary")
        self.btn_connect.setFixedHeight(28)
        self.btn_connect.setCursor(Qt.CursorShape.PointingHandCursor)
        body_lay.addWidget(self.btn_connect)
        outer.addWidget(self.body)

        self._specs = {
            "A": [
                (self.l_port, 0, 0, 1, 1), (self.cell_port, 0, 1, 1, 3),
                (self.lbl_detail, 1, 1, 1, 3),
                (self.l_baud, 2, 0, 1, 1), (self.combo_baud, 2, 1, 1, 3),
                (self.l_flow, 3, 0, 1, 1), (self.combo_flow, 3, 1, 1, 1),
                (self.l_stop, 3, 2, 1, 1), (self.combo_stopbits, 3, 3, 1, 1),
                (self.l_par, 4, 0, 1, 1), (self.combo_parity, 4, 1, 1, 1),
                (self.l_byte, 4, 2, 1, 1), (self.combo_bytesize, 4, 3, 1, 1),
                (self.cell_line, 5, 0, 1, 4),
            ],
            "B": [
                (self.l_port, 0, 0, 1, 1), (self.cell_port, 0, 1, 1, 3),
                (self.lbl_detail, 1, 1, 1, 3),
                (self.l_baud, 2, 0, 1, 1), (self.combo_baud, 2, 1, 1, 3),
                (self.l_flow, 3, 0, 1, 1), (self.combo_flow, 3, 1, 1, 3),
                (self.l_byte, 4, 0, 1, 1), (self.combo_bytesize, 4, 1, 1, 1),
                (self.l_stop, 4, 2, 1, 1), (self.combo_stopbits, 4, 3, 1, 1),
                (self.l_par, 5, 0, 1, 1), (self.combo_parity, 5, 1, 1, 3),
                (self.cell_line, 6, 0, 1, 4),
            ],
            "C": [
                (self.cell_port, 0, 0, 1, 1),
                (self.combo_baud, 1, 0, 1, 1),
                (self.combo_flow, 2, 0, 1, 1),
                (self.combo_parity, 3, 0, 1, 1),
                (self.combo_bytesize, 4, 0, 1, 1),
                (self.combo_stopbits, 5, 0, 1, 1),
                (self.cell_line, 6, 0, 1, 1),
            ],
        }
        self._all_widgets = [w for spec in self._specs.values() for w, *_ in spec]
        self._all_widgets = list(dict.fromkeys(self._all_widgets))
        self._grid_key = ""

        # -- 初值 ------------------------------------------------------
        self._select(self.combo_flow, params.flow.value)
        self._select(self.combo_parity, params.parity.value)
        self._select(self.combo_bytesize, params.bytesize)
        self._select(self.combo_stopbits, params.stopbits)

        # -- 连接 ------------------------------------------------------
        self.header.clicked.connect(self._toggle)
        self.btn_connect.clicked.connect(self._on_connect_clicked)
        self.btn_connect_mini.clicked.connect(self._on_connect_clicked)
        self.btn_refresh.clicked.connect(self._emit_refresh)
        self.combo_port.currentIndexChanged.connect(self._on_port_changed)
        for combo in (self.combo_baud, self.combo_flow, self.combo_parity, self.combo_bytesize, self.combo_stopbits):
            combo.currentIndexChanged.connect(lambda *_: self.paramsChanged.emit())
        self.combo_baud.lineEdit().editingFinished.connect(self.paramsChanged)
        self.btn_dtr.toggled.connect(self.dtrChanged)
        self.btn_rts.toggled.connect(self.rtsChanged)
        self.btn_break.toggled.connect(self.breakToggled)

        self.apply_tier(TIER_A)
        self._apply_collapsed(False)
        self.set_connected(False)      # 没连串口之前，控制线按钮点了也没意义，直接禁用

    # ------------------------------------------------------------------ 构件
    @staticmethod
    def _make_combo(min_text: str, expanding: bool) -> DarkComboBox:
        c = DarkComboBox()
        c.setFixedHeight(24)
        need = width_for_text(c, min_text)
        if expanding:
            c.setMinimumWidth(need)          # 显式最小宽会覆盖 minimumSizeHint，才能真的压下来
            c.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        else:
            c.setFixedWidth(need)
            c.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        return c

    @staticmethod
    def _small_button(text: str, tip: str, checkable: bool) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("tiny")          # 紧凑内边距，窄列里才放得下 DTR/RTS/BRK
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setFixedHeight(24)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return b

    # ------------------------------------------------------------------ 折叠
    def restore_collapsed(self, value: bool | None) -> None:
        self._user_choice = value
        if value is not None:
            self._apply_collapsed(value)

    def set_auto_collapsed(self, on: bool) -> None:
        self._auto = on
        if self._user_choice is None:
            self._apply_collapsed(on)

    @property
    def is_collapsed(self) -> bool:
        return self._collapsed

    def _toggle(self) -> None:
        self._user_choice = not self._collapsed
        self._apply_collapsed(self._user_choice)
        self.collapsedChanged.emit(self._user_choice)

    def _apply_collapsed(self, on: bool) -> None:
        self._collapsed = on
        self.body.setVisible(not on)
        self.btn_connect_mini.setVisible(on)
        self.btn_connect_mini.setToolTip("断开" if self._connected else "连接")
        self._refresh_header()

    # ------------------------------------------------------------------ 分档
    def apply_tier(self, tier: int) -> None:
        if tier == self._tier:
            return
        self._tier = tier
        key = "A" if tier == TIER_A else ("B" if tier == TIER_B else "C")
        for lab in self._labels:
            f = lab.font()
            f.setPointSize(9 if key != "C" else 8)
            lab.setFont(f)
        self._fit_labels()
        self._apply_grid(key)
        # 端口条目的文案长度跟档位绑定（C 档只留设备名），换档要把列表重建一遍
        if self._ports:
            self.set_ports(self._ports, self._occupied, self._own_uid)
        self._refresh_header()

    def _fit_labels(self) -> None:
        for lab in self._labels:
            w = lab.fontMetrics().horizontalAdvance(lab.text()) + 4
            lab.setMinimumWidth(w)
            lab.setMaximumWidth(w)

    def _apply_grid(self, key: str) -> None:
        if key == self._grid_key:
            return
        self._grid_key = key
        grid = self.grid
        for w in self._all_widgets:
            w.hide()                       # 先全藏，避免重投时闪一帧
        while grid.count():
            grid.takeAt(0)                 # 只摘 item，不 deleteLater，信号不受影响
        for c in range(4):
            grid.setColumnStretch(c, 0)
            grid.setColumnMinimumWidth(c, 0)
        for w, r, c, rs, cs in self._specs[key]:
            grid.addWidget(w, r, c, rs, cs)
            w.show()
        if key == "C":
            grid.setColumnStretch(0, 1)
        else:
            grid.setColumnStretch(1, 1)
            grid.setColumnStretch(3, 0 if key == "A" else 1)
        grid.setVerticalSpacing(6 if key == "A" else 4)

    # ------------------------------------------------------------------ 数据
    def params(self) -> PortParams:
        port = self.combo_port.currentData() or ""
        try:
            baud = int(self.combo_baud.currentText().strip())
        except ValueError:
            baud = 115200
        return PortParams(
            port=str(port),
            baudrate=baud,
            bytesize=int(self.combo_bytesize.currentData() or 8),
            parity=Parity(self.combo_parity.currentData() or "N"),
            stopbits=float(self.combo_stopbits.currentData() or 1.0),
            flow=Flow(self.combo_flow.currentData() or "none"),
        )

    def set_ports(self, ports: list[PortInfo], occupied: dict[str, tuple[str, str]], own_uid: str = "") -> None:
        """occupied: {端口名: (占用它的列 uid, 列标题)}，自己占用的那个不算冲突。"""
        self._ports = ports
        self._occupied = occupied
        self._own_uid = own_uid
        limit = 10 if self._tier >= TIER_C else (16 if self._tier == TIER_B else 20)
        current = self.combo_port.currentData()
        self.combo_port.blockSignals(True)
        self.combo_port.clear()
        for info in ports:
            busy = occupied.get(info.device)
            conflict = busy is not None and busy[0] != own_uid
            text = f"{info.device} 被{busy[1]}占用" if conflict else _short_label(info, limit)
            self.combo_port.addItem(text, info.device)
            idx = self.combo_port.count() - 1
            if conflict:
                item = self.combo_port.model().item(idx)
                if item is not None:
                    item.setEnabled(False)
            self.combo_port.setItemData(
                idx, f"{info.device} · {info.description}\n{info.detail}", Qt.ItemDataRole.ToolTipRole
            )

        target = current or self._params.port
        if target and self.combo_port.findData(target) < 0:
            self.combo_port.addItem(f"{target}（未检测到）", target)
        if self.combo_port.count() == 0:
            self.combo_port.addItem("（没有检测到串口）", "")
            item = self.combo_port.model().item(0)
            if item is not None:
                item.setEnabled(False)
        idx = self.combo_port.findData(target) if target else 0
        self.combo_port.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_port.blockSignals(False)
        selected = self.combo_port.currentData()
        if selected:
            self._params.port = str(selected)
        self._update_detail()
        self._refresh_header()

    @property
    def ports(self) -> list[PortInfo]:
        return self._ports

    def select_port(self, device: str) -> None:
        idx = self.combo_port.findData(device)
        if idx >= 0 and idx != self.combo_port.currentIndex():
            self.combo_port.setCurrentIndex(idx)

    def set_line_state(self, dtr: bool, rts: bool) -> None:
        """按串口的真实电平同步按钮显示（pyserial 打开时两根线默认都是拉高的）。"""
        for btn, value in ((self.btn_dtr, dtr), (self.btn_rts, rts)):
            btn.blockSignals(True)
            btn.setChecked(value)
            btn.blockSignals(False)

    @property
    def is_break_active(self) -> bool:
        return self.btn_break.isChecked()

    def set_connected(self, connected: bool, port: str = "") -> None:
        self._connected = connected
        for w in (
            self.combo_port,
            self.combo_baud,
            self.combo_flow,
            self.combo_parity,
            self.combo_bytesize,
            self.combo_stopbits,
            self.btn_refresh,
        ):
            w.setEnabled(not connected)
        self.btn_connect.setText("断开" if connected else "连接")
        self.btn_connect.setObjectName("danger" if connected else "primary")
        self.btn_connect.style().unpolish(self.btn_connect)
        self.btn_connect.style().polish(self.btn_connect)
        self.btn_connect_mini.set_icon_name("unplug" if connected else "plug")
        self.btn_connect_mini.setToolTip("断开" if connected else "连接")
        self.btn_dtr.setEnabled(connected)
        self.btn_rts.setEnabled(connected)
        self.btn_break.setEnabled(connected)
        if not connected:
            for btn in (self.btn_dtr, self.btn_rts, self.btn_break):
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
            self.lbl_detail.set_full_text(self._selected_detail())
        else:
            self.lbl_detail.set_full_text(f"{port} 已连接")
        self._refresh_header()

    def show_error(self, text: str) -> None:
        self.lbl_hint.setText(text)
        self.lbl_hint.show()
        if self._collapsed:
            # 收着的时候报错，得让用户看见
            self._apply_collapsed(False)

    def clear_error(self) -> None:
        self.lbl_hint.clear()
        self.lbl_hint.hide()

    # ------------------------------------------------------------------ 内部
    @staticmethod
    def _select(combo: DarkComboBox, value: object) -> None:
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _on_connect_clicked(self) -> None:
        if self._connected:
            self.disconnectRequested.emit()
        else:
            self.clear_error()
            self.connectRequested.emit()

    def _emit_refresh(self) -> None:
        self.paramsChanged.emit()

    def _on_port_changed(self) -> None:
        self._params.port = self.combo_port.currentData() or ""
        self._update_detail()
        self._refresh_header()
        self.paramsChanged.emit()

    def _selected_detail(self) -> str:
        name = self.combo_port.currentData()
        for info in self._ports:
            if info.device == name:
                return f"{info.description}  {info.detail}" if info.description else info.detail
        return "该端口当前未被检测到"

    def _update_detail(self) -> None:
        self.lbl_detail.set_full_text(self._selected_detail())

    def _refresh_header(self) -> None:
        """展开时显示"串口参数"，折叠时显示按宽度降级的摘要（绝不硬裁）。"""
        p = self.params()
        if not self._collapsed:
            self.header.set_text("串口参数", False)
            self.header.setToolTip("点击折叠串口参数")
            return
        port = p.port or "未选端口"
        rate = str(p.baudrate)
        frame = f"{p.bytesize}{p.parity.value}{p.stopbits:g}"
        state = " · 已连接" if self._connected else ""
        avail = max(0, self.header.title.width() - 4)
        fm = self.header.title.fontMetrics()
        for cand in (f"{port} · {rate} {frame}{state}", f"{port} · {rate}{state}", f"{rate} {frame}", port, rate):
            if fm.horizontalAdvance(cand) <= avail:
                break
        else:
            cand = port[:8]
        self.header.set_text(cand, True)
        self.header.setToolTip(f"{p.summary()}{state}\n点击展开串口参数")

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._collapsed:
            QTimer.singleShot(0, self._refresh_header)


def _short_label(info: PortInfo, limit: int = 20) -> str:
    """下拉里放短标签，完整描述进 tooltip —— 长文本会把下拉的最小宽度撑死。"""
    desc = (info.description or "").strip()
    label = f"{info.device}  {desc}" if desc else info.device
    return label if len(label) <= limit else label[: limit - 1] + "…"


__all__ = ["PortPanel", "PanelHeader"]
