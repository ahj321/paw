#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai-pc-control —— 用命令行控制鼠标和键盘，配合视觉模型做 Windows 桌面自动化。

一个最小、无依赖滥用的命令行工具：截图 / 点击 / 输入 / 找图。
给任何「能看图的模型 + 能跑命令的 agent」当手用。

示例：
    python pc.py size                       # 屏幕分辨率
    python pc.py shot screen.png            # 截屏
    python pc.py click 960 540              # 左键单击
    python pc.py type "hello"               # 输入文字
    python pc.py find button.png 0.9        # 屏幕上找图，返回坐标
    python pc.py click 960 540 --level read # 只允许只读动作时会拒绝
    python pc.py click 960 540 --dry-run    # 只打印行动表，不执行

权限等级（--level 或环境变量 PC_LEVEL，默认 full）：
    read   只能 size / shot / windows / find
    move   再加 move
    click  再加 click / dclick / rclick / scroll
    type   再加 type / key / hotkey
    full   全部

行动表：每次执行前会打印一行「计划：…」，说明接下来要做什么；
用 --no-plan 关掉，用 --dry-run 只看计划不执行（预览模式）。

安全：动作执行前检查鼠标左右键是否同时按住——同时按住即视为急停。
所有动作之间默认间隔 0.3s。
"""

import argparse
import ctypes
import os
import sys
import time

import pyautogui

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.3

LEVEL_ORDER = {"read": 0, "move": 1, "click": 2, "type": 3, "full": 4}
CMD_LEVEL = {
    "size": "read", "shot": "read", "windows": "read", "find": "read",
    "move": "move",
    "click": "click", "dclick": "click", "rclick": "click", "scroll": "click",
    "type": "type", "key": "type", "hotkey": "type",
}


def _emergency_pressed() -> bool:
    """左右键同时按住 = 急停。"""
    try:
        user32 = ctypes.windll.user32
        left = user32.GetAsyncKeyState(0x01) & 0x8000
        right = user32.GetAsyncKeyState(0x02) & 0x8000
        return bool(left and right)
    except Exception:
        return False


def _level_allows(level: str, cmd: str) -> bool:
    need = LEVEL_ORDER[CMD_LEVEL[cmd]]
    return LEVEL_ORDER[level] >= need


def _plan_text(cmd: str, args: list) -> str:
    """给人类看的行动表：这一步打算做什么。"""
    if cmd == "size":
        return "读取屏幕分辨率（不改任何东西）"
    if cmd == "shot":
        out = args[0] if args else "screen.png"
        return f"截屏并保存到 {out}（不改任何东西）"
    if cmd == "windows":
        return "列出当前窗口和位置（不改任何东西）"
    if cmd == "find":
        tpl = args[0] if args else "?"
        return f"在屏幕上查找图片 {tpl}，返回坐标（不点击）"
    if cmd == "move":
        return f"把鼠标移动到 ({args[0]}, {args[1]})，不点击"
    if cmd == "click":
        return f"在 ({args[0]}, {args[1]}) 单击鼠标左键"
    if cmd == "dclick":
        return f"在 ({args[0]}, {args[1]}) 双击鼠标左键"
    if cmd == "rclick":
        return f"在 ({args[0]}, {args[1]}) 单击鼠标右键"
    if cmd == "scroll":
        return f"滚动滚轮 {args[0]} 格"
    if cmd == "type":
        return f"输入文本：{' '.join(args)[:60]}"
    if cmd == "key":
        return f"按一次键：{args[0]}"
    if cmd == "hotkey":
        return f"按组合键：{' + '.join(args)}"
    return cmd


def main() -> int:
    ap = argparse.ArgumentParser(description="control the mouse and keyboard from the CLI")
    ap.add_argument(
        "cmd",
        choices=[
            "size", "shot", "click", "dclick", "rclick", "move",
            "type", "key", "hotkey", "scroll", "find", "windows",
        ],
    )
    ap.add_argument("args", nargs="*")
    ap.add_argument("--delay", type=float, default=0.0, help="动作前等待秒数")
    ap.add_argument(
        "--level",
        default=os.environ.get("PC_LEVEL", "full"),
        choices=list(LEVEL_ORDER.keys()),
        help="权限等级：read / move / click / type / full（默认 full，可用 PC_LEVEL 环境变量）",
    )
    ap.add_argument("--no-plan", action="store_true", help="不打印行动表")
    ap.add_argument("--dry-run", action="store_true", help="只打印行动表，不执行")
    a = ap.parse_args()

    if a.delay:
        time.sleep(a.delay)

    if not _level_allows(a.level, a.cmd):
        print(
            f"权限不足：当前等级 {a.level}，{a.cmd} 需要 {CMD_LEVEL[a.cmd]} 级权限",
            file=sys.stderr,
        )
        return 3

    if not a.no_plan:
        print(f"计划：{_plan_text(a.cmd, a.args)}", file=sys.stderr)

    if a.dry_run:
        print("（dry-run：只预览，未执行）", file=sys.stderr)
        return 0

    if a.cmd not in ("size", "shot", "windows") and _emergency_pressed():
        print("急停：检测到鼠标左右键同时按下，已中止", file=sys.stderr)
        return 1

    try:
        if a.cmd == "size":
            print(pyautogui.size())

        elif a.cmd == "shot":
            out = a.args[0] if a.args else "screen.png"
            img = pyautogui.screenshot()
            img.save(out)
            print(out, img.size)

        elif a.cmd == "click":
            pyautogui.click(int(a.args[0]), int(a.args[1]))
            print("clicked", a.args[0], a.args[1])

        elif a.cmd == "dclick":
            pyautogui.doubleClick(int(a.args[0]), int(a.args[1]))
            print("double-clicked", a.args[0], a.args[1])

        elif a.cmd == "rclick":
            pyautogui.rightClick(int(a.args[0]), int(a.args[1]))
            print("right-clicked", a.args[0], a.args[1])

        elif a.cmd == "move":
            pyautogui.moveTo(int(a.args[0]), int(a.args[1]), duration=0.3)
            print("moved", a.args[0], a.args[1])

        elif a.cmd == "type":
            text = " ".join(a.args)
            if any("\u4e00" <= ch <= "\u9fff" for ch in text):
                import tkinter as tk

                root = tk.Tk()
                root.withdraw()
                root.clipboard_clear()
                root.clipboard_append(text)
                root.update()
                pyautogui.hotkey("ctrl", "v")
                root.destroy()
            else:
                pyautogui.typewrite(text, interval=0.02)
            print("typed")

        elif a.cmd == "key":
            pyautogui.press(a.args[0])
            print("key", a.args[0])

        elif a.cmd == "hotkey":
            pyautogui.hotkey(*a.args)
            print("hotkey", " ".join(a.args))

        elif a.cmd == "scroll":
            pyautogui.scroll(int(a.args[0]))
            print("scrolled", a.args[0])

        elif a.cmd == "find":
            conf = float(a.args[1]) if len(a.args) > 1 else 0.85
            loc = pyautogui.locateOnScreen(a.args[0], confidence=conf)
            print(loc if loc else "not found")

        elif a.cmd == "windows":
            try:
                import pygetwindow as gw
            except ImportError:
                print("需要 pygetwindow：pip install pygetwindow", file=sys.stderr)
                return 1
            for w in gw.getAllWindows():
                title = (w.title or "").strip()
                if title:
                    print(f"{w.left:>6} {w.top:>6} {w.width:>6} {w.height:>6}  {title}")

    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except pyautogui.FailSafeException:
        print("failsafe: 已中止", file=sys.stderr)
        return 1
    except (IndexError, ValueError) as e:
        print(f"参数错误：{e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
