"""ANSI SGR 颜色码解析。

颜色是**流状态**：`\\x1b[34m` 一设，后面所有输出都是蓝的，跨行持续，直到 `\\x1b[0m`
清掉或换色号。所以这里维护一个 `AnsiState`，由 `TextAssembler` 持有、跟着 `_buf`
一起 reset —— 两者必须是同一个生命周期。

解析器只吐**槽位号**（0~15 或 -1），绝不吐 hex：色值属于主题，翻译放在渲染侧，
这样切深浅主题时颜色能跟着走，decoder 也不会沾上 Qt。

跨包截断（`\\x1b[3` + `4m`）由 `_split_dangling_escape` 配合调用方的 `_esc` 缓冲处理，
本模块不关心字节边界。
"""

from __future__ import annotations

import re
from functools import lru_cache

ANSI_NONE = -1

#: 槽位 0~15 的顺序 = SGR 的 30~37 然后 90~97
SLOT_NAMES = (
    "black",
    "red",
    "green",
    "yellow",
    "blue",
    "magenta",
    "cyan",
    "white",
    "bright.black",
    "bright.red",
    "bright.green",
    "bright.yellow",
    "bright.blue",
    "bright.magenta",
    "bright.cyan",
    "bright.white",
)

# xterm 标准 16 色 RGB。故意用标准值而不是主题色：槽位判定必须与主题无关，
# 否则切一次主题，同一个 256 色号可能被判到不同槽位。
_STD_RGB = (
    (0, 0, 0),
    (128, 0, 0),
    (0, 128, 0),
    (128, 128, 0),
    (0, 0, 128),
    (128, 0, 128),
    (0, 128, 128),
    (192, 192, 192),
    (128, 128, 128),
    (255, 0, 0),
    (0, 255, 0),
    (255, 255, 0),
    (0, 0, 255),
    (255, 0, 255),
    (0, 255, 255),
    (255, 255, 255),
)


class AnsiState:
    """当前前景色。只管前景色 —— 背景色、反显、下划线都不在关心范围内。"""

    __slots__ = ("_base", "_bright")

    def __init__(self) -> None:
        self._base: int | None = None
        self._bright = False

    @property
    def bright(self) -> bool:
        return self._bright

    @bright.setter
    def bright(self, value: bool) -> None:
        self._bright = bool(value)

    @property
    def slot(self) -> int:
        """当前颜色的槽位号；没有设过前景色就是 ANSI_NONE。"""
        if self._base is None:
            return ANSI_NONE
        return self._base + (8 if self._bright else 0)

    def set_base(self, n: int) -> None:
        """设 0~7 的基本色号，保留 bright 位（`\\x1b[1m\\x1b[31m` 是亮红）。"""
        self._base = n % 8

    def clear_fg(self) -> None:
        """39m：前景色回默认，不动 bright 位。"""
        self._base = None

    def reset(self) -> None:
        """0m：全部重置。"""
        self._base = None
        self._bright = False


