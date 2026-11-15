"""往串口灌假日志，用于没有硬件时自测 MutiCom。

典型用法（本程序打开 COM254 看，脚本往 COM253 灌）：
    python tools/fake_log_feeder.py --port COM253 --rate 200 --duration 20
    python tools/fake_log_feeder.py --port COM253 --mode raw --rate 100
    python tools/fake_log_feeder.py --port COM253 --burst 1048576 --duration 1
    python tools/fake_log_feeder.py --port COM253 --echo-port COM254      # 回环
"""

from __future__ import annotations

import argparse
import random
import sys
import time

import serial

BOOT = [
    "==== ****** boot cpu: scid log ready FLASH FPGA ****** ====",
    "==== ****** boot cpu: scid log ready ****** ====",
    "[CPU2] boot cpu2: init done..",
    "[CPU2] boot cpu2: app_status=0x22..",
    "boot cpu1: step1.5 cpu2 alive, state=0x22",
    "boot cpu1: step2 upgrade check, cl1_valid=0, rc=0",
    "boot cpu1: step4 stay in boot, ymodem on scib",
]

MIXED = [
    "[I][MAIN] comm mode -> MODBUS",
    "[I][DSPTX] send: type=0x000C002 file=\"CSD-5831_V2_DSP(CPU1)_V1.112_271118\"",
    "[I][DSPTX] state -> CMD_A1",
    "[I][DSPTX] state -> CMD_A1_WAIT",
    "[I][DSPTX] rx[a1] n=1: 43 00 00 00 00 00 00 00",
    "[I][YMODEM] phase -> WAIT_C",
    "[I][YMODEM] block0: 01 00 FF \"CSD-5831_V2_DSP(CPU1)_V1.112_271118\" size=36796",
    "[W][YMODEM] block0 nak, retry=1",
    "[W][DSPTX] downlink fail (ymodem session): type=0x000C002 -> UPGRADE_FAILED",
    "[I][DSPTX] state -> DONE",
    "[D] app check: head crc error, base=0x88000, rc=4",
    "[I] upgrade check: no pending file",
    "[I] ym: block0 unknown file, cancel",
    "[I] 中文日志测试：升级失败，重试第 3 次",
    "[I] 温度 26.5C 电压 3.31V 电流 1.02A",
    "[I] init done, ok",
    "[I] sensor read success, 4 channels ready",
]

PROGRESS = ["\rdownload {:3d}% [{}{}]"]


def now_stamp() -> str:
    t = time.time()
    lt = time.localtime(t)
    return time.strftime("%H:%M:%S", lt) + f".{int((t % 1) * 1000):03d}"


def build_line(mode: str, counter: int) -> bytes:
    if mode == "mixed":
        tpl = MIXED[counter % len(MIXED)]
        if counter % 37 == 0:
            return (BOOT[counter % len(BOOT)] + "\r\n").encode("utf-8")
        if counter % 120 == 0:
            # 超长行，验证 8K 切断和折行
            return ("[X] " + "0123456789abcdef" * 90 + "\r\n").encode("utf-8")
        enc = "utf-8" if counter % 7 else "gbk"
        line = tpl
        try:
            return (line + "\r\n").encode(enc)
        except UnicodeEncodeError:
            return (line + "\r\n").encode("utf-8")
    if mode == "progress":
        pct = counter % 101
        bar = "#" * (pct // 4)
        return PROGRESS[0].format(pct, bar, " " * (25 - len(bar))).encode()
    if mode == "raw":
        n = random.randint(8, 64)
        return bytes(random.randrange(256) for _ in range(n))
    if mode == "junk":
        return bytes(random.randrange(256) for _ in range(random.randint(16, 128)))
    return b"hello muticom\r\n"


def echo_loop(src_port: str, dst: serial.Serial, baud: int) -> None:
    """把 src 端口收到的内容原样转发到 dst，用来验证「发出去又收回来」。"""
    src = serial.Serial(src_port, baud, timeout=0)
    print(f"[feeder] 回环模式：{src_port} -> {dst.port}，Ctrl+C 结束")
    try:
        while True:
            n = src.in_waiting
            if n:
                dst.write(src.read(n))
            else:
                time.sleep(0.002)
    except KeyboardInterrupt:
        pass
    finally:
        src.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="往串口灌假日志")
    ap.add_argument("--port", default="COM253", help="要写入的端口（设备侧）")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--mode", choices=["mixed", "raw", "progress", "junk", "plain"], default="mixed")
    ap.add_argument("--rate", type=float, default=200, help="每秒多少条（0 = 不限速，能多快写多快）")
    ap.add_argument("--duration", type=float, default=0, help="跑多少秒，0 = 一直跑")
    ap.add_argument("--burst", type=int, default=0, help="先一次性写 N 字节，用来压突发")
    ap.add_argument("--echo-port", default="", help="回环模式：读这个端口并转发到 --port")
    args = ap.parse_args()

    try:
        ser = serial.Serial(args.port, args.baud, timeout=0, write_timeout=5)
    except Exception as exc:  # noqa: BLE001
        print(f"[feeder] 打不开 {args.port}: {exc}")
        return 1

    print(f"[feeder] {args.port} @ {args.baud} mode={args.mode} rate={args.rate}/s")

    if args.echo_port:
        echo_loop(args.echo_port, ser, args.baud)
        ser.close()
        return 0

    if args.burst:
        payload = (bytes(random.randrange(256) for _ in range(1024)) * (args.burst // 1024 + 1))[: args.burst]
        t0 = time.perf_counter()
        ser.write(payload)
        ser.flush()
        print(f"[feeder] burst {args.burst} 字节，用时 {(time.perf_counter() - t0) * 1000:.1f} ms")

    start = time.perf_counter()
    counter = 0
    next_t = start
    try:
        while True:
            line = build_line(args.mode, counter)
            ser.write(line)
            counter += 1
            if args.duration and time.perf_counter() - start > args.duration:
                break
            if args.rate > 0:
                next_t += 1.0 / args.rate
                delay = next_t - time.perf_counter()
                if delay > 0:
                    time.sleep(delay)
                elif delay < -0.5:
                    next_t = time.perf_counter()  # 落后太多就重设基准，别追命
            if counter % 200 == 0:
                print(f"\r[feeder] {counter} 条 / {time.perf_counter() - start:.1f}s", end="", flush=True)
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"\n[feeder] 写入出错: {exc}")
    finally:
        ser.close()
        print(f"\n[feeder] 共发送 {counter} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
