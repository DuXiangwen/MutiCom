"""串口枚举与热插拔检测。

`list_ports.comports()` 在 Windows 上是读注册表，几毫秒级，放在 UI 线程里定时跑没问题；
但为了不打断拖拽这类连续操作，扫描间隔取 3 秒，且只有结果变化时才发信号。
"""

from __future__ import annotations

import re

from PySide6.QtCore import QObject, QTimer, Signal
from serial.tools import list_ports

_NUM = re.compile(r"(\d+)")
# Windows 上 pyserial 的 description 常常自带 "(COM25)"，和我们拼的 "COM25 · ..." 会重复
_DESC_COM = re.compile(r"\s*\(COM\d+\)\s*$", re.I)


def _sort_key(info: "PortInfo") -> tuple:
    """COM2 排在 COM10 前面（按数字而不是字符串）。"""
    m = _NUM.search(info.device)
    return (int(m.group(1)) if m else 0, info.device)


class PortInfo:
    __slots__ = ("device", "description", "hwid", "vid", "pid")

    def __init__(self, device: str, description: str, hwid: str, vid: int | None, pid: int | None) -> None:
        self.device = device
        self.description = description
        self.hwid = hwid
        self.vid = vid
        self.pid = pid

    @property
    def label(self) -> str:
        return f"{self.device} · {self.description}" if self.description else self.device

    @property
    def detail(self) -> str:
        if self.vid is not None and self.pid is not None:
            return f"VID:PID={self.vid:04X}:{self.pid:04X}"
        # 虚拟串口（ELTIMA 之类）没有 VID/PID，退回 hwid 里的地址
        return self.hwid.split(" LOCATION")[0][:48]

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PortInfo):
            return NotImplemented
        return (self.device, self.description, self.hwid) == (other.device, other.description, other.hwid)

    def __hash__(self) -> int:
        return hash((self.device, self.hwid))

    def __repr__(self) -> str:
        return f"PortInfo({self.device!r})"


class PortScanner(QObject):
    portsChanged = Signal(list)  # list[PortInfo]

    def __init__(self, interval_ms: int = 3000, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ports: list[PortInfo] = []
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)

    @property
    def ports(self) -> list[PortInfo]:
        return self._ports

    def start(self) -> None:
        self.rescan(force=True)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        self.rescan()

    def rescan(self, force: bool = False) -> list[PortInfo]:
        ports = self.current()
        if force or ports != self._ports:
            self._ports = ports
            self.portsChanged.emit(ports)
        return ports

    @staticmethod
    def current() -> list[PortInfo]:
        out: list[PortInfo] = []
        try:
            for p in list_ports.comports():
                desc = _DESC_COM.sub("", (p.description or "").strip())
                out.append(
                    PortInfo(
                        device=p.device,
                        description=desc,
                        hwid=(p.hwid or "").strip(),
                        vid=getattr(p, "vid", None),
                        pid=getattr(p, "pid", None),
                    )
                )
        except Exception:  # 枚举失败不该影响界面
            return []
        out.sort(key=_sort_key)
        return out
