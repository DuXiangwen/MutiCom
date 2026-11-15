"""界面流程自测（不需要串口）：覆盖「新增列 / 端口列表下发 / 删除列 / 配置回填 / 拖拽排序」。

跑法：
    C:\\Python314\\python.exe tools\\selftest_ui.py
"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from muticom.app_config import AppConfig, ColumnConfig, ConfigStore, PortParams  # noqa: E402
from muticom.main_window import MainWindow  # noqa: E402
from muticom.ui_kit import apply_theme  # noqa: E402


def _send_mouse(w, etype, global_pos: QPoint) -> None:
    local = QPointF(w.mapFromGlobal(global_pos))
    ev = QMouseEvent(
        etype,
        local,
        QPointF(global_pos),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(w, ev)


def drag_header(col, target_global_x: int) -> None:
    """按住列头拖到指定的全局 x 坐标再松手（含 8px 阈值判定）。"""
    h = col._header
    start = h.mapToGlobal(QPoint(h.width() // 2, h.height() // 2))
    y = start.y()
    _send_mouse(h, QEvent.Type.MouseButtonPress, start)
    _send_mouse(h, QEvent.Type.MouseMove, QPoint(start.x() + 14, y))   # 先越过阈值
    _send_mouse(h, QEvent.Type.MouseMove, QPoint(target_global_x, y))
    _send_mouse(h, QEvent.Type.MouseButtonRelease, QPoint(target_global_x, y))


def pump(app: QApplication, seconds: float = 0.3) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.006)


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setApplicationName("MutiComUITest")
    app.setOrganizationName("dx1")
    apply_theme(app)

    store = ConfigStore(Path(tempfile.mkdtemp()) / "config.json")
    win = MainWindow(store, AppConfig(columns=[ColumnConfig()]))
    win.resize(1400, 860)
    win.show()
    pump(app, 0.6)

    fails: list[str] = []
    first = win.area.columns[0]
    total = first.panel.combo_port.count()
    print(f"[0] 初始 1 列，端口下拉 {total} 项，当前 = {first.panel.combo_port.currentText()!r}")
    if total < 2:
        fails.append(f"初始列没有拿到端口列表（{total} 项）")

    # ---- 1. 工具栏「+ 新增串口」后，新列必须立刻有端口列表 ----------------
    win.btn_add.click()
    pump(app, 0.6)
    new = win.area.columns[-1]
    n = new.panel.combo_port.count()
    text = new.panel.combo_port.currentText()
    print(f"[1] 新增列：下拉 {n} 项，当前 = {text!r}，列头 = {new.title!r}")
    if n < 2:
        fails.append(f"新增列的下拉是空的（{n} 项）—— 端口列表没下发")
    if not new.panel.params().port:
        fails.append("新增列没有选中任何端口")
    if "串口" in new.title:
        fails.append(f"新增列列头还是占位文字：{new.title!r}")

    # ---- 2. 再连加两列，每列都要有列表，且尽量挑不同的口 ----------------
    for _ in range(2):
        win.btn_add.click()
        pump(app, 0.4)
    picked: list[str] = []
    for i, col in enumerate(win.area.columns, start=1):
        c = col.panel.combo_port.count()
        picked.append(col.panel.params().port)
        print(f"[2] 第 {i} 列：{c} 项 / 选中的口 {picked[-1]!r} / {col.title!r}")
        if c < 2:
            fails.append(f"第 {i} 列下拉为空（{c} 项）")
    # 端口不够时允许重复，够的时候必须错开
    available = win.area.columns[0].panel.combo_port.count()
    if available >= len(picked) and len(set(picked)) != len(picked):
        fails.append(f"新增列挑了重复的端口：{picked}（可用 {available} 个）")

    # ---- 3. 删除中间列 -------------------------------------------------
    before = len(win.area.columns)
    win.area.remove_column(win.area.columns[1].cfg.uid)
    pump(app, 0.4)
    print(f"[3] 删列：{before} -> {len(win.area.columns)}")
    if len(win.area.columns) != before - 1:
        fails.append("删列没生效")
    for col in win.area.columns:
        col.panel.combo_port.count() >= 2 or fails.append("删列后剩下的列下拉空了")

    # ---- 4. 配置回填：指定端口要能选中 ---------------------------------
    cfg = AppConfig(columns=[ColumnConfig(params=PortParams(port="COM254")),
                             ColumnConfig(params=PortParams(port="COM25"))])
    win2 = MainWindow(ConfigStore(Path(tempfile.mkdtemp()) / "c2.json"), cfg)
    win2.resize(1200, 800)
    win2.show()
    pump(app, 0.8)
    for col, want in zip(win2.area.columns, ("COM254", "COM25")):
        got = col.panel.params().port
        print(f"[4] 配置回填：期望 {want}，实际 {got}，列头 {col.title!r}")
        if got != want:
            fails.append(f"配置里的端口没回填：期望 {want} 实际 {got}")
        if col.panel.combo_port.count() < 2:
            fails.append("回填后下拉没有列表")
    win2.close()
    pump(app, 0.5)

    # ---- 5. 拖拽排序 ---------------------------------------------------
    win3 = MainWindow(
        ConfigStore(Path(tempfile.mkdtemp()) / "c3.json"),
        AppConfig(columns=[ColumnConfig(params=PortParams(port=p))
                           for p in ("COM253", "COM254", "COM25", "COM10")]),
    )
    win3.resize(1400, 800)
    win3.show()
    pump(app, 0.8)
    area = win3.area
    before_order = [c.cfg.uid for c in area.columns]
    if len(before_order) != 4:
        fails.append(f"拖拽测试需要 4 列，实际 {len(before_order)}")

    # 把第 1 列拖到第 3 列的右半边 → 期望顺序 [2,3,1,4]
    third = area.columns[2]
    target_x = third.mapToGlobal(QPoint(third.width() - 6, 0)).x()
    drag_header(area.columns[0], target_x)
    pump(app, 0.3)
    after_order = [c.cfg.uid for c in area.columns]
    print(f"[5] 拖拽 1->3 后：{before_order.index(after_order[0])} 列打头，顺序 {after_order}")
    expect = [before_order[1], before_order[2], before_order[0], before_order[3]]
    if after_order != expect:
        fails.append(f"拖到第 3 列右侧后顺序不对：{after_order}（期望 {expect}）")

    # 再把最后一列拖到最前面
    last_col = area.columns[-1]
    first_col = area.columns[0]
    left_x = first_col.mapToGlobal(QPoint(6, 0)).x()
    drag_header(last_col, left_x)
    pump(app, 0.3)
    final_order = [c.cfg.uid for c in area.columns]
    expect2 = [expect[3], expect[0], expect[1], expect[2]]
    print(f"[5] 再拖末列到最前：{final_order}")
    if final_order != expect2:
        fails.append(f"拖到最前顺序不对：{final_order}（期望 {expect2}）")

    # 原位拖（移动距离没到阈值）不该改顺序
    hold = [c.cfg.uid for c in area.columns]
    h = area.columns[0]._header
    same = h.mapToGlobal(QPoint(h.width() // 2, h.height() // 2))
    _send_mouse(h, QEvent.Type.MouseButtonPress, same)
    _send_mouse(h, QEvent.Type.MouseButtonRelease, QPoint(same.x() + 3, same.y()))
    pump(app, 0.2)
    if [c.cfg.uid for c in area.columns] != hold:
        fails.append("没超过 8px 阈值却触发了重排（点击被误判成拖拽）")
    else:
        print("[5] 小幅移动没有误触发拖拽 ✓")

    # 顺序要能存进配置
    area.columnMoved.emit()
    pump(app, 0.3)
    saved = [c.uid for c in win3.cfg.columns]
    if saved != final_order:
        fails.append(f"配置里的列顺序没同步：{saved} vs {final_order}")
    win3.close()
    pump(app, 0.4)

    win.close()
    pump(app, 0.6)

    print("\n================")
    if fails:
        for f in fails:
            print("  [FAIL]", f)
        return 1
    print("  界面流程测试通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
