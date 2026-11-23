"""串口会话。

线程模型：每路一个 QThread + 一个 `SerialWorker`（moveToThread），**所有**串口动作
（打开/关闭/写/换编码/换规则）都是 worker 的槽，从 UI 侧用信号投递过去，在 worker 线程里
串行执行 —— 所以没有任何锁，也不会出现「UI 线程直接摸 pyserial 对象」这种隐患。

收数据靠 2ms 的精确定时器轮询 `in_waiting`（timeout=0，纯非阻塞），攒够 256 行或过了 16ms
才往 UI 发一次，避免高波特率下每来一个字节就闹一次界面。
"""

from __future__ import annotations

import time
from datetime import datetime

import serial
from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot

from .app_config import Flow, PortParams, ViewOptions
from .colorizer import RuleEngine
from .decoder import HexAssembler, TextAssembler, format_hex_block

READ_CHUNK = 64 * 1024      # 单次最多读 64KB，天然分块
RAW_LIMIT = 256 * 1024      # 原始字节环形缓冲，用于切 Hex/文本时重放
BATCH_LINES = 256           # 攒够这么多行就发
BATCH_INTERVAL = 0.016      # 或者超过 16ms 就发
PARTIAL_TIMEOUT = 0.12      # 半行静默 120ms 后强制推出
WRITE_SLICE = 8192          # 发送分块，避免大包把泵堵住


def _explain(exc: BaseException) -> str:
    msg = str(exc)
    low = msg.lower()
    if "permission" in low or "拒绝访问" in msg or "access is denied" in low:
        return "端口被占用（可能被其他程序打开，比如 VOFA+、串口助手）"
    if "file not found" in low or "cannot find" in low or "系统找不到" in msg or "not exist" in low:
        return "端口不存在，检查设备是否插好"
    if not msg:
        return exc.__class__.__name__
    return msg


