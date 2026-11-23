"""ANSI 解析自测：不需要串口、不需要 QApplication，纯逻辑。

跑法：
    C:\\Python314\\python.exe tools\\selftest_ansi.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from muticom import theme  # noqa: E402
from muticom.ansi import ANSI_NONE, SLOT_NAMES  # noqa: E402
from muticom.colorizer import LogPalette, RuleEngine  # noqa: E402
from muticom.decoder import HexAssembler, TextAssembler  # noqa: E402

failures: list[str] = []


def t(name: str, cond: bool, detail: object = "") -> None:
    if cond:
        print(f"  [ OK ] {name}")
    else:
        print(f"  [FAIL] {name}   {detail!r}")
        failures.append(name)


def feed_all(data: bytes, encoding: str = "utf-8") -> list[tuple[str, int]]:
    return TextAssembler(encoding).feed(data)


def clean(lines: list[tuple[str, int]]) -> bool:
    """所有输出文本都不该残留 ESC。"""
    return all("\x1b" not in text for text, _ in lines)


def run() -> int:
    print("[1] 基本解析")
    r = feed_all(b"\x1b[0m\x1b[34mmodem: seq=52\r\n")
    t("1 双前缀 → 剥干净 + 蓝", r == [("modem: seq=52", 4)] and clean(r), r)

    a = TextAssembler()
    r = a.feed(b"\x1b[34mA\r\nB\r\n")
    t("2 颜色跨行持续", r == [("A", 4), ("B", 4)], r)

    a = TextAssembler()
    r = a.feed(b"\x1b[34mA\r\n\x1b[0mB\r\n")
    t("3 行尾 reset 生效", r == [("A", 4), ("B", ANSI_NONE)], r)

    print("[2] 无 ANSI 回归")
    src = "升级失败，重试第 3 次\r\n[I] init done, ok\r\n"
    r = feed_all(src.encode("utf-8"))
    t("4a UTF-8 原样通过", clean(r) and all(s == ANSI_NONE for _, s in r) and "升级失败" in r[0][0], r)
    r = feed_all(src.encode("gbk"), encoding="gbk")
    t("4b GBK 原样通过", clean(r) and all(s == ANSI_NONE for _, s in r) and "升级失败" in r[0][0], r)

    print("[3] 跨包截断")
    a = TextAssembler()
    r1 = a.feed(b"\x1b[3")
    r2 = a.feed(b"4mhi\r\n")
    t("5 断在转义中间能还原", r1 == [] and r2 == [("hi", 4)] and clean(r2), (r1, r2))

    a = TextAssembler()
    r1 = a.feed(b"abc\x1b[")
    r2 = a.feed(b"31mdef\r\n")
    # abc 出现在设色之前，首个可见字符是 a，所以整行取无色 —— 这是「首个可见字符定色」的直接后果
    t("6a 断在文本中间（颜色在后）", r1 == [] and r2 == [("abcdef", ANSI_NONE)], (r1, r2))

    a = TextAssembler()
    r1 = a.feed(b"\x1b[")
    r2 = a.feed(b"31mdef\r\n")
    t("6b 断在文本中间（颜色在前）", r1 == [] and r2 == [("def", 1)], (r1, r2))

    print("[4] flush 半行")
    a = TextAssembler()
    a.feed(b"\x1b[34mabc")
    r = a.flush()
    t("7 半行按已有颜色推出去", r == [("abc", 4)], r)

    a = TextAssembler()
    a.feed(b"\x1b[3")
    r_flush = a.flush()
    r_next = a.feed(b"4mhi\r\n")
    t("8 半截转义不被 flush 吐出去", r_flush == [] and r_next == [("hi", 4)], (r_flush, r_next))

    print("[5] 色深降级")
    cases = [
        (b"\x1b[38;5;196mR\r\n", (9, 12), "256色 196"),
        (b"\x1b[38;5;21mB\r\n", (4, 12), "256色 21"),
        (b"\x1b[38;5;244mG\r\n", (7, 8, 15), "256色 244"),
        (b"\x1b[38;2;0;0;255mT\r\n", (4, 12), "真彩 0,0,255"),
    ]
    for raw, allowed, label in cases:
        r = feed_all(raw)
        ok = len(r) == 1 and r[0][1] in allowed and clean(r) and "38;" not in r[0][0]
        t(f"9/10 {label} → slot {r[0][1] if r else '?'}", ok, r)

    print("[6] 组合与边界")
    r = feed_all(b"\x1b[0m\x1b[1m\x1b[34mX\r\n")
    # 1m 是加粗位，和 34 叠加就是「亮蓝」，这是终端标准语义
    t("11 连续序列（粗体+蓝=亮蓝）", r == [("X", 12)], r)

    a = TextAssembler()
    r1 = a.feed(b"\x1b[31mA\x1b[32mB\x1b[0mC\r\n")
    r2 = a.feed(b"D\r\n")
    t("12 一行多色取首个", r1 == [("ABC", 1)] and r2 == [("D", ANSI_NONE)], (r1, r2))

    r = feed_all(b"\x1b[0m\r\n")
    t("13 纯转义行仍出行", r == [("", ANSI_NONE)], r)

    r = feed_all(b"\r\n\r\n")
    t("14 空行不丢", r == [("", ANSI_NONE), ("", ANSI_NONE)], r)

    r = feed_all(b"\x1b[34mA\x1b[39mB\r\n")
    # 39 只清前景色；A 是首个可见字符所以取蓝，状态随后被清掉
    t("16 39m 只清前景色", r == [("AB", 4)], r)

    a = TextAssembler()
    a.feed(b"\x1b[34m\r\n")
    r = a.feed(b"\r\n")
    t("20 空行不继承颜色", r == [("", ANSI_NONE)], r)

    print("[7] 超长行")
    a = TextAssembler()
    r1 = a.feed(b"\x1b[34m" + b"A" * 20000)
    r2 = a.flush()
    lens = [len(x) for x, _ in r1] + [len(x) for x, _ in r2]
    slots = {s for _, s in r1 + r2}
    t("15 残片切块 8187/8192/3621", lens == [8187, 8192, 3621] and slots == {4}, (lens, slots))
    t("15b 没有块以 ESC 开头", all(not x.startswith("\x1b") for x, _ in r1 + r2), [x[:6] for x, _ in r1 + r2])

    r = feed_all(b"\x1b[34m" + b"A" * 20000 + b"\r\n")
    t("18 含换行的超长行不切块", len(r) == 1 and len(r[0][0]) == 20000 and r[0][1] == 4, len(r))

    print("[8] Hex 模式")
    h = HexAssembler()
    r = h.feed(b"\x1b[34mAB\r\n")
    t("19 Hex 不解析颜色、字节可见",
      len(r) == 1 and r[0][1] == ANSI_NONE and "1B 5B 33 34 6D" in r[0][0] and clean(r), r)

    print("[9] 主题令牌")
    missing = [
        f"ansi.{n}"
        for n in SLOT_NAMES
        if f"ansi.{n}" not in theme.LIGHT or f"ansi.{n}" not in theme.DARK
    ]
    t("17a 16 个 ansi.* 令牌齐全", not missing, missing)

    try:
        RuleEngine()          # 走 _FALLBACK_TOKENS，缺项会 KeyError
        fallback_ok = True
    except KeyError as exc:
        fallback_ok = False
        print("      fallback KeyError:", exc)
    t("17b 无主题兜底构造", fallback_ok)

    low = []
    for name, tokens in (("light", theme.LIGHT), ("dark", theme.DARK)):
        pal = LogPalette.from_tokens(tokens)
        bg = tokens["bg.log"]
        for i, n in enumerate(SLOT_NAMES):
            if i == 0:        # ansi.black 就是「最暗的前景色」，天然过不了 4.5，白名单
                continue
            ratio = theme.contrast_ratio(pal.ansi[i], bg)
            if ratio < 4.5:
                low.append((name, n, round(ratio, 2)))
    t("17c 对比度 ≥4.5:1（ansi.black 豁免）", not low, low)

    print("\n================ 结果 ================")
    if failures:
        for f in failures:
            print("  [FAIL]", f)
        return 1
    print("  全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
