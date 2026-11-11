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
    python pc.py dclick 960 540             # 双击
    python pc.py type "hello"               # 输入英文
    python pc.py type "你好"                # 输入中文（走剪贴板）
    python pc.py hotkey ctrl s              # 组合键
    python pc.py scroll -3                  # 向下滚
    python pc.py find button.png 0.9        # 屏幕上找图，返回坐标

安全：动作执行前检查鼠标左右键是否同时按住——同时按住即视为急停，直接退出，
避免自动化失控。所有动作之间有 0.3s 间隔。
"""

import argparse
import ctypes
import sys
import time

import pyautogui

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.3


def _emergency_pressed() -> bool:
    """左右键同时按住 = 急停。"""
    try:
        user32 = ctypes.windll.user32
        left = user32.GetAsyncKeyState(0x01) & 0x8000
        right = user32.GetAsyncKeyState(0x02) & 0x8000
        return bool(left and right)
    except Exception:
        return False


def _type_text(text: str) -> None:
    """英文直接打；含中文走剪贴板 + Ctrl+V，兼容 Windows 输入法。"""
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


def _list_windows() -> None:
    try:
        import pygetwindow as gw
    except ImportError:
        print("需要 pygetwindow：pip install pygetwindow", file=sys.stderr)
        return
    for w in gw.getAllWindows():
        title = (w.title or "").strip()
        if title:
            print(f"{w.left:>6} {w.top:>6} {w.width:>6} {w.height:>6}  {title}")


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
    a = ap.parse_args()

    if a.delay:
        time.sleep(a.delay)

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
            _type_text(" ".join(a.args))
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
            _list_windows()

    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except pyautogui.FailSafeException:
        print("failsafe: 鼠标在屏幕左上角，已中止", file=sys.stderr)
        return 1
    except (IndexError, ValueError) as e:
        print(f"参数错误：{e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
