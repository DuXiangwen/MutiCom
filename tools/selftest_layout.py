"""布局自检：在多种列宽 / 连接态 / 折叠态下，检查有没有文字被裁。

判定方式：拿控件**真实的文字区宽度**（QStyle 子控件矩形 / contentsRect），跟
`fontMetrics().horizontalAdvance(text)` 比。下拉框按"最长条目"判，不是当前项。

打 `elideOk` 属性的控件（端口下拉、标题、统计、摘要）允许省略，但要求：
省略后至少还剩 4 个字、且带 tooltip。其余控件必须完整显示。

跑法：
    C:\\Python314\\python.exe tools\\selftest_layout.py [--width 240] [--verbose]
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QCheckBox,
    QComboBox,
    QLabel,
    QPushButton,
    QStyle,
    QStyleOptionButton,
    QStyleOptionComboBox,
    QWidget,
)

from muticom.app_config import AppConfig, ColumnConfig, ConfigStore, PortParams  # noqa: E402
from muticom.main_window import MainWindow  # noqa: E402
from muticom.ui_kit import ElideLabel, apply_theme  # noqa: E402

WIDTHS = (168, 200, 240, 270, 300, 352, 531)
CHROME_HIDDEN = {"qt_scrollarea_viewport", "qt_scrollarea_hcontainer", "qt_scrollarea_vcontainer"}


def pump(app: QApplication, seconds: float = 0.12) -> None:
    import time

    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.004)


# --------------------------------------------------------------------------- 取真实文字区


def text_area(w: QWidget) -> int:
    """控件能用来画文字的净宽。"""
    if isinstance(w, QComboBox):
        if w.isEditable() and w.lineEdit() is not None:
            return w.lineEdit().contentsRect().width()
        opt = QStyleOptionComboBox()
        w.initStyleOption(opt)
        rect = w.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, opt, QStyle.SubControl.SC_ComboBoxEditField, w)
        return rect.width()
    if isinstance(w, QPushButton):
        opt = QStyleOptionButton()
        w.initStyleOption(opt)
        return w.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, opt, w).width()
    if isinstance(w, QCheckBox):
        return w.width() - 22
    return w.contentsRect().width()


def needed_text(w: QWidget) -> str:
    if isinstance(w, QComboBox):
        texts = [w.itemText(i) for i in range(w.count())]
        if w.isEditable() and w.currentText():
            texts.append(w.currentText())
        return max(texts, key=len) if texts else ""
    if isinstance(w, (QPushButton, QLabel, QCheckBox)):
        return w.text()
    return ""


def elide_ok(w: QWidget) -> bool:
    return isinstance(w, ElideLabel) or bool(w.property("elideOk"))


# --------------------------------------------------------------------------- 检查


def check_widget(w: QWidget, ctx: str, fails: list[str]) -> None:
    text = needed_text(w)
    if not text:
        return
    avail = text_area(w)
    need = w.fontMetrics().horizontalAdvance(text)
    if elide_ok(w):
        shown = w.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, avail)
        ok = avail > 0 and shown and len(shown) >= 4 and bool(w.toolTip())
        if not ok:
            fails.append(f"{ctx}  {w.__class__.__name__}#{w.objectName() or '-'} 可省略但只剩 {shown!r} (avail={avail})")
        return
    if need > avail:
        fails.append(
            f"{ctx}  {w.__class__.__name__}#{w.objectName() or '-'}  wid={w.width()} need={need} avail={avail} diff={avail - need}"
        )


def walk(root: QWidget):
    for w in root.findChildren(QWidget):
        if not w.isVisible() or w.objectName() in CHROME_HIDDEN:
            continue
        if w.width() <= 1 or w.height() <= 1:
            continue
        yield w


def check_toolbar(col, ctx: str, fails: list[str]) -> None:
    bar = col.toolbar
    kids = [w for w in walk(bar) if w.parent() is bar]
    total = sum(w.width() for w in kids) + 2 * max(0, len(kids) - 1)
    if total > bar.width():
        fails.append(f"{ctx}  toolbar 溢出：控件合计 {total} > 可用 {bar.width()}")
    if kids:
        right = max(w.x() + w.width() for w in kids)
        if right > bar.width():
            fails.append(f"{ctx}  toolbar 右边越界 {right} > {bar.width()}")


def main() -> int:
    width_arg = None
    if "--width" in sys.argv:
        width_arg = int(sys.argv[sys.argv.index("--width") + 1])
    widths = (width_arg,) if width_arg else WIDTHS

    app = QApplication(sys.argv[:1])
    app.setApplicationName("MutiComLayoutTest")
    app.setOrganizationName("dx1")
    apply_theme(app, "light")

    # 直接拿一列自己量：塞进 MainWindow 的话，顶栏按钮会把窗口最小宽顶到 ~294，
    # 168/200/240 这几档根本到不了。
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from muticom.colorizer import LogPalette, RuleEngine
    from muticom.port_scanner import PortScanner
    from muticom.session_column import SessionColumn

    host = QWidget()
    host.resize(widths[0], 760)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(SessionColumn(ColumnConfig(params=PortParams(port="COM253")),
                                RuleEngine([], LogPalette.from_tokens(__import__("muticom.theme", fromlist=["tokens"]).tokens()))))
    col = lay.itemAt(0).widget()
    host.show()
    pump(app, 0.4)
    col.set_ports(PortScanner.current(), {})
    fails: list[str] = []
    cases = 0
    widgets_checked = 0

    for width in list(widths) + list(reversed(widths)):     # 升序 + 降序，查档位切换迟滞
        host.resize(width, 760)       # 列直接铺满 host，宽度精确可控
        pump(app, 0.18)
        actual = col.width()
        for conn in (False, True):
            col._connected = conn                      # 列自己的状态位也要跟着走
            col.lbl_live.setVisible(conn and col._tier == 0)
            if conn:
                col.panel.set_connected(True, "COM253")
                col.log.set_connection_state(True, "COM253 115200 8N1")
                col._on_stats(1229, 340, 1229.0)
            else:
                col.panel.set_connected(False)
                col.log.set_connection_state(False)
                col._on_stats(0, 0, 0.0)
            pump(app, 0.1)

            for collapsed in (False, True):
                col.panel.restore_collapsed(collapsed)
                if not collapsed:
                    col.panel.set_auto_collapsed(False)
                pump(app, 0.12)
                cases += 1
                ctx = f"w={actual} tier={'ABC'[col._tier]} conn={int(conn)} collapsed={int(collapsed)}"
                for w in walk(col):
                    widgets_checked += 1
                    check_widget(w, ctx, fails)
                check_toolbar(col, ctx, fails)

                # 结构断言
                if col._tier == 2 and col.minimumSizeHint().width() > 168:
                    culprits = sorted(
                        ((w.minimumSizeHint().width(), w) for w in walk(col)),
                        key=lambda t: -t[0],
                    )[:3]
                    detail = "，".join(f"{w.__class__.__name__}({w.objectName() or '-'})={n}" for n, w in culprits)
                    fails.append(f"{ctx}  列最小宽 {col.minimumSizeHint().width()} > 168（最宽的：{detail}）")
                if conn and col._tier <= 1 and not collapsed and col.lbl_stats.width() <= 0:
                    fails.append(f"{ctx}  lbl_stats 宽度为 0（连接后统计又不见了）")

    # 字体断言：日志必须是等宽、字号跟 view 走、document 跟随。
    # （QSS 的全局字体规则会在 polish 时盖掉 setFont，这里回归过一次：启动后显示微软雅黑 9pt）
    def font_check(tag: str) -> None:
        w = col.log.font()
        d = col.log.document().defaultFont()
        if not any(name in w.family() for name in ("Cascadia", "Consolas", "JetBrains", "Courier")):
            fails.append(f"{tag}  日志字体不是等宽：{w.family()}")
        if w.pointSize() != col.view.font_size:
            fails.append(f"{tag}  日志字号 {w.pointSize()} != 配置 {col.view.font_size}")
        if d.family() != w.family() or d.pointSize() != w.pointSize():
            fails.append(f"{tag}  document 字体没跟随 widget：{d.pointSize()}pt/{d.family()}")

    font_check("启动后")
    apply_theme(app, "dark")
    pump(app, 0.3)
    font_check("切深色后")
    col.toolbar._set_font(14)
    pump(app, 0.3)
    font_check("调到 14pt 后")
    apply_theme(app, "light")
    pump(app, 0.3)
    font_check("切回浅色后(应保持 14pt)")
    col.toolbar._set_font(10)
    pump(app, 0.2)

    # 折叠收益
    host.resize(531, 760)
    pump(app, 0.25)
    col.panel.restore_collapsed(False)
    pump(app, 0.2)
    expanded_h = col.log.height()
    col.panel.restore_collapsed(True)
    pump(app, 0.2)
    collapsed_h = col.log.height()
    delta = collapsed_h - expanded_h

    print()
    if fails:
        for f in fails:
            print("  [FAIL]", f)
    print("=" * 60)
    print(f"检查 {cases} 组 / {widgets_checked} 个控件；折叠后日志多 {delta}px")
    if fails:
        print(f"失败 {len(fails)} 条")
        host.close()
        return 1
    print("全部通过")
    host.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
