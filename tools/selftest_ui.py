"""界面流程自测（不需要串口）：覆盖「新增列 / 端口列表下发 / 删除列 / 配置回填」。

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

from PySide6.QtWidgets import QApplication  # noqa: E402

from muticom.app_config import AppConfig, ColumnConfig, ConfigStore, PortParams  # noqa: E402
from muticom.main_window import MainWindow  # noqa: E402
from muticom.ui_kit import apply_theme  # noqa: E402


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
