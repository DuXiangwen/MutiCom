"""单列的发送区：输入框 + 发送 + 行尾 + Hex + 历史 + 预设。"""

from __future__ import annotations

import re
from typing import Sequence

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .app_config import LineEnding, Preset, SendOptions
from .icons import icons
from . import theme
from .ui_kit import DarkComboBox, ThemedIconButton, width_for_text

_JUNK = re.compile(r"[\s,;:_]")
_HEX_CHARS = set("0123456789abcdefABCDEF")

ENDINGS = (("\\r\\n", LineEnding.CRLF), ("\\n", LineEnding.LF), ("\\r", LineEnding.CR), ("无", LineEnding.NONE))

TIER_C = 2


def parse_hex(text: str) -> bytes | None:
    """宽松解析十六进制：允许空格/逗号/换行，允许 0x 前缀；非法或奇数长度返回 None。"""
    s = _JUNK.sub("", text)
    s = s.replace("0x", "").replace("0X", "")
    if not s or len(s) % 2 or any(c not in _HEX_CHARS for c in s):
        return None
    return bytes.fromhex(s)


class SendEdit(QPlainTextEdit):
    """输入框：Ctrl+Enter 发送，单行内容时 ↑/↓ 翻历史。"""

    sendTriggered = Signal()
    historyStep = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sendEdit")
        self.setPlaceholderText("要发送的内容…  Ctrl+Enter 发送")
        self.setFixedHeight(44)
        self.setTabChangesFocus(True)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        mods = event.modifiers()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and mods & Qt.KeyboardModifier.ControlModifier:
            self.sendTriggered.emit()
            return
        single_line = "\n" not in self.toPlainText()
        if key == Qt.Key.Key_Up and (mods & Qt.KeyboardModifier.ControlModifier or single_line):
            self.historyStep.emit(-1)
            return
        if key == Qt.Key.Key_Down and (mods & Qt.KeyboardModifier.ControlModifier or single_line):
            self.historyStep.emit(1)
            return
        super().keyPressEvent(event)