class SerialWorker(QObject):
    opened = Signal()
    openFailed = Signal(str)
    closed = Signal()
    linesReady = Signal(list, bool)   # [(文本, 颜色|None, 时间戳前缀长度, ANSI 槽位)], 是否需要先清屏
    statsDelta = Signal(int, int)     # 本次新增的 (收字节, 发字节)
    errorOccurred = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._ser: serial.Serial | None = None
        self._view = ViewOptions()
        self._engine = RuleEngine()
        self._asm: TextAssembler | HexAssembler = TextAssembler()
        self._raw = bytearray()
        self._pending: list[tuple[str, str | None, int, int]] = []
        self._last_emit = 0.0
        self._last_rx = 0.0
        self._rx_bytes = 0
        self._tx_bytes = 0

        self._pump_timer = QTimer(self)
        self._pump_timer.setInterval(2)
        self._pump_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._pump_timer.timeout.connect(self._pump)

    # ------------------------------------------------------------------ 打开/关闭
    @Slot(object)
    def openPort(self, params: PortParams) -> None:
        self._close_inner()
        try:
            ser = serial.Serial(
                port=params.port,
                baudrate=params.baudrate,
                bytesize=params.bytesize,
                parity=params.parity.value,
                stopbits=params.stopbits,
                timeout=0,
                write_timeout=2.0,
                xonxoff=params.flow == Flow.XONXOFF,
                rtscts=params.flow == Flow.RTSCTS,
            )
        except Exception as exc:  # noqa: BLE001 —— pyserial 抛的异常类型很杂
            self.openFailed.emit(_explain(exc))
            return
        self._ser = ser
        self._raw.clear()
        self._pending.clear()
        self._rx_bytes = self._tx_bytes = 0
        self._last_emit = time.perf_counter()
        self._last_rx = self._last_emit
        self._rebuild_assembler()
        self._pump_timer.start()
        self.opened.emit()

    @Slot()
    def closePort(self) -> None:
        was_open = self._ser is not None
        self._close_inner()
        if was_open:
            self.closed.emit()

    def _close_inner(self) -> None:
        self._pump_timer.stop()
        if self._ser is not None:
            try:
                self._ser.break_condition = False   # 别把 TX 按在低电平上关串口
            except Exception:  # noqa: BLE001
                pass
            try:
                self._ser.close()
            except Exception:  # noqa: BLE001
                pass
            self._ser = None
        self._flush()

    @property
    def is_open(self) -> bool:
        return self._ser is not None

    # ------------------------------------------------------------------ 采集
    @Slot()
    def _pump(self) -> None:
        ser = self._ser
        if ser is None:
            return
        try:
            for _ in range(8):
                n = ser.in_waiting
                if not n:
                    break
                chunk = ser.read(min(n, READ_CHUNK))
                if not chunk:
                    break
                self._on_chunk(chunk)
        except Exception as exc:  # noqa: BLE001 —— 拔线会在这里抛
            self.errorOccurred.emit(_explain(exc))
            self.closePort()
            return

        now = time.perf_counter()
        if self._asm.has_partial and (now - self._last_rx) > PARTIAL_TIMEOUT:
            for text, slot in self._asm.flush():
                self._push_line(text, ansi=slot)
        self._maybe_flush(now)

    def _on_chunk(self, data: bytes) -> None:
        self._last_rx = time.perf_counter()
        self._rx_bytes += len(data)
        self._raw += data
        if len(self._raw) > RAW_LIMIT:
            del self._raw[: len(self._raw) - RAW_LIMIT]
        for text, slot in self._asm.feed(data):
            self._push_line(text, ansi=slot)

    # ------------------------------------------------------------------ 行缓冲
    def _ts_prefix(self) -> str:
        now = datetime.now()
        return now.strftime("[%H:%M:%S.") + f"{now.microsecond // 1000:03d}]"

    def _push_line(
        self,
        text: str,
        color: str | None = None,
        *,
        stamp: bool = True,
        ansi: int = -1,
    ) -> None:
        prefix_len = 0
        if stamp and self._view.timestamp:
            prefix = self._ts_prefix()
            text = prefix + text
            prefix_len = len(prefix)
        if color is None and ansi < 0:
            # 没带 ANSI 颜色的行才走关键词规则；带了就把解析留给渲染侧，
            # 好让色值跟着主题走（这里拿不到主题，也不该拿）。
            color = self._engine.color_for(text)
        self._pending.append((text, color, prefix_len, ansi))

    def _maybe_flush(self, now: float | None = None) -> None:
        if not self._pending:
            return
        now = time.perf_counter() if now is None else now
        if len(self._pending) >= BATCH_LINES or (now - self._last_emit) >= BATCH_INTERVAL:
            self._flush()

    def _flush(self) -> None:
        if not self._pending:
            return
        batch, self._pending = self._pending, []
        rx, tx = self._rx_bytes, self._tx_bytes
        self._rx_bytes = 0
        self._tx_bytes = 0
        self._last_emit = time.perf_counter()
        self.linesReady.emit(batch, False)
        if rx or tx:
            self.statsDelta.emit(rx, tx)

    # ------------------------------------------------------------------ 发送
    @Slot(bytes)
    def write(self, data: bytes) -> None:
        ser = self._ser
        if ser is None:
            self.errorOccurred.emit("串口未连接，发送被忽略")
            return
        try:
            for i in range(0, len(data), WRITE_SLICE):
                ser.write(data[i : i + WRITE_SLICE])
            ser.flush()
        except Exception as exc:  # noqa: BLE001
            self.errorOccurred.emit(f"发送失败：{_explain(exc)}")
            return
        self._tx_bytes += len(data)
        if self._view.show_tx:
            self._echo_tx(data)
        self._maybe_flush()

    def _echo_tx(self, data: bytes) -> None:
        """把发出去的数据以另一种颜色回显到日志里。"""
        if self._view.hex_mode:
            base = len(self._raw)
            for line in format_hex_block(data.rstrip(b"\r\n"), base):
                self._push_line(line, self._engine.palette.tx)
            return
        try:
            text = data.decode(self._view.encoding, errors="replace")
        except LookupError:
            text = data.decode("utf-8", errors="replace")
        for line in text.splitlines() or [""]:
            self._push_line(line, self._engine.palette.tx)

    # ------------------------------------------------------------------ 视图/规则
    def _rebuild_assembler(self) -> None:
        if self._view.hex_mode:
            self._asm = HexAssembler()
        else:
            self._asm = TextAssembler(self._view.encoding, ignore_lone_cr=self._view.ignore_lone_cr)

    @Slot(object)
    def applyView(self, view: ViewOptions) -> None:
        old = self._view
        self._view = view
        changed = (
            old.hex_mode != view.hex_mode
            or old.encoding != view.encoding
            or old.ignore_lone_cr != view.ignore_lone_cr
        )
        if changed:
            self._rebuild_assembler()
            self._replay()

    @Slot(object, bool)
    def applyRules(self, engine: RuleEngine, replay: bool = True) -> None:
        """replay=False 用在切主题：只换引擎，日志由 LogView 自己重染，别把环形缓冲重放一遍。"""
        self._engine = engine
        if replay and self._ser is not None:
            self._replay()

    def _replay(self) -> None:
        """用环形缓冲里最近的原始字节重新渲染一遍（切 Hex / 换编码 / 改着色规则时用）。"""
        self._asm.reset()
        self._pending.clear()
        buf = bytes(self._raw)
        if not buf:
            # 还没收到过任何字节：没有可重放的内容，别把用户已经看到的日志清掉
            return
        lines: list[tuple[str, int]] = []
        for i in range(0, len(buf), 32768):
            lines.extend(self._asm.feed(buf[i : i + 32768]))
        lines.extend(self._asm.flush())
        # 有 ANSI 槽位的行不预着色，交给渲染侧按当前主题翻译；否则切 Hex 再切回来
        # 彩色行会全退成正文色。
        out = [(t, None if s >= 0 else self._engine.color_for(t), 0, s) for t, s in lines]
        self.linesReady.emit(out, True)

    # ------------------------------------------------------------------ 控制线
    @Slot(bool)
    def setDtr(self, on: bool) -> None:
        self._set_line("dtr", on)

    @Slot(bool)
    def setRts(self, on: bool) -> None:
        self._set_line("rts", on)

    def _set_line(self, name: str, on: bool) -> None:
        ser = self._ser
        if ser is None:
            return
        try:
            setattr(ser, name, on)
        except Exception as exc:  # noqa: BLE001
            self.errorOccurred.emit(f"设置 {name.upper()} 失败：{_explain(exc)}")

    @Slot()
    def resetBuffer(self) -> None:
        """清屏时一起清掉环形缓冲，否则切 Hex/文本会把刚清掉的数据重新吐出来。"""
        self._raw.clear()
        self._asm.reset()
        self._pending.clear()

    @Slot(bool)
    def setBreak(self, on: bool) -> None:
        """持续 Break：保持 TX 拉低直到显式释放。

        个别 USB 转串口驱动不认 SetCommBreak（要么忽略、要么抛异常），
        这里降级成脉冲并告知用户，别把串口搞哑。
        """
        ser = self._ser
        if ser is None:
            return
        try:
            ser.break_condition = on
        except Exception as exc:  # noqa: BLE001
            if on:
                try:
                    ser.send_break(0.1)
                    self.errorOccurred.emit("该串口驱动不支持保持 Break，已降级为 100ms 脉冲")
                except Exception:  # noqa: BLE001
                    self.errorOccurred.emit(f"发送 Break 失败：{_explain(exc)}")