def _xterm256(n: int) -> tuple[int, int, int]:
    """xterm 256 色号 → RGB。0~15 标准色，16~231 是 6×6×6 色立方，232~255 灰阶。"""
    n = max(0, min(255, n))
    if n < 16:
        return _STD_RGB[n]
    if n < 232:
        v = n - 16
        return (v // 36 * 51, (v % 36) // 6 * 51, v % 6 * 51)
    g = 8 + (n - 232) * 10
    return (g, g, g)


@lru_cache(maxsize=512)
def _nearest_slot(r: int, g: int, b: int) -> int:
    """256 色 / 真彩色降级：找最近的标准 16 色槽位。"""
    best = 0
    best_d = None
    for i, (cr, cg, cb) in enumerate(_STD_RGB):
        d = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
        if best_d is None or d < best_d:
            best, best_d = i, d
    return best


def apply_sgr(state: AnsiState, params: str) -> None:
    """应用一串 SGR 参数（`\\x1b[` 与 `m` 之间那段）。

    必须按 arity 消费参数：`38;5;196` 是「256 色 196」这一个指令，不是 38、5、196
    三个独立码 —— 拆开处理会残留一堆脏状态，还会把后面的正常文本吃掉。
    """
    if not params:
        state.reset()          # `\\x1b[m` 等价于 `\\x1b[0m`
        return

    nums = [int(x) if x.isdigit() else 0 for x in params.split(";")]
    i = 0
    while i < len(nums):
        n = nums[i]
        if n == 0:
            state.reset()
        elif n == 39:
            state.clear_fg()
        elif 30 <= n <= 37:
            state.set_base(n - 30)
        elif 90 <= n <= 97:
            state.set_base(n - 90)
            state.bright = True
        elif n == 1:
            state.bright = True
        elif n == 22:
            state.bright = False
        elif n in (38, 48):
            rgb: tuple[int, int, int] | None = None
            if i + 2 < len(nums) and nums[i + 1] == 5:
                rgb = _xterm256(nums[i + 2])
                i += 2
            elif i + 4 < len(nums) and nums[i + 1] == 2:
                rgb = (nums[i + 2], nums[i + 3], nums[i + 4])
                i += 4
            else:
                break          # 残缺的 38/48：整组放弃，别当普通码继续吃
            if n == 38 and rgb is not None:
                slot = _nearest_slot(*rgb)
                state.set_base(slot % 8)
                state.bright = slot >= 8
        # 其余码（4 下划线、7 反显、49 背景默认…）不关心，跳过
        i += 1


_ESC_TAIL = re.compile(r"\x1b(?:\[[0-9;:?<=>]*|\])?$")
_CSI = re.compile(r"\x1b\[([0-9;:?<=>]*)([@-~])")
_OSC = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)", re.S)


def _split_dangling_escape(s: str) -> tuple[str, str]:
    """把字符串末尾「还没收完整」的转义序列切出来，返回 (正文, 待续转义)。

    待续部分由调用方暂存，等下一包到了再拼回去 —— 这是跨包截断（`\\x1b[3` + `4m`）
    能正确还原的原因。完整序列（如 `\\x1b[34m`）不会被切走，它已经能解析了。

    切点必须基于 tail 的实际长度算。写成 `s[: len(s) - 64 + m.start()]` 会在
    `len(s) < 64` 时算出负数下标，把前面的正常文本一起吞进缓冲（已实测复现）。
    """
    tail = s[-64:]
    m = _ESC_TAIL.search(tail)
    if not m:
        return s, ""
    cut = len(s) - len(tail) + m.start()
    return s[:cut], s[cut:]


def scan_line(text: str, state: AnsiState) -> tuple[str, int]:
    """剥掉一行里的转义序列，返回 (可见文本, 这一行采用的槽位号)。

    整行用**首个可见字符**时的颜色 —— 固件的主流写法是行首设色 + 行尾 reset，
    如果改成「最后一个赢」，行尾那个 `0m` 会把整行重置回正文色，功能直接废掉。
    """
    if "\x1b" not in text:                       # 快路径：绝大多数行走这里
        return text, (state.slot if text else ANSI_NONE)

    out: list[str] = []
    first = ANSI_NONE
    have_first = False
    pos = 0
    while True:
        j = text.find("\x1b", pos)
        seg = text[pos:j] if j >= 0 else text[pos:]
        if seg and not have_first:               # 首个可见字符定整行颜色
            first = state.slot
            have_first = True
        out.append(seg)
        if j < 0:
            break
        m = _CSI.match(text, j)
        if m:
            if m.group(2) == "m":
                apply_sgr(state, m.group(1))
            pos = m.end()                        # 其他 CSI（光标、清屏）整段丢弃
            continue
        m = _OSC.match(text, j)
        if m:
            pos = m.end()
            continue
        pos = j + 1                              # 认不出的 ESC：只丢这一个字符
    return "".join(out), first


__all__ = [
    "ANSI_NONE",
    "SLOT_NAMES",
    "AnsiState",
    "apply_sgr",
    "scan_line",
]
