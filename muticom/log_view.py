"""日志显示区与它的工具条。

性能取舍（本机实测 20000 行）：
- 逐行 appendHtml：1.19s（≈17k 行/s）
- 每批拼成一个 HTML 串、单次 insertHtml：0.27s（≈70k 行/s）  ← 用这个
- QSyntaxHighlighter：高亮在 UI 线程跑，5 万 block 首次全量高亮要秒级，规则一改还要全量重刷 —— 不用

`white-space:pre` 必须写：不写的话前导空格会被 HTML 规则吞掉（日志缩进就没了），
写了之后不影响自动折行，`toPlainText()` 复制出来也还是普通空格（不会变 &nbsp;）。

主题切换时 `retint()` 会重新着色整个文档。已知取舍：已经显示出来的 Tx 回显行会退化成正文色
（发送方向没有留在文本里），换主题后再发就有了。这是为了不额外存一份 5 万行文本副本。
"""

from __future__ import annotations

import re
from collections import deque
from html import escape
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontMetricsF, QPainter, QTextCursor
from PySide6.QtWidgets import (
    QSizePolicy,
    QHBoxLayout,
    QMenu,
    QPlainTextEdit,
    QSizePolicy,
    QToolButton,
    QWidget,
)

from . import theme
from .app_config import ViewOptions
from .colorizer import RuleEngine
from .icons import ICON_SIZE, icons
from .ui_kit import DarkComboBox, ThemedIconButton, ToolTextButton

MONO_FAMILIES = ["Cascadia Mono", "Consolas", "JetBrains Mono", "Courier New"]

MAX_PENDING = 40_000       # 待渲染队列上限，超了丢最老的
FLUSH_INTERVAL = 30        # ms，一帧内最多渲染一次
FLUSH_BATCH = 2000         # 单次最多插入多少行，防止一帧卡死

ENCODINGS = (("UTF-8", "utf-8"), ("GBK", "gbk"), ("Latin-1", "latin-1"))
LINE_LIMITS = (10_000, 20_000, 50_000, 100_000, 200_000)

# append_system 用的三种系统消息前缀，retint 时靠前缀还原语义色
SYS, ERR, TIP = "——", "[错误]", "[提示]"

_TS_RE = re.compile(r"^\[\d{2}:\d{2}:\d{2}\.\d{3}\]")

TIER_A, TIER_B, TIER_C = 0, 1, 2


def mono_font(size: int) -> QFont:
    f = QFont()
    f.setFamilies(MONO_FAMILIES)
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPointSize(size)
    return f


