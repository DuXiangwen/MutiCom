"""主题令牌 + QSS 渲染 + 调色板。

为什么用「一份模板 + 令牌替换」而不是两份 QSS：这个主题有 38 个语义色、上百处引用，
两份文件只要有一次改动不同步，就会出现"深色下好看、浅色下塌了"。现在颜色只在一个地方定义。

模板占位符写成 `${bg.card}`，用 str.replace 逐条替换（不用 string.Template：它的 idpattern
不允许点号，会解析失败）。替换完如果还剩 `${` 就直接抛错，把漏掉的令牌名打出来——
静态文件里这种东西静默失败最难查。
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from .paths import resources_dir

# --------------------------------------------------------------------------- 令牌

LIGHT: dict[str, str] = {
    # 背景层级：窗口 → 地面 → 卡片 → 内嵌块 → 日志
    "bg.app": "#f3f4f6",
    "bg.canvas": "#e9ebee",
    "bg.card": "#ffffff",
    "bg.panel": "#f7f8fa",
    "bg.input": "#ffffff",
    "bg.log": "#fbfbfc",
    "bg.hover": "#f0f1f3",
    "bg.pressed": "#e6e8eb",
    # 边框
    "border": "#d8dbe0",
    "border.subtle": "#e9ebee",
    "border.strong": "#c2c8d0",
    # 文字
    "text": "#1f2328",
    "text.dim": "#59636e",
    "text.faint": "#6e7781",
    "text.on-accent": "#ffffff",
    "text.on-danger": "#ffffff",
    # 强调 / 语义
    "accent": "#0f766e",
    "accent.hover": "#0b665f",
    "accent.pressed": "#0a5751",
    "accent.subtle": "#e6f4f1",
    "danger": "#b42318",
    "danger.hover": "#9c1d13",
    "ok": "#1a7f37",
    "warn": "#8a5f00",
    "info": "#0550ae",
    "dot.off": "#b6bcc4",
    # 交互
    "selection": "#cfe4fb",
    "selection.text": "#1f2328",
    "scroll.handle": "#cdd3da",
    "scroll.handle.hover": "#b3bbc5",
    "focus.ring": "#0f766e",
    # 日志语义色（白底上必须压明度，深色那套在白底只有 1.6~2.0:1）
    "log.text": "#24292f",
    "log.ts": "#616b76",
    "log.tx": "#0550ae",
    "log.info": "#0b6b60",
    "log.debug": "#5c6672",
    "log.sep": "#6f42c1",
    "log.sys": "#6e7781",
}

DARK: dict[str, str] = {
    "bg.app": "#17181a",
    "bg.canvas": "#121316",
    "bg.card": "#1e1f22",
    "bg.panel": "#24262a",
    "bg.input": "#1a1b1e",
    "bg.log": "#131417",
    "bg.hover": "#2a2c31",
    "bg.pressed": "#202226",
    "border": "#33363c",
    "border.subtle": "#2a2d32",
    "border.strong": "#454a52",
    "text": "#e3e5e8",
    "text.dim": "#9aa0a8",
    "text.faint": "#7d838c",
    "text.on-accent": "#05201d",
    "text.on-danger": "#2b0a0c",
    "accent": "#2bb3a3",
    "accent.hover": "#3ec9b8",
    "accent.pressed": "#1f8b80",
    "accent.subtle": "#16302c",
    "danger": "#f0616d",
    "danger.hover": "#ff7a85",
    "ok": "#3fb950",
    "warn": "#d7ba7d",
    "info": "#6cb0f5",
    "dot.off": "#4a4f57",
    "selection": "#2a4a7a",
    "selection.text": "#ffffff",
    "scroll.handle": "#3d414a",
    "scroll.handle.hover": "#4d525c",
    "focus.ring": "#2bb3a3",
    "log.text": "#d6d9dd",
    "log.ts": "#6a9955",
    "log.tx": "#7aa2f7",
    "log.info": "#4ec9b0",
    "log.debug": "#8b949e",
    "log.sep": "#b392f0",
    "log.sys": "#7d8590",
}

THEMES = {"light": LIGHT, "dark": DARK}
DEFAULT_THEME = "light"


# --------------------------------------------------------------------------- 状态


class _Bus(QObject):
    changed = Signal(str)


bus = _Bus()
_current = DEFAULT_THEME


def current_name() -> str:
    return _current


def is_dark() -> bool:
    return _current == "dark"


def tokens(name: str | None = None) -> dict[str, str]:
    return THEMES.get(name or _current, LIGHT)


def color(key: str) -> str:
    """取当前主题的某个令牌，缺了返回洋红（一眼就能看出是漏了）。"""
    return tokens().get(key, "#ff00ff")


# --------------------------------------------------------------------------- 渲染


def render_qss(t: dict[str, str]) -> str:
    text = (resources_dir() / "theme.qss.tmpl").read_text(encoding="utf-8")
    for key, value in t.items():
        text = text.replace("${" + key + "}", value)
    missing = text.find("${")
    if missing >= 0:
        name = text[missing + 2 : text.find("}", missing)]
        raise KeyError(f"theme.qss.tmpl 里用了未定义的令牌：{name}")
    return text


def qpalette_for(t: dict[str, str]) -> QPalette:
    """Fusion 的原生控件（弹窗、菜单、tooltip）不吃 QSS，必须同时给调色板。"""
    p = QPalette()
    role = QPalette.ColorRole
    group = QPalette.ColorGroup
    c = QColor
    p.setColor(role.Window, c(t["bg.app"]))
    p.setColor(role.WindowText, c(t["text"]))
    p.setColor(role.Base, c(t["bg.input"]))
    p.setColor(role.AlternateBase, c(t["bg.panel"]))
    p.setColor(role.Text, c(t["text"]))
    p.setColor(role.Button, c(t["bg.card"]))
    p.setColor(role.ButtonText, c(t["text"]))
    p.setColor(role.BrightText, c(t["accent"]))
    p.setColor(role.Highlight, c(t["selection"]))
    p.setColor(role.HighlightedText, c(t["selection.text"]))
    p.setColor(role.ToolTipBase, c(t["bg.card"]))
    p.setColor(role.ToolTipText, c(t["text"]))
    p.setColor(role.PlaceholderText, c(t["text.faint"]))
    p.setColor(role.Link, c(t["accent"]))
    p.setColor(role.Light, c(t["bg.hover"]))
    p.setColor(role.Midlight, c(t["border.subtle"]))
    p.setColor(role.Mid, c(t["border"]))
    p.setColor(role.Dark, c(t["border.strong"]))
    p.setColor(role.Shadow, c(t["border.strong"]))

    disabled = group.Disabled
    p.setColor(disabled, role.Text, c(t["text.faint"]))
    p.setColor(disabled, role.WindowText, c(t["text.faint"]))
    p.setColor(disabled, role.ButtonText, c(t["text.faint"]))
    p.setColor(disabled, role.Base, c(t["bg.panel"]))
    p.setColor(disabled, role.Highlight, c(t["border"]))
    return p


def apply(app: QApplication, name: str | None = None) -> None:
    global _current
    name = name if name in THEMES else DEFAULT_THEME
    _current = name
    t = THEMES[name]
    app.setStyle("Fusion")  # 缺了它：下拉边框、滚动条、弹窗底色都不听 QSS
    app.setPalette(qpalette_for(t))
    app.setStyleSheet(render_qss(t))
    bus.changed.emit(name)


def toggle(app: QApplication) -> str:
    apply(app, "dark" if _current == "light" else "light")
    return _current


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG 相对亮度比，用来判断用户自定义规则色在实际背景上能不能看清。"""
    def lum(c: QColor) -> float:
        def ch(v: float) -> float:
            v /= 255.0
            return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

        return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())

    a, b = lum(QColor(fg)), lum(QColor(bg))
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)
