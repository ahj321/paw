#!/usr/bin/env python3
"""paw 的 MCP server：把 paw.py 的命令包成 MCP tools，任何支持 MCP 的客户端都能用。

用法（stdio）：
    python paw_mcp.py

在客户端里配置（例如 Claude Desktop 的 claude_desktop_config.json）：

    {
      "mcpServers": {
        "paw": {
          "command": "python",
          "args": ["D:/ai work/ai-pc-control/paw_mcp.py"]
        }
      }
    }
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

PC = Path(__file__).with_name("paw.py")
mcp = FastMCP("paw")


def _run(*args: str, timeout: int = 120) -> str:
    """调用 paw.py，返回它的标准输出（失败时返回错误信息）。"""
    cmd = [sys.executable, str(PC), *args, "--no-plan"]
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return f"超时（{timeout} 秒）：{' '.join(args)}"
    out = (p.stdout or "").strip()
    err = (p.stderr or "").strip()
    if p.returncode != 0:
        return f"失败（exit {p.returncode}）：{err or out}"
    return out or "ok"


@mcp.tool()
def screen_size() -> str:
    """屏幕的物理像素尺寸，例如 (3840, 2160)。"""
    return _run("size")


@mcp.tool()
def display_scale() -> str:
    """显示缩放比例，例如 1.50x（150%）。截屏坐标是物理像素。"""
    return _run("scale")


@mcp.tool()
def screenshot(path: str = "screen.png", crop: str = "", jpg: bool = False) -> str:
    """截屏并保存到 path。

    crop 形如 "x,y,w,h"（物理像素），只截取该区域。
    """
    args = ["shot", path]
    if crop:
        args += ["--crop", crop]
    if jpg:
        args.append("--jpg")
    return _run(*args)


@mcp.tool()
def click(x: int, y: int, double: bool = False, right: bool = False) -> str:
    """在 (x, y) 点击鼠标。x、y 是截图的物理像素坐标。"""
    if double:
        return _run("dclick", str(x), str(y))
    if right:
        return _run("rclick", str(x), str(y))
    return _run("click", str(x), str(y))


@mcp.tool()
def move(x: int, y: int) -> str:
    """把鼠标移到 (x, y)，不点击。"""
    return _run("move", str(x), str(y))


@mcp.tool()
def drag(x1: int, y1: int, x2: int, y2: int) -> str:
    """从 (x1, y1) 拖到 (x2, y2)。"""
    return _run("drag", str(x1), str(y1), str(x2), str(y2))


@mcp.tool()
def type_text(text: str) -> str:
    """在当前焦点处输入文字（中文走剪贴板）。"""
    return _run("type", text)


@mcp.tool()
def press_key(key: str) -> str:
    """按一个键，例如 enter、tab、esc、f5。"""
    return _run("key", key)


@mcp.tool()
def press_hotkey(*keys: str) -> str:
    """按组合键，例如 press_hotkey("ctrl", "s")。"""
    return _run("hotkey", *keys)


@mcp.tool()
def scroll(amount: int) -> str:
    """滚动滚轮，正数向上、负数向下。"""
    return _run("scroll", str(amount))


@mcp.tool()
def focus_window(title: str) -> str:
    """按标题把窗口切到前台；点击前先做这一步。"""
    return _run("focus", title)


@mcp.tool()
def list_windows() -> str:
    """列出当前可见窗口的标题。"""
    return _run("windows")


@mcp.tool()
def launch(target: str) -> str:
    """打开程序、文件、网址或协议，例如 "notepad"、"https://example.com"、"steam://open/main"。"""
    return _run("launch", target)


@mcp.tool()
def wait(seconds: float) -> str:
    """等待若干秒（页面、动画加载）。"""
    return _run("wait", str(seconds))


if __name__ == "__main__":
    mcp.run()
