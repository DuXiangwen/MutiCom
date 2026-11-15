"""入口：QApplication + 主题 + 主窗口。"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME, ORG_NAME
from .app_config import ConfigStore
from .main_window import MainWindow
from .ui_kit import apply_theme


def _install_excepthook() -> None:
    """pythonw 启动时没有控制台，异常要落盘 + 弹窗，否则崩了完全没线索。"""

    def hook(exc_type, exc, tb) -> None:
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        try:
            log = Path(ConfigStore().path).parent / "error.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError:
            pass
        sys.stderr.write(text)
        try:
            QMessageBox.critical(None, "MutiCom 出错", f"{exc_type.__name__}: {exc}\n\n详细堆栈已写入 error.log")
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = hook


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationDisplayName(f"{APP_NAME} · 多路串口日志监视")

    # 主题要先读配置再应用，否则"上次选的深色"重启就丢了
    store = ConfigStore()
    cfg = store.load()
    apply_theme(app, cfg.theme)
    _install_excepthook()

    win = MainWindow(store, cfg)
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
