"""日志着色规则引擎。

匹配顺序：用户自定义规则（按列表顺序）→ 行内级别标签快路径（[E]/[W]/[I]/[D]/[V]）
→ 内置正则规则（error / warn / ok / 分隔行）→ 默认正文色。

色值不写死在这里，由 `LogPalette` 从主题令牌注入 —— 同一套规则在浅色/深色下自动换色。
级别标签快路径放在正则之前：嵌入式日志 90% 行首就带 `[I]` 这类标记，一次 C 层子串查找
比跑 4 条正则快一到两个量级，而级别标签本来就应该压过关键词（`[I] no error` 该显示成 Info）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True, slots=True)
class LogPalette:
    """日志区的语义色。字段名和 theme.py 的令牌一一对应。"""

    text: str
    ts: str
    tx: str
    info: str
    warn: str
    danger: str
    debug: str
    sep: str
    sys: str
    ok: str

    @classmethod
    def from_tokens(cls, t: dict[str, str]) -> "LogPalette":
        return cls(
            text=t["log.text"],
            ts=t["log.ts"],
            tx=t["log.tx"],
            info=t["log.info"],
            warn=t["warn"],
            danger=t["danger"],
            debug=t["log.debug"],
            sep=t["log.sep"],
            sys=t["log.sys"],
            ok=t["ok"],
        )


@dataclass(frozen=True, slots=True)
class ColorRule:
    """一条着色规则。builtin=True 的规则只读，不写进配置文件。"""

    name: str = ""
    pattern: str = ""
    color: str = "#d4d4d4"
    regex: bool = True
    enabled: bool = True
    builtin: bool = False


_ERROR_RX = re.compile(r"\b(?:error|err|failed|failure|panic|fatal|abort|assert\w*\s*fail)\b", re.I)
_WARN_RX = re.compile(r"\b(?:warn(?:ing)?|retry|deprecated|timeout)\b", re.I)
_OK_RX = re.compile(r"\b(?:ok|success|succeed|done|passed|ready)\b", re.I)
_SEP_RX = re.compile(r"^[=\-#*]{3,}")


def builtin_rules(palette: LogPalette) -> list[ColorRule]:
    """给对话框展示用的内置规则快照（实际匹配走下面的快路径，不依赖这个列表）。"""
    return [
        ColorRule(name="级别 [E]/[F]", pattern="error", color=palette.danger, builtin=True),
        ColorRule(name="级别 [W]", pattern="warn", color=palette.warn, builtin=True),
        ColorRule(name="级别 [I]", pattern="info", color=palette.info, builtin=True),
        ColorRule(name="级别 [D]/[V]", pattern="debug", color=palette.debug, builtin=True),
        ColorRule(name="格式错误", pattern="error / fail / panic / abort", color=palette.danger, builtin=True),
        ColorRule(name="格式警告", pattern="warn / retry / timeout", color=palette.warn, builtin=True),
        ColorRule(name="格式成功", pattern="ok / done / success", color=palette.ok, builtin=True),
        ColorRule(name="分隔行", pattern="==== ----", color=palette.sep, builtin=True),
    ]


def validate(pattern: str, regex: bool) -> tuple[bool, str]:
    """返回 (是否可用, 错误说明)。正则非法时对话框会标红，运行时会自动降级为子串匹配。"""
    if not pattern:
        return False, "匹配内容不能为空"
    if not regex:
        return True, ""
    try:
        re.compile(pattern, re.I)
    except re.error as exc:
        return False, f"正则表达式有误：{exc}"
    return True, ""


class RuleEngine:
    """不可变的规则集合：换规则就新建一个实例整体替换，避免多线程下改到一半。"""

    def __init__(self, user_rules: Sequence[ColorRule] = (), palette: LogPalette | None = None) -> None:
        self.palette = palette or LogPalette.from_tokens(_FALLBACK_TOKENS)
        self._user: list[tuple[object, str]] = []
        self._user_count = 0
        for rule in user_rules:
            if not rule.enabled or not rule.pattern:
                continue
            matcher: object
            if rule.regex:
                try:
                    matcher = re.compile(rule.pattern, re.I)
                except re.error:
                    matcher = rule.pattern.lower()  # 非法正则降级成子串，不让程序挂
            else:
                matcher = rule.pattern.lower()
            self._user.append((matcher, rule.color))
            self._user_count += 1

        p = self.palette
        self._tags_severe = (("[E]", p.danger), ("[F]", p.danger), ("[W]", p.warn))
        self._tags_normal = (("[I]", p.info), ("[D]", p.debug), ("[V]", p.debug))

    @property
    def user_rule_count(self) -> int:
        return self._user_count

    def color_for(self, line: str) -> str:
        p = self.palette
        # 1) 用户自定义规则优先
        if self._user:
            lowered = ""
            for matcher, color in self._user:
                if isinstance(matcher, re.Pattern):
                    if matcher.search(line):
                        return color
                else:
                    if not lowered:
                        lowered = line.lower()
                    if matcher in lowered:  # type: ignore[operator]
                        return color
        # 2) 明确的严重级别标签
        for tag, color in self._tags_severe:
            if tag in line:
                return color
        # 3) 分隔行是结构性的，先判掉（`==== boot ready ====` 不该被 ready 抢走）
        if _SEP_RX.search(line):
            return p.sep
        # 4) 关键词
        if _ERROR_RX.search(line):
            return p.danger
        # 5) 普通级别标签压过 warn/ok 关键词
        for tag, color in self._tags_normal:
            if tag in line:
                return color
        if _WARN_RX.search(line):
            return p.warn
        if _OK_RX.search(line):
            return p.ok
        return p.text

    def colorize(self, lines: Iterable[str]) -> list[tuple[str, str, int]]:
        """批量着色，返回 [(文本, 颜色, 时间戳前缀长度)] —— 供 worker 侧调用。"""
        return [(text, self.color_for(text), 0) for text in lines]


# RuleEngine 在拿到主题之前也可能被构造（比如单测），给一份浅色兜底
_FALLBACK_TOKENS: dict[str, str] = {
    "log.text": "#24292f",
    "log.ts": "#616b76",
    "log.tx": "#0550ae",
    "log.info": "#0b6b60",
    "log.debug": "#5c6672",
    "log.sep": "#6f42c1",
    "log.sys": "#6e7781",
    "warn": "#8a5f00",
    "danger": "#b42318",
    "ok": "#1a7f37",
}
