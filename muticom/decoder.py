"""字节流 → 文本行 / Hex 行。

文本模式用 codecs 的增量解码器，被 TCP/串口切断的多字节字符会留在内部等下一包补齐，
不会吐出半个汉字。分行按 `\\r\\n` / `\\n` / 单个 `\\r` 三种终止符处理（很多固件用 `\\r`
做进度条刷新，所以「忽略孤 \\r」是个可开关的选项）。

ANSI 颜色码在这一层解析：`AnsiState` 和 `_buf` 同生命周期，`reset()` 时一起清零。
输出统一是 `(文本, 槽位号)` —— 槽位 0~15 或 -1，**不在这里翻译成 hex**，色值属于主题。

跨包截断（`\\x1b[3` + `4m`）靠 `_esc` 缓冲还原：半截序列在 `feed()` 里就被摘出来单独存着，
等下一包拼回去再解析。绝不能就地丢掉 —— 那样会删掉前半截、把 `4m` 当正文吐到屏幕上。
"""

from __future__ import annotations

import codecs
import re

from .ansi import ANSI_NONE, AnsiState, _split_dangling_escape, scan_line

_TERM_TEXT = re.compile(r"\r\n|\n|\r")
_TERM_BYTES = re.compile(rb"\r\n|\n|\r")

#: 切块时如果切点落在转义序列中间，往前挪到 ESC 之前
_CSI_OPEN = re.compile(r"\x1b\[[0-9;:?<=>]*$")

HEX_ROW = 16  # 每行显示的字节数


def _safe_cut(s: str, limit: int) -> int:
    """算出不超过 limit 的安全切点，别把 `\\x1b[34m` 切成 `\\x1b[3` + `4m`。

    `$` 只能对尾部窗口做匹配 —— 直接对整个字符串用会永远匹配不到中间位置。
    """
    window = s[max(0, limit - 32) : limit]
    m = _CSI_OPEN.search(window)
    if not m:
        return limit
    return limit - len(window) + m.start()


def hex_line(offset: int, data: bytes) -> str:
    """标准 hexdump 一行：8 位偏移 + 16 字节 + ASCII 预览。"""
    if not data:
        return f"{offset:08X}"
    hexs = " ".join(f"{b:02X}" for b in data)
    ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
    return f"{offset:08X}  {hexs:<47}  |{ascii_:<16}|"


def format_hex_block(data: bytes, offset: int = 0) -> list[str]:
    """把一段字节格式化成若干 hexdump 行（不做分行判断，供 Tx 回显等场景用）。"""
    return [hex_line(offset + i, data[i : i + HEX_ROW]) for i in range(0, len(data), HEX_ROW)]


