"""两个管理对话框：着色规则、预设指令。"""

from __future__ import annotations

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .app_config import LineEnding, Preset
from .colorizer import ColorRule, LogPalette, RuleEngine, validate

PREVIEW_LINES = [
    "[00:04:14.633][I][MAIN] comm mode -> MODBUS",
    "[00:04:15.667][W][YMODEM] block0 nak, retry=1",
    "[00:04:15.667][DSPTX] downlink fail (ymodem session): UPGRADE_FAILED",
    "==== ****** boot cpu: scid log ready ****** ====",
    "boot cpu: step4 stay in boot, ymodem on scib",
    "ym: block0 unknown file, cancel",
    "upgrade check: no pending file",
    "app check: head crc error, base=0x88000, rc=4",
    "init done, ok",
]

ENDING_LABELS = {LineEnding.CRLF: "\\r\\n", LineEnding.LF: "\\n", LineEnding.CR: "\\r", LineEnding.NONE: "无"}
LABEL_TO_ENDING = {v: k for k, v in ENDING_LABELS.items()}


def _btn(text: str, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    b.setToolTip(tip)
    b.setFixedHeight(24)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.setAlternatingRowColors(False)
    t.setShowGrid(False)
    t.setColumnWidth(0, 44)
    t.setColumnWidth(1, 110)
    t.setColumnWidth(2, 260)
    t.setColumnWidth(3, 56)
    t.setColumnWidth(4, 84)
    t.horizontalHeader().setStretchLastSection(True)
    return t


def _check_item(checked: bool) -> QTableWidgetItem:
    item = QTableWidgetItem()
    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsSelectable)
    item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
    return item


