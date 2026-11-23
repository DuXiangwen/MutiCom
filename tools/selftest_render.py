"""渲染层自测：ANSI 槽位有没有真的变成屏幕上的颜色，以及换主题后还在不在。

offscreen 跑，不需要显示器、不需要串口、不需要 qtpilot：
    C:\\Python314\\python.exe tools\\selftest_render.py

这是唯一能钉死「蓝色到底是 #0550ae 还是 #0969da」的手段 —— 截图只能看出「有颜色」，
看不出具体色值。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from muticom import theme  # noqa: E402
from muticom.ansi import ANSI_NONE  # noqa: E402
from muticom.app_config import ViewOptions  # noqa: E402
from muticom.colorizer import LogPalette, RuleEngine  # noqa: E402
from muticom.log_view import LogView  # noqa: E402

failures: list[str] = []


def t(name: str, cond: bool, detail: object = "") -> None:
    if cond:
        print(f"  [ OK ] {name}")
    else:
        print(f"  [FAIL] {name}   {detail!r}")
        failures.append(name)


def block_info(view: LogView, i: int) -> tuple[str, int]:
    """第 i 个 block 的前景色（hex）和挂在上面的 ANSI 槽位。"""
    b = view.document().findBlockByNumber(i)
    if not b.isValid():
        return "<invalid>", -1
    it = b.begin()
    if it.atEnd():
        return "<empty>", b.userState()
    return it.fragment().charFormat().foreground().color().name(), b.userState()


def run() -> int:
    app = QApplication([])
    light = RuleEngine(palette=LogPalette.from_tokens(theme.LIGHT))
    dark = RuleEngine(palette=LogPalette.from_tokens(theme.DARK))

    view = LogView(ViewOptions(), light)
    view.append_lines(
        [
            ("modem: seq=52", None, 0, 4),                                   # ANSI 蓝
            ("[E] upgrade failed", light.color_for("[E] upgrade failed"), 0, ANSI_NONE),  # 关键词
        ]
    )
    view._flush()

    print("[1] 浅色主题")
    color0, slot0 = block_info(view, 0)
    t("1 ANSI 蓝渲染成 #0550ae", color0 == light.palette.ansi[4] == "#0550ae", color0)
    t("2 槽位挂到 block.userState", slot0 == 4, slot0)

    color1, slot1 = block_info(view, 1)
    t("3 无 ANSI 行走关键词规则（error→红）", color1 == light.palette.danger, color1)
    t("4 无 ANSI 行不挂槽位", slot1 == ANSI_NONE, slot1)

    print("[2] 换主题")
    view.retint(dark)
    color0d, slot0d = block_info(view, 0)
    t("5 深色下 ANSI 蓝跟着变", color0d == dark.palette.ansi[4] == "#6cb0f5", color0d)
    t("6 换主题后槽位仍在", slot0d == 4, slot0d)
    color1d, _ = block_info(view, 1)
    t("7 关键词色也跟着换", color1d == dark.palette.danger, color1d)

    view.retint(light)
    color0b, slot0b = block_info(view, 0)
    t("8 切回浅色能还原", color0b == "#0550ae" and slot0b == 4, (color0b, slot0b))

    print("[3] 行数上限裁头")
    view2 = LogView(ViewOptions(max_lines=3), light)
    view2.append_lines(
        [
            ("line-0 blue", None, 0, 4),
            ("line-1 red", None, 0, 1),
            ("line-2 green", None, 0, 2),
            ("line-3 yellow", None, 0, 3),
            ("line-4 cyan", None, 0, 6),
        ]
    )
    view2._flush()
    doc2 = view2.document()
    t("9 裁头后只剩 3 行", doc2.blockCount() == 3, doc2.blockCount())
    last_color, last_slot = block_info(view2, doc2.blockCount() - 1)
    t("10 裁头后末行槽位仍对（6=cyan）", last_slot == 6 and last_color == light.palette.ansi[6],
      (last_color, last_slot))
    first_color, first_slot = block_info(view2, 0)
    t("11 裁头后首行槽位仍对（2=green）", first_slot == 2 and first_color == light.palette.ansi[2],
      (first_color, first_slot))

    print("[4] 导出文本干净")
    text = view.toPlainText()
    t("12 toPlainText 不含 ESC", "\x1b" not in text, repr(text[:40]))
    t("13 文本内容完整", "modem: seq=52" in text and "upgrade failed" in text, repr(text[:60]))

    print("\n================ 结果 ================")
    if failures:
        for f in failures:
            print("  [FAIL]", f)
        return 1
    print("  全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