class LogView(QPlainTextEdit):
    droppedLines = Signal(int)
    autoScrollInterrupted = Signal()

    def __init__(self, view: ViewOptions, engine: RuleEngine | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("logView")
        self._view = view
        self._engine = engine or RuleEngine()
        self._pending: deque[tuple[str, str | None, int, int]] = deque(maxlen=MAX_PENDING)
        self._dropped = 0
        self._connected = False
        self._conn_detail = ""
        self._sys_rows: list[tuple[str, str]] = []   # 系统提示行（已连接/断开…），重绘时要恢复

        self.setReadOnly(True)
        self.setUndoRedoEnabled(False)
        self.setFrameShape(QPlainTextEdit.Shape.NoFrame)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.document().setDocumentMargin(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._apply_font()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(FLUSH_INTERVAL)
        self._timer.timeout.connect(self._flush)

        # 用户自己往上翻的时候顺手关掉自动滚屏，别一边看一边被拽回底部
        self.verticalScrollBar().actionTriggered.connect(self._on_scroll_action)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        # QSS 的全局字体规则在 polish 时会盖掉 __init__ 里的 setFont
        # （现象：日志显示成微软雅黑 9pt，切一次视图才"变回"等宽），
        # polish 落地之后再设一次才稳。
        self._apply_font()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            # 切主题会重新 polish，字体又被 QSS 打回默认，等样式落地后补回来
            QTimer.singleShot(0, self._apply_font)

    # ------------------------------------------------------------------ 配置
    def _apply_font(self) -> None:
        f = mono_font(self._view.font_size)
        self.setFont(f)
        self.setTabStopDistance(QFontMetricsF(f).horizontalAdvance(" ") * 4)
        self.setMaximumBlockCount(self._view.max_lines)

    def set_font_size(self, pt: int) -> None:
        self._view.font_size = max(9, min(20, pt))
        self._apply_font()

    def set_max_lines(self, n: int) -> None:
        self._view.max_lines = int(n)
        self.setMaximumBlockCount(int(n))

    def set_view(self, view: ViewOptions) -> None:
        self._view = view
        self._apply_font()

    def set_engine(self, engine: RuleEngine) -> None:
        self._engine = engine

    def set_connection_state(self, on: bool, detail: str = "") -> None:
        """占位文案要用：连着但没数据 ≠ 没连。"""
        self._connected = on
        self._conn_detail = detail
        self.viewport().update()

    @property
    def dropped(self) -> int:
        return self._dropped

    # ------------------------------------------------------------------ 写入
    def append_lines(self, lines: Sequence[tuple[str, str | None, int, int]], reset: bool = False) -> None:
        if reset:
            # 重绘（切 Hex/文本、改规则）：保留"已连接"这类系统行，别把它们弄丢
            self._redraw()
        if not lines:
            return
        overflow = len(self._pending) + len(lines) - MAX_PENDING
        if overflow > 0:
            self._dropped += overflow
            self.droppedLines.emit(overflow)
        self._pending.extend(lines)
        if not self._timer.isActive():
            self._timer.start()

    def append_system(self, text: str, role: str = "sys") -> None:
        pal = self._engine.palette
        color = {"sys": pal.sys, "danger": pal.danger, "warn": pal.warn}.get(role, pal.sys)
        self._sys_rows.append((text, color))
        self.append_lines([(text, color, 0, -1)])

    def clear_log(self) -> None:
        self._pending.clear()
        self._sys_rows.clear()
        self.clear()
        self._dropped = 0

    def _redraw(self) -> None:
        """重画内容但保留系统行（跟 clear_log 的区别就在这）。"""
        self._pending.clear()
        self.clear()
        self._dropped = 0
        self._pending.extend((text, color, 0, -1) for text, color in self._sys_rows)

    def save_to_file(self, path: str | Path) -> int:
        text = self.toPlainText()
        Path(path).write_text(text, encoding="utf-8", errors="replace")
        return len(text)

    # ------------------------------------------------------------------ 渲染
    def _flush(self) -> None:
        if not self._pending:
            return
        batch = [self._pending.popleft() for _ in range(min(len(self._pending), FLUSH_BATCH))]
        html = self._render(batch)

        doc = self.document()
        cursor = QTextCursor(doc)
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not doc.isEmpty():
            # 不先插一个 block 的话，新的 <p> 会并到最后一行里
            cursor.insertBlock()
        cursor.insertHtml(html)
        self._attach_slots(batch)

        if self._view.autoscroll:
            self.moveCursor(QTextCursor.MoveOperation.End)
            bar = self.verticalScrollBar()
            bar.setValue(bar.maximum())
        if self._pending and not self._timer.isActive():
            self._timer.start()

    def _attach_slots(self, rows: Sequence[tuple[str, str | None, int, int]]) -> None:
        """把 ANSI 槽位挂到刚插入的最后 len(rows) 个 block 上。

        用「从尾部倒数」定位，同时躲开两个偏差：`setMaximumBlockCount` 会在插入过程中
        裁头，`insertHtml` 的第一个 `<p>` 会并进当前 block 而不是新建。新行永远在末尾，
        所以倒数定位一定对。
        """
        doc = self.document()
        if not rows:
            return
        if len(rows) > doc.blockCount():
            rows = rows[-doc.blockCount() :]
        start = doc.blockCount() - len(rows)
        for i, row in enumerate(rows):
            slot = row[3]
            if slot >= 0:
                block = doc.findBlockByNumber(start + i)
                if block.isValid():
                    block.setUserState(slot)

    def _render(self, batch: Sequence[tuple[str, str | None, int, int]]) -> str:
        pal = self._engine.palette
        ts_color = pal.ts
        ansi = pal.ansi
        parts: list[str] = []
        for text, color, ts_len, slot in batch:
            if color is None:
                color = ansi[slot] if 0 <= slot < len(ansi) else pal.text
            if ts_len and len(text) > ts_len:
                ts = escape(text[:ts_len])
                rest = escape(text[ts_len:])
                parts.append(
                    f'<p style="white-space:pre;color:{color}">'
                    f'<span style="color:{ts_color}">{ts}</span>{rest}</p>'
                )
            else:
                body = escape(text) or " "
                parts.append(f'<p style="white-space:pre;color:{color}">{body}</p>')
        return "".join(parts)

    def retint(self, engine: RuleEngine) -> None:
        """换主题后重新着色整个文档（保住滚动位置、行数和 ANSI 颜色）。"""
        self._apply_font()          # 重插文本前确保字体正确，别把字号写死进 char format
        self._engine = engine
        doc = self.document()
        text = self.toPlainText()
        if not text:
            self.update()
            return
        bar = self.verticalScrollBar()
        keep = bar.value()

        # ANSI 原文早在解码层就剥离了，`toPlainText()` 里没有颜色信息 —— 颜色只能从
        # block 的 userState 里捞。必须在 clear() 之前读完，否则跟着 block 一起没了。
        slots: list[int] = []
        for i in range(doc.blockCount()):
            b = doc.findBlockByNumber(i)
            slots.append(b.userState() if b.isValid() else -1)

        rows: list[tuple[str, str | None, int, int]] = []
        for i, line in enumerate(text.split("\n")):
            m = _TS_RE.match(line)
            ts_len = len(m.group(0)) if m else 0
            slot = slots[i] if i < len(slots) else -1
            if slot >= 0:
                color = None                      # 交给 _render 按新主题翻译
            elif line.startswith(SYS):
                color = engine.palette.sys
            elif line.startswith(ERR):
                color = engine.palette.danger
            elif line.startswith(TIP):
                color = engine.palette.warn
            else:
                color = engine.color_for(line)
            rows.append((line, color, ts_len, slot))

        self.setUpdatesEnabled(False)
        try:
            super().clear()
            doc = self.document()
            doc.setMaximumBlockCount(self._view.max_lines)
            cursor = QTextCursor(doc)
            cursor.movePosition(QTextCursor.MoveOperation.End)
            step = 2000
            for i in range(0, len(rows), step):
                if i:
                    cursor.insertBlock()
                chunk = rows[i : i + step]
                cursor.insertHtml(self._render(chunk))
                self._attach_slots(chunk)
        finally:
            self.setUpdatesEnabled(True)
        bar.setValue(min(keep, bar.maximum()))
        self.update()

    def _on_scroll_action(self, action: int) -> None:
        if not self._view.autoscroll:
            return
        bar = self.verticalScrollBar()
        if bar.value() < bar.maximum() - 2:
            self._view.autoscroll = False
            self.autoScrollInterrupted.emit()

    # ------------------------------------------------------------------ 空状态
    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        if not self.document().isEmpty():
            return
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.viewport().width(), self.viewport().height()
        icon_px = 34
        top = h / 2 - 44

        faded = self._connected
        plug = icons.pixmap("plug", theme.color("text.faint"), icon_px)
        p.setOpacity(0.16 if faded else 0.28)
        p.drawPixmap(
            QRectF((w - icon_px) / 2, top, icon_px, icon_px),
            plug,
            QRectF(0, 0, plug.width(), plug.height()),
        )
        p.setOpacity(1.0)

        f = self.font()
        f.setFamilies(["Microsoft YaHei UI"])
        f.setPointSize(9)
        p.setFont(f)
        p.setPen(theme.color("text.faint"))
        main = "等待数据…" if faded else "未连接"
        p.drawText(QRectF(0, top + icon_px + 10, w, 20), Qt.AlignmentFlag.AlignHCenter, main)

        mode = "十六进制视图" if self._view.hex_mode else "文本视图"
        base = self._conn_detail if faded else "选择端口后点击「连接」"
        f.setPointSize(8)
        p.setFont(f)
        fm = p.fontMetrics()
        sub = f"{base} · {mode}"
        if fm.horizontalAdvance(sub) > w - 24:
            sub = mode
        if sub and fm.horizontalAdvance(sub) <= w - 24:
            p.drawText(QRectF(12, top + icon_px + 32, w - 24, 18), Qt.AlignmentFlag.AlignHCenter, sub)
        p.end()


class LogToolBar(QWidget):
    """每列一条。窄列会自动折叠，挤出去的项进「⋯」菜单，功能一个都不丢。"""

    changed = Signal()
    clearRequested = Signal()
    saveLogRequested = Signal()
    copyAllRequested = Signal()

    def __init__(self, view: ViewOptions, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view = view
        self._tier = -1

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)

        self.btn_ts = ThemedIconButton("clock", "时间戳：每行前面加 [时:分:秒.毫秒]", checkable=True)
        self.btn_ts.setChecked(view.timestamp)
        self.btn_ts.toggled.connect(self._on_ts)

        # 文本 / 十六进制：一个开关。勾上=文本，取消=十六进制（默认勾上）
        self.btn_abc = ToolTextButton(
            "Abc", "文本显示（取消勾选 = 十六进制，按原始行对齐并带 ASCII 预览）", checkable=True
        )
        self.btn_abc.setChecked(not view.hex_mode)
        self.btn_abc.toggled.connect(self._on_abc_toggled)

        self.btn_tx = ThemedIconButton("arrow-up-right", "发送回显：把发出去的数据也显示在日志里", checkable=True)
        self.btn_tx.setChecked(view.show_tx)
        self.btn_tx.toggled.connect(self._on_tx)

        self.combo_enc = DarkComboBox()
        self.combo_enc.setFixedHeight(26)
        self.combo_enc.setMinimumWidth(70)
        self.combo_enc.setMaximumWidth(200)
        self.combo_enc.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.combo_enc.setToolTip("接收数据的字符编码")
        for label, value in ENCODINGS:
            self.combo_enc.addItem(label, value)
        self._select_encoding(view.encoding)
        self.combo_enc.currentIndexChanged.connect(self._on_encoding)

        self.btn_scroll = ThemedIconButton("pin-bottom", "自动滚屏：跟随最新一行（手动上翻会自动关闭）", checkable=True)
        self.btn_scroll.setChecked(view.autoscroll)
        self.btn_scroll.toggled.connect(self._on_scroll)

        self.btn_clear = ThemedIconButton("trash", "清空当前列的日志")
        self.btn_clear.clicked.connect(self.clearRequested)

        self.btn_more = ThemedIconButton("more-h", "更多设置（字号、行数上限、保存日志…）")
        self.btn_more.clicked.connect(self._show_menu)

        for btn in (self.btn_ts, self.btn_tx, self.btn_scroll, self.btn_clear, self.btn_more):
            btn.setFixedSize(28, 28)          # 28 宽才能让窄列多塞一个「清空」

        self._all = [
            self.btn_ts,
            self.btn_abc,
            self.btn_tx,
            self.combo_enc,
            self.btn_scroll,
            self.btn_clear,
            self.btn_more,
        ]
        for w in self._all:
            lay.addWidget(w)
        # 编码框吃掉剩余空间：按钮全部靠左，右侧不留死白，也不会溢出
        lay.setStretch(lay.indexOf(self.combo_enc), 1)

        # 三套配置，从全量往下降级；挤出去的项都在 ⋯ 菜单里
        self._configs = {
            "full": [self.btn_ts, self.btn_abc, self.btn_tx, self.combo_enc,
                     self.btn_scroll, self.btn_clear, self.btn_more],
            "medium": [self.btn_ts, self.btn_abc, self.combo_enc,
                       self.btn_scroll, self.btn_clear, self.btn_more],
            "minimal": [self.btn_ts, self.btn_abc, self.btn_more],
        }
        self._active = ""

    # ------------------------------------------------------------------ 分档
    def apply_width(self, width: int) -> None:
        """按真实控件宽度挑「装得下的最全」那套配置 —— 不写死档位阈值。"""
        for name in ("full", "medium", "minimal"):
            if self._needed(self._configs[name]) <= width:
                break
        else:
            name = "minimal"
        if name == self._active:
            return
        self._active = name
        keep = set(self._configs[name])
        for w in self._all:
            w.setVisible(w in keep)

    def minimumSizeHint(self):  # noqa: N802
        # 只按"最小配置"报最小宽：正常布局的最小宽会把列顶在 278，横向滚动就回来了
        from PySide6.QtCore import QSize

        return QSize(self._needed(self._configs["minimal"]), 30)

    def _needed(self, widgets: list[QWidget]) -> int:
        total = 0
        for w in widgets:
            if w.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding:
                total += w.minimumWidth()          # 能压缩的（编码框）按最小宽算
            else:
                total += w.maximumWidth()          # 定宽控件按实际宽度算，别用 sizeHint（会偏小）
        return total + 2 * max(0, len(widgets) - 1)

    # ------------------------------------------------------------------ 菜单
    def _show_menu(self) -> None:
        t = theme.tokens()
        menu = QMenu(self)

        def add(text: str, icon: str, checked: bool | None = None):
            act = menu.addAction(icons.icon(icon, t["text.dim"], t["text.faint"], 16), text)
            if checked is not None:
                act.setCheckable(True)
                act.setChecked(checked)
            return act

        add("时间戳", "clock", self._view.timestamp).triggered.connect(
            lambda checked: self.btn_ts.setChecked(checked)
        )
        add("十六进制显示", "hex", self._view.hex_mode).triggered.connect(
            lambda checked: self._set_hex(checked)
        )
        add("显示发送回显", "arrow-up-right", self._view.show_tx).triggered.connect(
            lambda checked: self.btn_tx.setChecked(checked)
        )
        add("自动滚屏", "pin-bottom", self._view.autoscroll).triggered.connect(
            lambda checked: self.btn_scroll.setChecked(checked)
        )

        font_menu = menu.addMenu("字号")
        for pt in range(9, 21):
            act = font_menu.addAction(f"{pt} pt")
            act.setCheckable(True)
            act.setChecked(pt == self._view.font_size)
            act.triggered.connect(lambda _=False, v=pt: self._set_font(v))

        enc_menu = menu.addMenu("编码")
        for label, value in ENCODINGS:
            act = enc_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(value == self._view.encoding)
            act.triggered.connect(lambda _=False, v=value: self._set_encoding(v))

        menu.addSeparator()
        add("清空日志", "trash").triggered.connect(self.clearRequested)

        menu.addSeparator()
        limit_menu = menu.addMenu("行数上限")
        for n in LINE_LIMITS:
            act = limit_menu.addAction(f"{n // 1000}k 行")
            act.setCheckable(True)
            act.setChecked(n == self._view.max_lines)
            act.triggered.connect(lambda _=False, v=n: self._set_limit(v))
        add("忽略孤立 \\r（进度条刷屏时用）", "check", self._view.ignore_lone_cr).triggered.connect(
            self._on_lone_cr
        )

        menu.addSeparator()
        add("保存日志到文件…", "save", checked=None).triggered.connect(self.saveLogRequested)
        menu.addAction("复制全部内容").triggered.connect(self.copyAllRequested)

        menu.exec(self.btn_more.mapToGlobal(self.btn_more.rect().bottomLeft()))

    # ------------------------------------------------------------------ 槽
    def _on_ts(self, on: bool) -> None:
        self._view.timestamp = on
        self.changed.emit()

    def _on_abc_toggled(self, checked: bool) -> None:
        """勾上=文本，取消=十六进制。"""
        self._set_hex(not checked)

    def _set_hex(self, on: bool) -> None:
        self._view.hex_mode = on
        self.btn_abc.blockSignals(True)
        self.btn_abc.setChecked(not on)
        self.btn_abc.blockSignals(False)
        self.changed.emit()

    def _on_tx(self, on: bool) -> None:
        self._view.show_tx = on
        self.changed.emit()

    def _on_scroll(self, on: bool) -> None:
        self._view.autoscroll = on
        self.changed.emit()

    def _on_encoding(self) -> None:
        self._view.encoding = self.combo_enc.currentData() or "utf-8"
        self.changed.emit()

    def _on_lone_cr(self, checked: bool) -> None:
        self._view.ignore_lone_cr = checked
        self.changed.emit()

    def _set_font(self, pt: int) -> None:
        self._view.font_size = max(9, min(20, pt))
        self.changed.emit()

    def _set_encoding(self, value: str) -> None:
        idx = self.combo_enc.findData(value)
        if idx >= 0:
            self.combo_enc.setCurrentIndex(idx)

    def _set_limit(self, n: int) -> None:
        self._view.max_lines = n
        self.changed.emit()

    def _select_encoding(self, encoding: str) -> None:
        self.combo_enc.blockSignals(True)
        idx = self.combo_enc.findData(encoding)
        self.combo_enc.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_enc.blockSignals(False)

    # ------------------------------------------------------------------ 外部同步
    def refresh(self) -> None:
        for btn, value in (
            (self.btn_ts, self._view.timestamp),
            (self.btn_tx, self._view.show_tx),
            (self.btn_scroll, self._view.autoscroll),
            (self.btn_abc, not self._view.hex_mode),
        ):
            btn.blockSignals(True)
            btn.setChecked(value)
            btn.blockSignals(False)
        self._select_encoding(self._view.encoding)

    def set_autoscroll(self, on: bool) -> None:
        self.btn_scroll.blockSignals(True)
        self.btn_scroll.setChecked(on)
        self.btn_scroll.blockSignals(False)
        self._view.autoscroll = on


__all__ = ["LogView", "LogToolBar", "mono_font"]
