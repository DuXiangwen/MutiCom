"""UI 冒烟测试：把主窗口渲染成 PNG，用来快速检查配色/布局。

默认走屏幕渲染（off-screen 平台下 Windows 字体枚举不全，中文会渲染成方框，看不出效果）。
需要无界面时设 MUTICOM_OFFSCREEN=1。

用法：
    C:\\Python314\\python.exe tools\\smoke_shot.py [输出文件] [列数] [宽] [高] [light|dark]
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

if os.environ.get("MUTICOM_OFFSCREEN") == "1":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from muticom.app_config import AppConfig, ColumnConfig, ConfigStore, PortParams  # noqa: E402
from muticom.main_window import MainWindow  # noqa: E402
from muticom.ui_kit import apply_theme  # noqa: E402

PORTS = ["COM253", "COM254", "COM25", "COM10", "COM3", "COM28", "COM99", "COM98"]

SAMPLE = [
    "[00:04:14.633][I][MAIN] comm mode -> MODBUS",
    "[00:04:15.635][I][DSPTX] rx[a1] n=1: 43 00 00 00 00 00 00 00",
    "[00:04:15.667][W][YMODEM] block0 nak, retry=1",
    "[00:04:15.667][W][DSPTX] downlink fail (ymodem session): UPGRADE_FAILED",
    "==== ****** boot cpu: scid log ready FLASH FPGA ****** ====",
    "[CPU2] boot cpu2: init done..",
    "app check: head crc error, base=0x88000, rc=4",
    "upgrade check: no pending file",
    "ym: block0 unknown file, cancel",
    "中文日志：升级失败，重试第 3 次",
    "温度 26.5C 电压 3.31V 电流 1.02A",
    "init done, ok",
]


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else PROJECT / "build" / "shot.png")
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    w = int(sys.argv[3]) if len(sys.argv) > 3 else 1360
    h = int(sys.argv[4]) if len(sys.argv) > 4 else 760
    theme_name = sys.argv[5] if len(sys.argv) > 5 else "light"
    out.parent.mkdir(parents=True, exist_ok=True)

    app = QApplication(sys.argv[:1])
    app.setApplicationName("MutiCom")
    app.setOrganizationName("dx1")
    apply_theme(app, theme_name)

    store = ConfigStore(Path(tempfile.mkdtemp()) / "config.json")
    cfg = AppConfig(
        theme=theme_name,
        columns=[ColumnConfig(params=PortParams(port=PORTS[i % len(PORTS)])) for i in range(n)],
    )
    win = MainWindow(store, cfg)
    win.resize(w, h)
    win.show()

    for col in win.area.columns:
        engine = col.log._engine
        col.log.append_lines(
            [(t, engine.color_for(t), 15 if t.startswith("[00:04") else 0, -1) for t in SAMPLE]
        )

    def shoot() -> None:
        win.grab().save(str(out))
        bar = win.area.horizontalScrollBar()
        widths = [c.width() for c in win.area.columns]
        print(f"saved {out.name}  {w}x{h}  列数={len(widths)}  列宽={widths}  横向滚动max={bar.maximum()}")
        app.quit()

    QTimer.singleShot(700, shoot)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
