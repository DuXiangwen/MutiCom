"""端到端自测：用 ELTIMA 虚拟串口对 COM253<->COM254 验证收发全链路。

不需要真实硬件。跑法：
    C:\\Python314\\python.exe tools\\selftest_serial.py

步骤：
1. 第一列连 COM254，feeder 往 COM253 灌混合日志 → 检查收数据、中文、着色
2. feeder 回环模式（读 COM254 写回 COM253）→ 自己发的数据能收回来，检查 Tx/Rx 双向
3. 第二列连 COM253，从 COM254 列发一条 → 检查对端列收到
4. 切 Hex 视图检查偏移和 ASCII 列
5. 关窗，检查串口线程干净退出
"""

from __future__ import annotations

import subprocess
import re
import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from muticom.app_config import AppConfig, ColumnConfig, ConfigStore, PortParams  # noqa: E402
from muticom.main_window import MainWindow  # noqa: E402
from muticom.ui_kit import apply_theme  # noqa: E402

DEV = "COM253"
MON = "COM254"
FEEDER = str(PROJECT / "tools" / "fake_log_feeder.py")


def pump(app: QApplication, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.008)


def spawn(args: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, FEEDER, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def main() -> int:
    app = QApplication(sys.argv[:1])
    app.setApplicationName("MutiComTest")
    app.setOrganizationName("dx1")
    apply_theme(app)

    store = ConfigStore(Path(tempfile.mkdtemp()) / "config.json")
    cfg = AppConfig(
        columns=[
            ColumnConfig(params=PortParams(port=MON)),
            ColumnConfig(params=PortParams(port=DEV)),
        ]
    )
    win = MainWindow(store, cfg)
    win.show()
    pump(app, 0.5)

    col_mon, col_dev = win.area.columns
    failures: list[str] = []

    # ---- 1. 收数据 -----------------------------------------------------
    col_mon.connect_now()
    pump(app, 0.8)
    print(f"[1] {MON} 连接={col_mon.is_connected}")
    if not col_mon.is_connected:
        failures.append(f"{MON} 没连上")

    feeder = spawn(["--port", DEV, "--rate", "180", "--duration", "3", "--mode", "mixed"])
    pump(app, 3.8)
    out = feeder.communicate(timeout=10)[0]
    if "打不开" in out:
        failures.append(f"feeder 打不开 {DEV}: {out.strip()}")

    text = col_mon.log.toPlainText()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    print(f"[1] 收到 {len(lines)} 行，前 3 行：")
    for ln in lines[:3]:
        print("    ", ln)
    if len(lines) < 100:
        failures.append(f"收数据太少：{len(lines)} 行")
    if "中文日志测试" not in text:
        failures.append("中文没有正确解码")
    if "[I]" not in text or "[W]" not in text:
        failures.append("日志级别标签缺失")
    if "crc error" not in text:
        failures.append("没有收到 error 行")

    # ---- 2. 两列互通（同时也是 Tx 通路的验证）-----------------------
    col_dev.connect_now()
    pump(app, 1.0)
    print(f"[2] {DEV} 连接={col_dev.is_connected}")
    if not col_dev.is_connected:
        failures.append(f"{DEV} 没连上（另一列还占着这个口？）")
    col_dev.log.clear_log()
    before_tx = col_mon._tx_total
    col_mon._on_send("CMD=PING\r\n".encode())
    pump(app, 1.2)
    dev_text = col_dev.log.toPlainText()
    print(f"[2] {DEV} 收到：{dev_text.strip()!r}   Tx {before_tx}->{col_mon._tx_total}")
    if "CMD=PING" not in dev_text:
        failures.append("跨列发送没到达对端")
    if col_mon._tx_total <= before_tx:
        failures.append("Tx 计数没有增长")

    # ---- 3. Hex 视图 ---------------------------------------------------
    col_mon.view.hex_mode = True
    col_mon._on_view_changed()
    pump(app, 0.5)
    hex_lines = [ln for ln in col_mon.log.toPlainText().splitlines() if ln.strip()]
    print("[3] Hex 视图前 2 行：")
    for ln in hex_lines[:2]:
        print("    ", ln)
    # 切 Hex 后"已连接"这类系统行会保留在最前面，先挑出真正的 hexdump 行再判格式
    dump = [ln for ln in hex_lines if re.match(r"^[0-9A-F]{8}  ", ln)]
    if not dump:
        failures.append("Hex 视图里没有 hexdump 行")
    elif "|" not in dump[0]:
        failures.append("Hex 视图没有 ASCII 预览列")
    elif len(dump[0].split()[0]) != 8:
        failures.append("Hex 视图偏移不是 8 位")
    col_mon.view.hex_mode = False
    col_mon._on_view_changed()
    pump(app, 0.4)

    # ---- 4. 退出 -------------------------------------------------------
    win.close()
    pump(app, 0.5)
    deadline = time.time() + 4
    alive = [c.cfg.uid for c in win.area.columns if c.session._thread.isRunning()]
    while alive and time.time() < deadline:
        pump(app, 0.2)
        alive = [c.cfg.uid for c in win.area.columns if c.session._thread.isRunning()]
    if alive:
        failures.append(f"串口线程没退干净: {alive}")
    print(f"[4] 线程退出={'OK' if not alive else 'FAIL'}")

    print("\n================ 结果 ================")
    if failures:
        for f in failures:
            print("  [FAIL]", f)
        return 1
    print("  全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