class SerialSession(QObject):
    """UI 侧门面：管线程生命周期、统计速率。UI 只跟它打交道。"""

    opened = Signal()
    failed = Signal(str)
    closed = Signal()
    linesReady = Signal(list, bool)
    stats = Signal(int, int, float)   # 累计收, 累计发, 收速率 B/s

    _openRequested = Signal(object)
    _closeRequested = Signal()
    _writeRequested = Signal(bytes)
    _viewRequested = Signal(object)
    _rulesRequested = Signal(object, bool)
    _dtrRequested = Signal(bool)
    _rtsRequested = Signal(bool)
    _breakRequested = Signal(bool)
    _resetRequested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread = QThread(self)
        self._thread.setObjectName("muticom-rx")
        self._worker = SerialWorker()
        self._worker.moveToThread(self._thread)

        self._openRequested.connect(self._worker.openPort)
        self._closeRequested.connect(self._worker.closePort)
        self._writeRequested.connect(self._worker.write)
        self._viewRequested.connect(self._worker.applyView)
        self._rulesRequested.connect(self._worker.applyRules)
        self._dtrRequested.connect(self._worker.setDtr)
        self._rtsRequested.connect(self._worker.setRts)
        self._breakRequested.connect(self._worker.setBreak)
        self._resetRequested.connect(self._worker.resetBuffer)

        self._worker.opened.connect(self._on_opened)
        self._worker.openFailed.connect(self.failed)
        self._worker.closed.connect(self._on_closed)
        self._worker.linesReady.connect(self.linesReady)
        self._worker.errorOccurred.connect(self.failed)
        self._worker.statsDelta.connect(self._on_stats)

        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()

        self._rx_total = 0
        self._tx_total = 0
        self._rx_window = 0
        self._is_open = False
        self._rate_timer = QTimer(self)
        self._rate_timer.setInterval(500)
        self._rate_timer.timeout.connect(self._emit_rate)

    # ------------------------------------------------------------------ 对外
    @property
    def is_open(self) -> bool:
        return self._is_open

    def open(self, params: PortParams, view: ViewOptions) -> None:
        self._viewRequested.emit(view.copy())
        self._openRequested.emit(params)

    def close(self) -> None:
        self._closeRequested.emit()

    def send(self, payload: bytes) -> None:
        if payload:
            self._writeRequested.emit(payload)

    def apply_view(self, view: ViewOptions) -> None:
        self._viewRequested.emit(view.copy())

    def apply_rules(self, engine: RuleEngine, replay: bool = True) -> None:
        self._rulesRequested.emit(engine, replay)

    def set_dtr(self, on: bool) -> None:
        self._dtrRequested.emit(on)

    def set_rts(self, on: bool) -> None:
        self._rtsRequested.emit(on)

    def set_break(self, on: bool) -> None:
        self._breakRequested.emit(on)

    def reset_buffer(self) -> None:
        self._resetRequested.emit()

    def shutdown(self, timeout_ms: int = 2000) -> bool:
        """关窗口/删列时调用：先让 worker 关串口，再退出线程。"""
        if not self._thread.isRunning():
            return True
        self._rate_timer.stop()
        self._closeRequested.emit()
        self._thread.quit()
        ok = self._thread.wait(timeout_ms)
        if not ok:
            print(f"[MutiCom] 警告：串口线程 {self._thread.objectName()} 未在 {timeout_ms}ms 内退出")
        return ok

    # ------------------------------------------------------------------ 内部
    @Slot()
    def _on_closed(self) -> None:
        self._is_open = False
        self._rate_timer.stop()
        self._rx_window = 0
        self.closed.emit()

    @Slot()
    def _on_opened(self) -> None:
        self._is_open = True
        self._rx_total = self._tx_total = self._rx_window = 0
        self._rate_timer.start()
        self.opened.emit()
    @Slot(int, int)
    def _on_stats(self, rx: int, tx: int) -> None:
        self._rx_total += rx
        self._tx_total += tx
        self._rx_window += rx

    @Slot()
    def _emit_rate(self) -> None:
        rate = self._rx_window / 0.5
        self._rx_window = 0
        self.stats.emit(self._rx_total, self._tx_total, rate)


__all__ = ["SerialSession", "SerialWorker"]