class SendPanel(QFrame):
    sendRequested = Signal(bytes)
    managePresetsRequested = Signal()

    MAX_HISTORY = 100
    MAX_QUICK = 3

    def __init__(self, opts: SendOptions, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        self._opts = opts
        self._encoding = "utf-8"
        self._history: list[str] = []
        self._hist_idx = -1
        self._presets: list[Preset] = []
        self._tier = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        # -- 预设快发 ---------------------------------------------------
        self._preset_row = QHBoxLayout()
        self._preset_row.setContentsMargins(0, 0, 0, 0)
        self._preset_row.setSpacing(4)
        self._preset_bar = QWidget()
        self._preset_bar.setLayout(self._preset_row)
        self._preset_bar.hide()
        outer.addWidget(self._preset_bar)

        # -- 错误提示（独占一行，不跟控件抢位置）-------------------------
        self.lbl_hint = QLabel("")
        self.lbl_hint.setObjectName("errorHint")
        self.lbl_hint.hide()
        outer.addWidget(self.lbl_hint)

        # -- 输入 + 发送 -------------------------------------------------
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.edit = SendEdit()
        self.edit.setMinimumWidth(56)          # 允许窄列把输入框压扁，否则面板最小宽顶住列
        self.edit.sendTriggered.connect(self._on_send_clicked)
        self.edit.historyStep.connect(self._step_history)
        row.addWidget(self.edit, 1)

        self.btn_send = QPushButton("发送")
        self.btn_send.setObjectName("primary")
        self._send_wide = max(64, width_for_text(self.btn_send, "发送", chrome=26) + 8)
        self._send_narrow = max(54, width_for_text(self.btn_send, "发送", chrome=26) + 6)
        self.btn_send.setFixedWidth(self._send_wide)
        self.btn_send.setFixedHeight(44)
        self.btn_send.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_send.setToolTip("发送（Ctrl+Enter 同效）")
        self.btn_send.clicked.connect(self._on_send_clicked)
        row.addWidget(self.btn_send)
        outer.addLayout(row)

        # -- 行尾 / Hex / 历史 ------------------------------------------
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(6)

        self.lbl_ending = QLabel("行尾")
        self.lbl_ending.setObjectName("fieldLabel")
        self.lbl_ending.setMinimumWidth(self.lbl_ending.fontMetrics().horizontalAdvance("行尾") + 4)
        bottom.addWidget(self.lbl_ending)

        self.combo_ending = DarkComboBox()
        self.combo_ending.setFixedHeight(24)
        self.combo_ending.setFixedWidth(74)
        self.combo_ending.setToolTip("发送时自动追加的行尾")
        for text, value in ENDINGS:
            self.combo_ending.addItem(text, value.value)
        idx = self.combo_ending.findData(opts.line_ending.value)
        self.combo_ending.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_ending.currentIndexChanged.connect(self._on_ending)
        bottom.addWidget(self.combo_ending)

        self.chk_hex = QCheckBox("Hex")
        self.chk_hex.setToolTip("按十六进制发送（与上面日志的 Hex 显示互不影响）")
        self.chk_hex.setChecked(opts.hex_mode)
        self.chk_hex.toggled.connect(self._on_hex)
        bottom.addWidget(self.chk_hex)

        bottom.addStretch(1)

        self.btn_history = ThemedIconButton("history", "发送历史（↑/↓ 也能翻）", size=18)
        self.btn_history.setFixedSize(26, 26)
        self.btn_history.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        bottom.addWidget(self.btn_history)

        outer.addLayout(bottom)

        self._rebuild_history_menu()

    # ------------------------------------------------------------------ 分档
    def apply_tier(self, tier: int) -> None:
        if tier == self._tier:
            return
        self._tier = tier
        compact = tier >= TIER_C
        self.lbl_ending.setVisible(not compact)
        self.combo_ending.setFixedWidth(72 if compact else 74)
        self.chk_hex.setText("" if compact else "Hex")
        self.btn_send.setFixedWidth(self._send_narrow if compact else self._send_wide)
        f = self.btn_send.font()
        f.setPointSize(8 if compact else 9)
        self.btn_send.setFont(f)
        self._preset_bar.setVisible(bool(self._presets) and not compact)
        self._sync_placeholder()

    # ------------------------------------------------------------------ 外部
    def set_encoding(self, encoding: str) -> None:
        self._encoding = encoding

    def set_presets(self, presets: Sequence[Preset]) -> None:
        self._presets = list(presets)
        self._rebuild_preset_row()

    def draft(self) -> str:
        return self.edit.toPlainText()

    def set_draft(self, text: str) -> None:
        self.edit.setPlainText(text or "")

    def focus_input(self) -> None:
        self.edit.setFocus()

    # ------------------------------------------------------------------ 预设
    def _rebuild_preset_row(self) -> None:
        while self._preset_row.count():
            item = self._preset_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        for preset in self._presets[: self.MAX_QUICK]:
            btn = QPushButton(preset.name or preset.payload or "指令")
            btn.setObjectName("chip")
            btn.setToolTip(f"{preset.payload}\n（点击直接发送）")
            btn.setFixedHeight(24)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            btn.clicked.connect(lambda _=False, p=preset: self._send_preset(p))
            self._preset_row.addWidget(btn)

        more = ThemedIconButton("bolt", "预设指令", size=18)
        more.setFixedSize(26, 26)
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        more.setMenu(self._build_preset_menu())
        self._preset_menu_btn = more
        self._preset_row.addWidget(more)
        self._preset_row.addStretch(1)
        self._preset_bar.setVisible(bool(self._presets) and self._tier < TIER_C)

    def _build_preset_menu(self) -> QMenu:
        menu = QMenu(self)
        if self._presets:
            groups: dict[str, list[Preset]] = {}
            for p in self._presets:
                groups.setdefault(p.group or "通用", []).append(p)
            for group, items in groups.items():
                if len(groups) > 1:
                    menu.addSection(group)
                for p in items:
                    act = menu.addAction(p.name or p.payload)
                    act.setToolTip(p.payload)
                    act.triggered.connect(lambda _=False, preset=p: self._send_preset(preset))
            menu.addSeparator()
        else:
            menu.addAction("还没有预设指令").setEnabled(False)
            menu.addSeparator()
        menu.addAction("管理预设…").triggered.connect(self.managePresetsRequested)
        return menu

    def _send_preset(self, preset: Preset) -> None:
        if preset.hex:
            data = parse_hex(preset.payload)
            if data is None:
                self._flash_error("预设内容不是合法的十六进制")
                return
        else:
            data = preset.payload.encode(self._encoding, errors="replace")
            data += preset.ending.value.encode()
        self.sendRequested.emit(data)

    # ------------------------------------------------------------------ 发送
    def _on_send_clicked(self) -> None:
        text = self.edit.toPlainText()
        if not text.strip():
            return
        if self._opts.hex_mode:
            data = parse_hex(text)
            if data is None:
                self._flash_error("Hex 内容不合法（只能是 0-9A-F，且长度为偶数）")
                return
        else:
            data = text.encode(self._encoding, errors="replace")
            data += self._opts.line_ending.value.encode()
        self._push_history(text)
        self.sendRequested.emit(data)

    def _push_history(self, text: str) -> None:
        if text in self._history:
            self._history.remove(text)
        self._history.insert(0, text)
        del self._history[self.MAX_HISTORY :]
        self._hist_idx = -1
        self._rebuild_history_menu()

    def _step_history(self, step: int) -> None:
        if not self._history:
            return
        if self._hist_idx < 0:
            self._hist_idx = 0 if step < 0 else -1
        else:
            self._hist_idx = max(-1, min(len(self._history) - 1, self._hist_idx - step))
        if self._hist_idx < 0:
            return
        self.edit.setPlainText(self._history[self._hist_idx])
        self.edit.moveCursor(QTextCursor.MoveOperation.End)

    def _rebuild_history_menu(self) -> None:
        t = theme.tokens()
        menu = QMenu(self)
        if not self._history:
            menu.addAction("暂无历史").setEnabled(False)
        else:
            for text in self._history[:20]:
                label = text.replace("\n", " ⏎ ")
                if len(label) > 42:
                    label = label[:42] + "…"
                act = menu.addAction(label)
                act.triggered.connect(lambda _=False, value=text: self.edit.setPlainText(value))
            menu.addSeparator()
            act = menu.addAction(icons.icon("trash", t["text.dim"], t["text.faint"], 16), "清空历史")
            act.triggered.connect(self._clear_history)
        self.btn_history.setMenu(menu)

    def _clear_history(self) -> None:
        self._history.clear()
        self._hist_idx = -1
        self._rebuild_history_menu()

    # ------------------------------------------------------------------ 槽
    def _on_ending(self) -> None:
        self._opts.line_ending = LineEnding(self.combo_ending.currentData() or "")

    def _on_hex(self, on: bool) -> None:
        self._opts.hex_mode = on
        self._sync_placeholder()

    def _sync_placeholder(self) -> None:
        if self._tier >= TIER_C:
            self.edit.setPlaceholderText("Hex…" if self._opts.hex_mode else "输入…")
        elif self._opts.hex_mode:
            self.edit.setPlaceholderText("十六进制字节… 如 01 03 00 00 00 01  Ctrl+Enter 发送")
        else:
            self.edit.setPlaceholderText("要发送的内容…  Ctrl+Enter 发送")

    def _flash_error(self, text: str) -> None:
        self.lbl_hint.setText(text)
        self.lbl_hint.show()
        self.edit.setProperty("error", True)
        self.edit.style().unpolish(self.edit)
        self.edit.style().polish(self.edit)
        QTimer.singleShot(2500, self._clear_error)

    def _clear_error(self) -> None:
        self.lbl_hint.clear()
        self.lbl_hint.hide()
        self.edit.setProperty("error", False)
        self.edit.style().unpolish(self.edit)
        self.edit.style().polish(self.edit)