class RulesDialog(QDialog):
    """着色规则编辑。改动实时反映在下方的预览里。"""

    def __init__(
        self,
        rules: list[ColorRule],
        palette: LogPalette | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("着色规则")
        self.resize(780, 540)
        self._palette = palette or LogPalette.from_tokens(theme.tokens())
        self._rules = [r for r in rules if not r.builtin]

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        hint = QLabel(
            "规则从上往下匹配，命中第一条就停。用户规则优先于内置规则；"
            "内置规则（[E]/[W]/[I]/[D]/[V] 级别标记、error/fail、warn、ok/done、分隔行）不用在这里配。"
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        self.table = _table(["启用", "名称", "匹配内容（关键词或正则）", "正则", "颜色"])
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        outer.addWidget(self.table, 1)

        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.btn_add = _btn("新增", "新增一条规则")
        self.btn_del = _btn("删除", "删除选中规则")
        self.btn_up = _btn("上移", "提高优先级")
        self.btn_down = _btn("下移", "降低优先级")
        self.btn_color = _btn("选颜色…", "给选中规则挑一个颜色")
        self.btn_clear = _btn("清空全部", "删除所有自定义规则")
        for b in (self.btn_add, self.btn_del, self.btn_up, self.btn_down, self.btn_color, self.btn_clear):
            tools.addWidget(b)
        tools.addStretch(1)
        outer.addLayout(tools)

        preview_title = QLabel("预览")
        preview_title.setObjectName("sectionTitle")
        outer.addWidget(preview_title)
        self.preview = QPlainTextEdit()
        self.preview.setObjectName("logView")
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(170)
        self.preview.setFont(self.font())
        outer.addWidget(self.preview)

        self.lbl_warn = QLabel("")
        self.lbl_warn.setObjectName("errorHint")
        outer.addWidget(self.lbl_warn)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        ok = _btn("保存")
        ok.setObjectName("primary")
        ok.setFixedWidth(84)
        cancel = _btn("取消")
        cancel.setFixedWidth(84)
        bottom.addWidget(cancel)
        bottom.addWidget(ok)
        outer.addLayout(bottom)

        self.btn_add.clicked.connect(self._add_row)
        self.btn_del.clicked.connect(self._del_row)
        self.btn_up.clicked.connect(lambda: self._move_row(-1))
        self.btn_down.clicked.connect(lambda: self._move_row(+1))
        self.btn_color.clicked.connect(self._pick_color)
        self.btn_clear.clicked.connect(self._clear_all)
        self.table.itemChanged.connect(lambda *_: self._refresh_preview())
        self.table.cellDoubleClicked.connect(self._on_double_click)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

        for rule in self._rules:
            self._add_row(rule)
        self._refresh_preview()

    # ------------------------------------------------------------------ 行操作
    def _add_row(self, rule: ColorRule | None = None) -> None:
        rule = rule or ColorRule(name="新规则", pattern="", color=self._palette.info, regex=True, enabled=True)
        row = self.table.rowCount()
        self.table.blockSignals(True)  # 建行过程中会触发 itemChanged，那时单元格还没填全
        self.table.insertRow(row)
        self.table.setItem(row, 0, _check_item(rule.enabled))
        self.table.setItem(row, 1, QTableWidgetItem(rule.name))
        self.table.setItem(row, 2, QTableWidgetItem(rule.pattern))
        self.table.setItem(row, 3, _check_item(rule.regex))
        color_item = QTableWidgetItem(rule.color)
        color_item.setData(Qt.ItemDataRole.UserRole, rule.color)
        color_item.setBackground(QColor(rule.color))
        color_item.setForeground(QColor("#101010"))
        color_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        self.table.setItem(row, 4, color_item)
        self.table.blockSignals(False)
        self.table.selectRow(row)
        self._refresh_preview()

    def _current_row(self) -> int:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return rows[0].row() if rows else -1

    def _del_row(self) -> None:
        row = self._current_row()
        if row >= 0:
            self.table.removeRow(row)
            self._refresh_preview()

    def _move_row(self, delta: int) -> None:
        row = self._current_row()
        target = row + delta
        if row < 0 or target < 0 or target >= self.table.rowCount():
            return
        self._swap_rows(row, target)
        self.table.selectRow(target)

    def _swap_rows(self, a: int, b: int) -> None:
        data_a = self._row_data(a)
        data_b = self._row_data(b)
        self._write_row(a, data_b)
        self._write_row(b, data_a)
        self._refresh_preview()

    def _row_data(self, row: int) -> tuple:
        return (
            self.table.item(row, 0).checkState() == Qt.CheckState.Checked,
            self.table.item(row, 1).text(),
            self.table.item(row, 2).text(),
            self.table.item(row, 3).checkState() == Qt.CheckState.Checked,
            self.table.item(row, 4).data(Qt.ItemDataRole.UserRole),
        )

    def _write_row(self, row: int, data: tuple) -> None:
        enabled, name, pattern, regex, color = data
        self.table.blockSignals(True)
        self.table.item(row, 0).setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
        self.table.item(row, 1).setText(name)
        self.table.item(row, 2).setText(pattern)
        self.table.item(row, 3).setCheckState(Qt.CheckState.Checked if regex else Qt.CheckState.Unchecked)
        item = self.table.item(row, 4)
        item.setText(color)
        item.setData(Qt.ItemDataRole.UserRole, color)
        item.setBackground(QColor(color))
        self.table.blockSignals(False)

    def _clear_all(self) -> None:
        self.table.setRowCount(0)
        self._refresh_preview()

    def _on_double_click(self, row: int, col: int) -> None:
        if col == 4:
            self.table.selectRow(row)
            self._pick_color()

    def _pick_color(self) -> None:
        row = self._current_row()
        if row < 0:
            return
        current = QColor(self.table.item(row, 4).data(Qt.ItemDataRole.UserRole) or "#ffffff")
        color = QColorDialog.getColor(current, self, "选择颜色")
        if color.isValid():
            item = self.table.item(row, 4)
            item.setText(color.name())
            item.setData(Qt.ItemDataRole.UserRole, color.name())
            item.setBackground(color)
            item.setForeground(QColor("#101010"))
            self._refresh_preview()

    # ------------------------------------------------------------------ 结果
    def rules(self) -> list[ColorRule]:
        return self._collect()

    def _row_items(self, row: int) -> list[QTableWidgetItem | None]:
        return [self.table.item(row, c) for c in range(5)]

    def _collect(self) -> list[ColorRule]:
        out: list[ColorRule] = []
        for row in range(self.table.rowCount()):
            items = self._row_items(row)
            if any(item is None for item in items):
                continue
            pattern = items[2].text().strip()
            if not pattern:
                continue
            out.append(
                ColorRule(
                    name=items[1].text().strip() or pattern,
                    pattern=pattern,
                    color=items[4].data(Qt.ItemDataRole.UserRole) or "#d4d4d4",
                    regex=items[3].checkState() == Qt.CheckState.Checked,
                    enabled=items[0].checkState() == Qt.CheckState.Checked,
                    builtin=False,
                )
            )
        return out

    def _refresh_preview(self) -> None:
        problems: list[str] = []
        for row in range(self.table.rowCount()):
            items = self._row_items(row)
            if any(item is None for item in items):
                continue
            pattern = items[2].text().strip()
            regex = items[3].checkState() == Qt.CheckState.Checked
            ok, msg = validate(pattern, regex)
            items[2].setForeground(QColor(theme.color("danger")) if pattern and not ok else QColor(theme.color("text")))
            if pattern and not ok:
                problems.append(f"第 {row + 1} 行：{msg}")
        # 自定义颜色在日志底色上可能看不清，给个提示（不强行改用户的选择）
        low: list[str] = []
        for row in range(self.table.rowCount()):
            items = self._row_items(row)
            if any(item is None for item in items):
                continue
            color = items[4].data(Qt.ItemDataRole.UserRole)
            if color and theme.contrast_ratio(color, theme.color("bg.log")) < 3.0:
                low.append(f"第 {row + 1} 行「{items[1].text() or '未命名'}」在当前主题下不易辨认")
        self.lbl_warn.setText("  ".join(problems + low))

        engine = RuleEngine(self._collect(), self._palette)
        self.preview.clear()
        html = "".join(
            f'<p style="white-space:pre;color:{engine.color_for(line)}">{escape(line)}</p>'
            for line in PREVIEW_LINES
        )
        self.preview.appendHtml(html)


class PresetsDialog(QDialog):
    """预设指令管理。全局一份，按分组显示。"""

    def __init__(self, presets: list[Preset], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("预设指令")
        self.resize(720, 440)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        hint = QLabel("一键发送的常用命令。Hex 列勾上时「内容」按十六进制解析，否则按当前列编码发送。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        self.table = _table(["名称", "内容", "Hex", "结尾", "分组"])
        self.table.setColumnWidth(0, 120)
        self.table.setColumnWidth(1, 240)
        self.table.setColumnWidth(2, 46)
        self.table.setColumnWidth(3, 78)
        self.table.setColumnWidth(4, 100)
        outer.addWidget(self.table, 1)

        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.btn_add = _btn("新增")
        self.btn_del = _btn("删除")
        self.btn_up = _btn("上移")
        self.btn_down = _btn("下移")
        for b in (self.btn_add, self.btn_del, self.btn_up, self.btn_down):
            tools.addWidget(b)
        tools.addStretch(1)
        outer.addLayout(tools)

        bottom = QHBoxLayout()
        bottom.addStretch(1)
        ok = _btn("保存")
        ok.setObjectName("primary")
        ok.setFixedWidth(84)
        cancel = _btn("取消")
        cancel.setFixedWidth(84)
        bottom.addWidget(cancel)
        bottom.addWidget(ok)
        outer.addLayout(bottom)

        self.btn_add.clicked.connect(lambda: self._add_row(None))
        self.btn_del.clicked.connect(self._del_row)
        self.btn_up.clicked.connect(lambda: self._move_row(-1))
        self.btn_down.clicked.connect(lambda: self._move_row(+1))
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)

        for p in presets:
            self._add_row(p)

    def _add_row(self, preset: Preset | None) -> None:
        preset = preset or Preset(name="新指令", payload="", ending=LineEnding.CRLF)
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(preset.name))
        self.table.setItem(row, 1, QTableWidgetItem(preset.payload))
        self.table.setItem(row, 2, _check_item(preset.hex))
        self.table.setItem(row, 3, QTableWidgetItem(ENDING_LABELS.get(preset.ending, "\\r\\n")))
        self.table.setItem(row, 4, QTableWidgetItem(preset.group))
        self.table.selectRow(row)

    def _current_row(self) -> int:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return rows[0].row() if rows else -1

    def _del_row(self) -> None:
        row = self._current_row()
        if row >= 0:
            self.table.removeRow(row)

    def _move_row(self, delta: int) -> None:
        row = self._current_row()
        target = row + delta
        if row < 0 or target < 0 or target >= self.table.rowCount():
            return
        a = [self._cell_text(row, c) for c in range(5)]
        b = [self._cell_text(target, c) for c in range(5)]
        for c, value in enumerate(b):
            self._set_cell(row, c, value)
        for c, value in enumerate(a):
            self._set_cell(target, c, value)
        self.table.selectRow(target)

    def _cell_text(self, row: int, col: int) -> tuple[str, bool]:
        item = self.table.item(row, col)
        if col == 2:
            return item.text(), item.checkState() == Qt.CheckState.Checked
        return item.text(), False

    def _set_cell(self, row: int, col: int, value: tuple[str, bool]) -> None:
        text, checked = value
        item = self.table.item(row, col)
        item.setText(text)
        if col == 2:
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def presets(self) -> list[Preset]:
        out: list[Preset] = []
        for row in range(self.table.rowCount()):
            payload = self.table.item(row, 1).text()
            name = self.table.item(row, 0).text().strip() or payload or "指令"
            ending_label = self.table.item(row, 3).text().strip()
            out.append(
                Preset(
                    name=name,
                    payload=payload,
                    hex=self.table.item(row, 2).checkState() == Qt.CheckState.Checked,
                    ending=LABEL_TO_ENDING.get(ending_label, LineEnding.CRLF),
                    group=self.table.item(row, 4).text().strip() or "通用",
                )
            )
        return out


__all__ = ["RulesDialog", "PresetsDialog"]
