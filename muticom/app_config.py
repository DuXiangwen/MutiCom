"""配置数据模型与 JSON 持久化。

三条约定：
1. 每个 dataclass 字段都有默认值 —— 配置文件缺字段、多字段都不会炸，向前兼容。
2. 枚举统一用 StrEnum，序列化直接存 value，JSON 里人能看懂。
3. 写盘走「临时文件 + os.replace」原子替换，进程被杀也不会留下半个 JSON。
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import MISSING, dataclass, field, fields, is_dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QStandardPaths, QTimer

from .colorizer import ColorRule


# --------------------------------------------------------------------------- 枚举


class Parity(StrEnum):
    NONE = "N"
    EVEN = "E"
    ODD = "O"
    MARK = "M"
    SPACE = "S"


class Flow(StrEnum):
    NONE = "none"
    RTSCTS = "rtscts"
    XONXOFF = "xonxoff"


class LineEnding(StrEnum):
    NONE = ""
    CR = "\r"
    LF = "\n"
    CRLF = "\r\n"


# --------------------------------------------------------------------------- 数据类


@dataclass(slots=True)
class PortParams:
    port: str = ""
    baudrate: int = 115200
    bytesize: int = 8
    parity: Parity = Parity.NONE
    stopbits: float = 1.0
    flow: Flow = Flow.NONE

    def summary(self) -> str:
        """状态栏用的一行摘要，如 COM25 115200 8N1。"""
        if not self.port:
            return "未选择端口"
        return f"{self.port} {self.baudrate} {self.bytesize}{self.parity.value}{self.stopbits:g}"


@dataclass(slots=True)
class ViewOptions:
    timestamp: bool = True
    hex_mode: bool = False
    show_tx: bool = True
    font_size: int = 10
    encoding: str = "utf-8"
    max_lines: int = 50_000
    autoscroll: bool = True
    ignore_lone_cr: bool = False

    def copy(self) -> "ViewOptions":
        return replace(self)


@dataclass(slots=True)
class SendOptions:
    hex_mode: bool = False
    line_ending: LineEnding = LineEnding.CRLF


@dataclass(slots=True)
class Preset:
    name: str = "新指令"
    payload: str = ""
    hex: bool = False
    ending: LineEnding = LineEnding.CRLF
    group: str = "通用"


@dataclass(slots=True)
class ColumnConfig:
    uid: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    params: PortParams = field(default_factory=PortParams)
    view: ViewOptions = field(default_factory=ViewOptions)
    send: SendOptions = field(default_factory=SendOptions)
    auto_connect: bool = False
    draft: str = ""
    # None = 跟随自动策略（窄列/矮窗口自动折），True/False = 用户显式选择
    panel_collapsed: bool | None = None


@dataclass(slots=True)
class AppConfig:
    schema: int = 2
    theme: str = "light"
    equal_columns: bool = True
    geometry: str = ""
    state: str = ""
    splitter_sizes: list[int] = field(default_factory=list)
    columns: list[ColumnConfig] = field(default_factory=list)
    rules: list[ColorRule] = field(default_factory=list)
    presets: list[Preset] = field(default_factory=list)
    last_dir: str = ""

    def ensure_one_column(self) -> None:
        if not self.columns:
            self.columns = [ColumnConfig()]


# --------------------------------------------------------------------------- 通用序列化


def to_dict(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_dict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, StrEnum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [to_dict(x) for x in obj]
    return obj


def _default_of(f) -> Any:
    if f.default is not MISSING:
        return f.default
    if f.default_factory is not MISSING:  # type: ignore[misc]
        return f.default_factory()  # type: ignore[misc]
    raise LookupError(f.name)


def from_dict(cls: type, data: Any) -> Any:
    """按字段默认值的类型把 JSON 值转回来；坏值一律回落默认值。"""
    if not isinstance(data, dict):
        return cls()
    kwargs: dict[str, Any] = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        try:
            default = _default_of(f)
        except LookupError:
            continue
        val = data[f.name]
        try:
            if is_dataclass(default) and not isinstance(default, type):
                kwargs[f.name] = from_dict(type(default), val)
            elif isinstance(default, StrEnum):
                kwargs[f.name] = type(default)(val)
            elif isinstance(default, bool):
                kwargs[f.name] = bool(val)
            elif isinstance(default, int):
                kwargs[f.name] = int(val)
            elif isinstance(default, float):
                kwargs[f.name] = float(val)
            elif default is None:
                # 三态字段（None = 未设置）原样透传，别掉进 str(val) 分支变成 "None"
                kwargs[f.name] = val
            elif isinstance(default, list):
                continue  # 列表元素类型由调用方显式处理
            else:
                kwargs[f.name] = str(val)
        except (TypeError, ValueError):
            continue
    return cls(**kwargs)


# --------------------------------------------------------------------------- 持久化


def default_config_path() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppConfigLocation)
    if not base:
        base = str(Path.home() / ".muticom")
    return Path(base) / "config.json"


class ConfigStore(QObject):
    """配置读写。改动去抖 800ms 落盘，退出时强制落盘。"""

    def __init__(self, path: Path | None = None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._path = Path(path) if path else default_config_path()
        self._pending: AppConfig | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(800)
        self._timer.timeout.connect(self._flush)

    @property
    def path(self) -> Path:
        return self._path

    # -- 读 ---------------------------------------------------------------
    def load(self) -> AppConfig:
        if not self._path.exists():
            cfg = AppConfig()
            cfg.ensure_one_column()
            return cfg
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # 文件坏了别让程序起不来：备份一份，用默认配置继续
            try:
                self._path.replace(self._path.with_suffix(".bak"))
            except OSError:
                pass
            cfg = AppConfig()
            cfg.ensure_one_column()
            return cfg

        cfg = AppConfig(
            schema=int(raw.get("schema", 2) or 2),
            theme=str(raw.get("theme") or "light"),
            equal_columns=bool(raw.get("equal_columns", True)),
            geometry=str(raw.get("geometry", "") or ""),
            state=str(raw.get("state", "") or ""),
            splitter_sizes=[int(x) for x in (raw.get("splitter_sizes") or []) if isinstance(x, (int, float))],
            last_dir=str(raw.get("last_dir", "") or ""),
        )
        for item in raw.get("columns") or []:
            cfg.columns.append(from_dict(ColumnConfig, item))
        for item in raw.get("rules") or []:
            cfg.rules.append(from_dict(ColorRule, item))
        for item in raw.get("presets") or []:
            cfg.presets.append(from_dict(Preset, item))
        cfg.ensure_one_column()
        return cfg

    # -- 写 ---------------------------------------------------------------
    def save_now(self, cfg: AppConfig) -> None:
        self._pending = None
        self._timer.stop()
        self._write(cfg)

    def schedule_save(self, cfg: AppConfig) -> None:
        self._pending = cfg
        if not self._timer.isActive():
            self._timer.start()

    def _flush(self) -> None:
        if self._pending is None:
            return
        cfg, self._pending = self._pending, None
        self._write(cfg)

    def _write(self, cfg: AppConfig) -> None:
        payload = {
            "schema": cfg.schema,
            "theme": cfg.theme,
            "equal_columns": cfg.equal_columns,
            "geometry": cfg.geometry,
            "state": cfg.state,
            "splitter_sizes": list(cfg.splitter_sizes),
            "columns": [to_dict(c) for c in cfg.columns],
            "rules": [to_dict(r) for r in cfg.rules if not r.builtin],
            "presets": [to_dict(p) for p in cfg.presets],
            "last_dir": cfg.last_dir,
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self._path)
        except OSError:
            # 写盘失败不影响使用（比如目录只读），静默跳过
            pass

    def export_to(self, cfg: AppConfig, path: Path) -> None:
        payload = {
            "schema": cfg.schema,
            "theme": cfg.theme,
            "equal_columns": cfg.equal_columns,
            "columns": [to_dict(c) for c in cfg.columns],
            "rules": [to_dict(r) for r in cfg.rules if not r.builtin],
            "presets": [to_dict(p) for p in cfg.presets],
        }
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def import_from(self, path: Path) -> AppConfig:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        cfg = AppConfig()
        for item in raw.get("columns") or []:
            cfg.columns.append(from_dict(ColumnConfig, item))
        for item in raw.get("rules") or []:
            cfg.rules.append(from_dict(ColorRule, item))
        for item in raw.get("presets") or []:
            cfg.presets.append(from_dict(Preset, item))
        cfg.ensure_one_column()
        return cfg
