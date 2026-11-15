"""字节流 → 文本行 / Hex 行。

文本模式用 codecs 的增量解码器，被 TCP/串口切断的多字节字符会留在内部等下一包补齐，
不会吐出半个汉字。分行按 `\\r\\n` / `\\n` / 单个 `\\r` 三种终止符处理（很多固件用 `\\r`
做进度条刷新，所以「忽略孤 \\r」是个可开关的选项）。
"""

from __future__ import annotations

import codecs
import re

_TERM_TEXT = re.compile(r"\r\n|\n|\r")
_TERM_BYTES = re.compile(rb"\r\n|\n|\r")

HEX_ROW = 16  # 每行显示的字节数


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
        return bool(self._buf)

    def reset(self) -> None:
        self._decoder.reset()
        self._buf = ""

    # -- 主流程 -----------------------------------------------------------
    def feed(self, data: bytes) -> list[str]:
        if not data:
            return []
        text = self._decoder.decode(data)
        if not text:
            return []
        self._buf += text
        return self._take_complete()

    def flush(self) -> list[str]:
        """超时静默后，把没有换行结尾的半行推出去。"""
        if not self._buf:
            return []
        out, self._buf = [self._buf], ""
        return out

    def _take_complete(self) -> list[str]:
        buf = self._buf
        if self._ignore_lone_cr:
            parts = buf.replace("\r\n", "\n").split("\n")
        else:
            parts = _TERM_TEXT.split(buf)
        if len(parts) == 1:
            # 没有终止符，整段还是半行
            return self._enforce_max_line([])
        self._buf = parts.pop()
        return self._enforce_max_line(parts)

    def _enforce_max_line(self, out: list[str]) -> list[str]:
        """单行超过 8K 字符就切断 —— 一行几十万字符能把 QPlainTextEdit 布局拖死。"""
        guard = 0
        while len(self._buf) > self._max_line and guard < 32:
            out.append(self._buf[: self._max_line])
            self._buf = self._buf[self._max_line :]
            guard += 1
        if len(self._buf) > self._max_line:
            overflow = len(self._buf)
            self._buf = ""
            out.append(f"…(超长行剩余 {overflow} 字符已丢弃)")
        return out


class HexAssembler:
    """字节流 → hexdump 行，按原始行边界对齐（每行最多 16 字节）。"""

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

    def feed(self, data: bytes) -> list[str]:
        if not data:
            return []
        self._buf += data
        parts = _TERM_BYTES.split(self._buf)
        if len(parts) == 1:
            return []
        self._buf = parts.pop()
        out: list[str] = []
        for chunk in parts:
            out.extend(self._format(chunk))
        return out

    def flush(self) -> list[str]:
        if not self._buf:
            return []
        chunk, self._buf = self._buf, b""
        return self._format(chunk)

    def _format(self, chunk: bytes) -> list[str]:
        base = self._offset
        self._offset += len(chunk)
        if not chunk:
            return [hex_line(base, b"")]
        return [hex_line(base + i, chunk[i : i + HEX_ROW]) for i in range(0, len(chunk), HEX_ROW)]
