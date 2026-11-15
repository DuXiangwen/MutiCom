"""主窗口：顶栏 + 多列区域 + 底部状态条。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from . import __version__, theme
from .app_config import AppConfig, ConfigStore
from .colorizer import LogPalette, RuleEngine
from .column_area import ColumnArea
from .dialogs import PresetsDialog, RulesDialog
from .icons import icons
from .port_scanner import PortScanner
from .ui_kit import ThemedIconButton, ThemedTextButton, apply_theme


class MainWindow(QMainWindow):
    def __init__(self, store: ConfigStore, cfg: AppConfig) -> None:
        super().__init__()
        self.store = store
        self.cfg = cfg
        self._engine = RuleEngine(cfg.rules, LogPalette.from_tokens(theme.tokens()))

        self.setWindowTitle(f"MutiCom · 多路串口日志监视  v{__version__}")
        self.setMinimumSize(680, 480)
        self.resize(1360, 860)
        if cfg.geometry:
            self.restoreGeometry(QByteArray.fromBase64(cfg.geometry.encode()))

        self._build_ui()

        self.area.set_engine(self._engine)
        for col_cfg in cfg.columns:
            self.area.add_column(col_cfg)
        self.area.restore_sizes(cfg.splitter_sizes, cfg.equal_columns)
        self.area.set_presets(cfg.presets)

        self.scanner = PortScanner(parent=self)
        self.scanner.portsChanged.connect(self.area.apply_ports)
        self.scanner.portsChanged.connect(lambda *_: self._update_status())
        self.scanner.start()
        self.area.apply_ports(self.scanner.ports)

        for col, col_cfg in zip(self.area.columns, cfg.columns):
            if col_cfg.auto_connect:
                QTimer.singleShot(400, col.connect_now)

        self._sync_theme_button()
        self._update_status()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- 顶栏 -------------------------------------------------------
        bar = QWidget()
        bar.setObjectName("topBar")
        bar.setFixedHeight(46)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(16, 0, 12, 0)
        bl.setSpacing(8)

        title = QLabel("MutiCom")
        title.setObjectName("appTitle")
        ver = QLabel(f"v{__version__}")
        ver.setObjectName("versionChip")
        bl.addWidget(title)
        bl.addWidget(ver, 0, Qt.AlignmentFlag.AlignVCenter)
        bl.addSpacing(6)

        divider = QFrame()
        divider.setObjectName("vDivider")
        divider.setFixedSize(1, 20)
        bl.addWidget(divider, 0, Qt.AlignmentFlag.AlignVCenter)
        bl.addSpacing(6)

        self.btn_add = ThemedTextButton("新增串口", "add", "新增一路串口（Ctrl+N）", primary=True)
        self.btn_rules = ThemedTextButton("着色规则", "palette", "管理日志着色规则（Ctrl+T）")
        self.btn_presets = ThemedTextButton("预设指令", "bolt", "管理一键发送的预设命令")
        self.btn_save_all = ThemedTextButton("保存日志", "save", "把当前列日志另存为文本")
        for b in (self.btn_add, self.btn_rules, self.btn_presets, self.btn_save_all):
            bl.addWidget(b)
        bl.addStretch(1)

        self.btn_theme = ThemedIconButton("moon", "切换浅色 / 深色主题", size=18)
        self.btn_theme.setFixedSize(30, 30)
        bl.addWidget(self.btn_theme)

        self.btn_more = ThemedIconButton("more-h", "更多", size=18)
        self.btn_more.setFixedSize(30, 30)
        menu = QMenu(self)
        menu.addAction("让所有列等宽").triggered.connect(lambda: self.area.set_equal_mode(True))
        menu.addSeparator()
        menu.addAction("导出配置…").triggered.connect(self._export_config)
        menu.addAction("导入配置…").triggered.connect(self._import_config)
        menu.addAction("打开配置目录").triggered.connect(self._open_config_dir)
        menu.addSeparator()
        menu.addAction("关于").triggered.connect(self._about)
        self.btn_more.setMenu(menu)
        self.btn_more.setPopupMode(self.btn_more.ToolButtonPopupMode.InstantPopup)
        bl.addWidget(self.btn_more)
        root.addWidget(bar)

        # -- 多列区域 ---------------------------------------------------
        self.area = ColumnArea()
        self.area.columnChanged.connect(self._on_column_changed)
        self.area.columnAdded.connect(lambda *_: self._on_column_changed(""))
        self.area.columnRemoved.connect(lambda *_: self._on_column_changed(""))
        self.area.equalModeChanged.connect(lambda _on: self._schedule_save())
        self.area.saveLogRequested.connect(self._save_column_log)
        self.area.managePresetsRequested.connect(self._open_presets)
        root.addWidget(self.area, 1)

        # -- 底部状态 ---------------------------------------------------
        bottom = QWidget()
        bottom.setObjectName("statusBar")
        bottom.setFixedHeight(30)
        sl = QHBoxLayout(bottom)
        sl.setContentsMargins(16, 0, 10, 0)
        sl.setSpacing(8)
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("statusText")
        sl.addWidget(self.lbl_status)
        sl.addStretch(1)
        self.btn_folder = ThemedIconButton("folder", f"配置目录：{self.store.path.parent}", size=16)
        self.btn_folder.setFixedSize(26, 26)
        self.btn_folder.clicked.connect(self._open_config_dir)
        sl.addWidget(self.btn_folder)
        root.addWidget(bottom)

        self.setCentralWidget(central)

        # -- 动作 -------------------------------------------------------
        self.btn_add.clicked.connect(lambda: self.area.add_column())
        self.btn_rules.clicked.connect(self._open_rules)
        self.btn_presets.clicked.connect(self._open_presets)
        self.btn_save_all.clicked.connect(lambda: self._save_column_log(self._first_uid()))
        self.btn_theme.clicked.connect(self._toggle_theme)

        QShortcut(QKeySequence("Ctrl+N"), self, activated=lambda: self.area.add_column())
        QShortcut(QKeySequence("Ctrl+T"), self, activated=self._open_rules)

    # ------------------------------------------------------------------ 主题
    def _toggle_theme(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        new = "dark" if theme.current_name() == "light" else "light"
        apply_theme(app, new)
        self.cfg.theme = new
        self._sync_theme_button()
        self._rebuild_engine()
        self._schedule_save()

    def _sync_theme_button(self) -> None:
        self.btn_theme.set_icon_name("sun" if theme.current_name() == "dark" else "moon")
        self.btn_theme.setToolTip("切到深色主题" if theme.current_name() == "light" else "切到浅色主题")

    def _rebuild_engine(self) -> None:
        self._engine = RuleEngine(self.cfg.rules, LogPalette.from_tokens(theme.tokens()))
        self.area.set_engine(self._engine)
        self.area.apply_engine(self._engine, replay=False)
        self._update_status()

    # ------------------------------------------------------------------ 动作
    def _first_uid(self) -> str:
        cols = self.area.columns
        return cols[0].cfg.uid if cols else ""

    def _open_rules(self) -> None:
        dlg = RulesDialog(self.cfg.rules, self._engine.palette, self)
        if dlg.exec():
            self.cfg.rules = dlg.rules()
            self._rebuild_engine()
            self.area.apply_engine(self._engine, replay=True)
            self._schedule_save()

    def _open_presets(self) -> None:
        dlg = PresetsDialog(self.cfg.presets, self)
        if dlg.exec():
            self.cfg.presets = dlg.presets()
            self.area.set_presets(self.cfg.presets)
            self._schedule_save()

    def _save_column_log(self, uid: str) -> None:
        col = next((c for c in self.area.columns if c.cfg.uid == uid), None)
        if col is None:
            col = self.area.columns[0] if self.area.columns else None
        if col is None:
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        default = Path(self.cfg.last_dir or Path.home()) / f"{col.port or 'log'}_{stamp}.log"
        path, _ = QFileDialog.getSaveFileName(self, "保存日志", str(default), "日志文件 (*.log *.txt);;所有文件 (*)")
        if not path:
            return
        try:
            n = col.log.save_to_file(path)
        except OSError as exc:
            QMessageBox.warning(self, "保存失败", f"写文件时出错：\n{exc}")
            return
        self.cfg.last_dir = str(Path(path).parent)
        self._schedule_save()
        self.lbl_status.setText(f"已保存 {n} 字符到 {path}")

    def _export_config(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "导出配置", str(Path.home() / "muticom_config.json"), "JSON (*.json)"
        )
        if not path:
            return
        self._sync_cfg()
        try:
            self.store.export_to(self.cfg, Path(path))
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))

    def _import_config(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入配置", str(Path.home()), "JSON (*.json)")
        if not path:
            return
        try:
            imported = self.store.import_from(Path(path))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "导入失败", f"配置文件读不出来：\n{exc}")
            return
        if QMessageBox.question(self, "导入配置", "导入会替换当前所有列和规则，继续？") != QMessageBox.StandardButton.Yes:
            return
        self.area.clear()
        self.cfg.columns = imported.columns
        self.cfg.rules = imported.rules
        self.cfg.presets = imported.presets
        for col_cfg in self.cfg.columns:
            self.area.add_column(col_cfg)
        self._rebuild_engine()
        self.area.set_presets(self.cfg.presets)
        self.area.apply_ports(self.scanner.ports)
        self._update_status()
        self._schedule_save()

    def _open_config_dir(self) -> None:
        import os
        import subprocess

        folder = self.store.path.parent
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(folder))  # noqa: S606 —— Windows 打开资源管理器
        except AttributeError:
            subprocess.Popen(["xdg-open", str(folder)])  # noqa: S603

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "关于 MutiCom",
            f"<b>MutiCom v{__version__}</b><br>多路串口日志监视器<br><br>"
            "一个窗口并排看多路串口，替代多开 VOFA+。<br>"
            "Ctrl+N 新增列 · Ctrl+T 着色规则 · 双击列间分隔条恢复等宽",
        )

    # ------------------------------------------------------------------ 配置
    def _sync_cfg(self) -> None:
        self.cfg.columns = [c.config() for c in self.area.columns]
        self.cfg.splitter_sizes = self.area.sizes()
        self.cfg.equal_columns = self.area.equal_mode
        self.cfg.theme = theme.current_name()

    def _schedule_save(self) -> None:
        self._sync_cfg()
        self.store.schedule_save(self.cfg)

    def _on_column_changed(self, _uid: str) -> None:
        self._update_status()
        self._schedule_save()

    def _update_status(self) -> None:
        cols = self.area.columns
        connected = sum(1 for c in cols if c.is_connected)
        ports = len(self.scanner.ports) if hasattr(self, "scanner") else 0
        mode = "等宽" if self.area.equal_mode else "自定义列宽"
        self.lbl_status.setText(
            f"{len(cols)} 列 · 已连接 {connected} · 可用串口 {ports} · 规则 {self._engine.user_rule_count} 条 · {mode}"
        )
        if hasattr(self, "btn_folder"):
            self.btn_folder.setToolTip(f"配置目录：{self.store.path.parent}")

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self.cfg.geometry = bytes(self.saveGeometry().toBase64()).decode()
            self._sync_cfg()
            self.store.save_now(self.cfg)
        finally:
            self.scanner.stop()
            self.area.shutdown()
        super().closeEvent(event)


__all__ = ["MainWindow"]