class TextAssembler:
    """字节流 → 完整文本行。不完整的尾巴留在缓冲里，等 flush() 或下一包。"""

    def __init__(
        self,
        encoding: str = "utf-8",
        max_line: int = 8192,
        ignore_lone_cr: bool = False,
    ) -> None:
        self._encoding = encoding
        self._max_line = max_line
        self._ignore_lone_cr = ignore_lone_cr
        self._decoder = self._make_decoder(encoding)
        self._buf = ""
        self._ansi = AnsiState()
        self._esc = ""          # 末尾还没收完整的转义序列，等下一包拼回来

    @staticmethod
    def _make_decoder(encoding: str) -> codecs.IncrementalDecoder:
        try:
            factory = codecs.getincrementaldecoder(encoding)
        except LookupError:
            factory = codecs.getincrementaldecoder("utf-8")
        return factory(errors="replace")

    # -- 配置 -------------------------------------------------------------
    def configure(self, encoding: str, ignore_lone_cr: bool) -> None:
        if encoding != self._encoding:
            self._encoding = encoding
            self._decoder = self._make_decoder(encoding)
        self._ignore_lone_cr = ignore_lone_cr

    # -- 状态 -------------------------------------------------------------
    @property
    def has_partial(self) -> bool:
        """只看 `_buf`：只剩半截转义时没有可显示的东西，不该触发超时 flush。"""
        return bool(self._buf)

    def reset(self) -> None:
        self._decoder.reset()
        self._buf = ""
        self._ansi.reset()
        self._esc = ""

    # -- 主流程 -----------------------------------------------------------
    def feed(self, data: bytes) -> list[tuple[str, int]]:
        if not data:
            return []
        text = self._decoder.decode(data)
        if not text:
            return []
        if self._esc:                                   # 上一包剩下的半截，先拼回来
            text, self._esc = self._esc + text, ""
        self._buf += text
        # 摘除必须在这里做：`_take_complete` 有两个出口，「整段没有换行」那条
        # 根本不碰 `_buf`，在那边摘会漏掉一半情况。
        self._buf, self._esc = _split_dangling_escape(self._buf)
        return self._take_complete()

    def flush(self) -> list[tuple[str, int]]:
        """超时静默后，把没有换行结尾的半行推出去。

        不 reset `_ansi`：`\\x1b[34m` 之后静默、下一段接着来，仍然是蓝的（终端就是这么跑的）。
        `_buf` 在 `feed()` 里已保证没有半截转义，所以这里吐出去的一定是干净文本。
        """
        if not self._buf:
            return []
        raw, self._buf = self._buf, ""
        text, slot = scan_line(raw, self._ansi)
        return [(text, slot)] if text else []

    def _take_complete(self) -> list[tuple[str, int]]:
        buf = self._buf
        if self._ignore_lone_cr:
            parts = buf.replace("\r\n", "\n").split("\n")
        else:
            parts = _TERM_TEXT.split(buf)
        if len(parts) == 1:
            # 没有终止符，整段还是半行
            return [self._emit(s) for s in self._enforce_max_line([])]
        self._buf = parts.pop()
        return [self._emit(s) for s in self._enforce_max_line(parts)]

    def _emit(self, raw: str) -> tuple[str, int]:
        """按输出顺序推进颜色状态 —— 顺序对了，颜色归属就对了。"""
        return scan_line(raw, self._ansi)

    def _enforce_max_line(self, out: list[str]) -> list[str]:
        """单行超过 8K 字符就切断 —— 一行几十万字符能把 QPlainTextEdit 布局拖死。

        注意只切 `_buf`（没有换行的那条残片），不切 `out` 里已完成的行：
        「一行对应一个换行符」这个契约不能破。
        """
        guard = 0
        while len(self._buf) > self._max_line and guard < 32:
            cut = _safe_cut(self._buf, self._max_line)
            out.append(self._buf[:cut])
            self._buf = self._buf[cut:]
            guard += 1
        if len(self._buf) > self._max_line:
            overflow = len(self._buf)
            self._buf = ""
            out.append(f"…(超长行剩余 {overflow} 字符已丢弃)")
        return out


class HexAssembler:
    """字节流 → hexdump 行，按原始行边界对齐（每行最多 16 字节）。

    Hex 模式**不解析颜色**：这个视图存在的意义就是给用户看字节真相，槽位恒为 ANSI_NONE。
    返回类型必须和 `TextAssembler` 一致 —— 两个 assembler 共用
    `for text, slot in self._asm.feed(data)` 这一行解包，返回 `list[str]` 会直接崩。
    """

    def __init__(self) -> None:
        self._buf = b""
        self._offset = 0
        self._partial_offset = 0

    @property
    def has_partial(self) -> bool:
        return bool(self._buf)

    def reset(self) -> None:
        self._buf = b""
        self._offset = 0
        self._partial_offset = 0

    def feed(self, data: bytes) -> list[tuple[str, int]]:
        if not data:
            return []
        self._buf += data
        parts = _TERM_BYTES.split(self._buf)
        if len(parts) == 1:
            return []
        self._buf = parts.pop()
        out: list[tuple[str, int]] = []
        for chunk in parts:
            out.extend((line, ANSI_NONE) for line in self._format(chunk))
        return out

    def flush(self) -> list[tuple[str, int]]:
        if not self._buf:
            return []
        chunk, self._buf = self._buf, b""
        return [(line, ANSI_NONE) for line in self._format(chunk)]

    def _format(self, chunk: bytes) -> list[str]:
        base = self._offset
        self._offset += len(chunk)
        if not chunk:
            return [hex_line(base, b"")]
        return [hex_line(base + i, chunk[i : i + HEX_ROW]) for i in range(0, len(chunk), HEX_ROW)]


__all__ = [
    "HEX_ROW",
    "HexAssembler",
    "TextAssembler",
    "format_hex_block",
    "hex_line",
]
