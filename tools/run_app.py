"""qtpilot 调试入口。

产品入口是 `python -m muticom`，但 qtPilot 的 launcher 只接受「一个脚本路径」，
没法传 `-m` 给 python.exe，所以这里做个等价的最小入口。

顺带做了两件调试专用的事：
1. 自动连 COM254 —— 配合 `fake_log_feeder.py --port COM253` 灌数据，不用手点连接按钮
2. 给主窗口设 objectName —— `qt_ui_screenshot` 不接受裸 objectName，必须有顶层窗口名

启动：
    "C:/Users/HXSXM/.workbuddy/qtpilot-tools/qtstart-pyside-qt6.8" \\
        "C:/Users/HXSXM/Desktop/ExcitingProject/M12_My/dx1_muti_com/tools/run_app.py" 9322
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from muticom import APP_NAME, ORG_NAME  # noqa: E402
from muticom.app_config import AppConfig, ColumnConfig, ConfigStore, PortParams  # noqa: E402
from muticom.main_window import MainWindow  # noqa: E402
from muticom.ui_kit import apply_theme  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationDisplayName(f"{APP_NAME} · 调试")

    store = ConfigStore()
    cfg = AppConfig()
    cfg.columns = [
        ColumnConfig(
            params=PortParams(port="COM254", baudrate=115200),
            auto_connect=True,
        )
    ]
    apply_theme(app, "light")

    win = MainWindow(store, cfg)
    win.setObjectName("mainWindow")      # 只影响调试定位，不写进产品代码
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
