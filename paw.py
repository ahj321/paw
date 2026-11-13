#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
paw —— 用命令行控制鼠标和键盘，配合视觉模型做 Windows 桌面自动化。

一个最小、无依赖滥用的命令行工具：截图 / 点击 / 输入 / 打开页面。
支持一条命令做一件事，也支持一次跑一串（run 子命令）。

示例：
    python paw.py size                       # 屏幕分辨率
    python paw.py shot screen.png            # 截屏
    python paw.py launch https://bilibili.com  # 打开网页
    python paw.py launch notepad               # 打开程序
    python paw.py focus "Edge"               # 把标题含 Edge 的窗口切到前台
    python paw.py goto https://bilibili.com   # 地址栏打开（输完等 0.6s 再回车）
    python paw.py wait 2                     # 等 2 秒（页面/动画加载）
    python paw.py click 960 540              # 左键单击
    python paw.py type "hello"               # 输入文字
    python paw.py click 960 540 --dry-run    # 只打印行动表，不执行

一次做多件事（每行一条，支持引号与 # 注释）：
    python paw.py run steps.txt
    python paw.py run -            # 从标准输入读

steps.txt 示例：
    launch notepad
    click 300 200
    type "hello"
    hotkey ctrl s

权限等级（--level 或环境变量 PC_LEVEL，默认 safe）：
    browse 末级：只能看、只能打开页面/程序（shot/size/windows/find/launch/move）
    safe   次级：再加点击、滚动、打字、按键；
           但命中「付款 / 注册注销 / 删除改东西」这类危险动作时拒绝
    full   最高级：允许一切

行动表：每个动作前会打印一行「计划：…」，说明接下来要做什么；
用 --no-plan 关掉，用 --dry-run 只看计划不执行（预览模式）。

速度：默认每个动作之间隔 0.3s；用 --pause 0.05 加速，用 wait N 显式等页面加载。
连续动作建议写进 steps.txt 一次跑（run），只在开头和关键节点截图确认。

安全：动作执行前检查鼠标左右键是否同时按住——同时按住即视为急停。
所有动作之间默认间隔 0.3s。
"""

import argparse
import ctypes
import json
import os
import shlex
import subprocess
import sys
import threading
import time
import webbrowser

# 4K + 缩放时，不声明 DPI 感知的话 pyautogui 拿到的坐标会和物理像素差一截，
# 点击就会偏。先声明，再导入 pyautogui。
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE_V2
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

try:
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except Exception:
    pass

# pyautogui 的导入很贵（约 0.4 秒）。size / shot / windows 这些不需要它的命令
# 就靠下面的懒加载跳过导入，只付解释器启动的钱。
_PG_CONF = {"pause": 0.15}


def _import_pyautogui():
    import pyautogui as pg

    pg.FAILSAFE = False
    pg.PAUSE = _PG_CONF["pause"]
    return pg


class _LazyPyAutoGUI:
    """第一次访问属性时才真正导入 pyautogui；PAUSE 赋值只记下来，等导入时生效。"""

    _module = None

    def _load(self):
        if _LazyPyAutoGUI._module is None:
            _LazyPyAutoGUI._module = _import_pyautogui()
        return _LazyPyAutoGUI._module

    def __getattr__(self, name):
        return getattr(self._load(), name)

    def __setattr__(self, name, value):
        if name == "PAUSE":
            _PG_CONF["pause"] = value
            if _LazyPyAutoGUI._module is not None:
                setattr(_LazyPyAutoGUI._module, name, value)
            return
        setattr(self._load(), name, value)


pyautogui = _LazyPyAutoGUI()


def _screen_size():
    """屏幕物理像素尺寸。Windows 下用系统 API，避免为了两个数字导入 pyautogui。"""
    if sys.platform.startswith("win"):
        try:
            u = ctypes.windll.user32
            return (u.GetSystemMetrics(0), u.GetSystemMetrics(1))
        except Exception:
            pass
    return pyautogui.size()


def _dpi_scale() -> float:
    """Windows 显示缩放比例（96dpi = 1.0，150% = 1.5）。截图与坐标都用物理像素。"""
    if not sys.platform.startswith("win"):
        return 1.0
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return round(dpi / 96.0, 2) if dpi else 1.0
    except Exception:
        return 1.0

# 急停：命令行下靠鼠标左右键同按；Web 看板点「急停」时会置上这个标记。
STOP_FLAG = threading.Event()

LEVEL_ORDER = {"browse": 0, "safe": 1, "full": 2}
CMD_LEVEL = {
    "size": "browse", "shot": "browse", "windows": "browse", "find": "browse",
    "scale": "browse",
    "launch": "browse", "move": "browse",
    "desks": "browse", "desk": "browse", "screens": "browse", "watch": "browse",
    "focus": "browse", "wait": "browse", "goto": "browse",
    "uia": "browse", "start": "browse", "stop": "browse",
    "click": "safe", "dclick": "safe", "rclick": "safe", "scroll": "safe", "drag": "safe",
    "type": "safe", "key": "safe", "hotkey": "safe",
    "uia-click": "safe",
}
ALL_CMDS = list(CMD_LEVEL.keys())

# safe 级下，文本里出现这些词就拒绝（付款 / 注册注销 / 删改东西）
DANGER_WORDS = [
    "付款", "支付", "购买", "下单", "结账", "充值", "转账", "扣款", "扫码",
    "注册", "注销", "登录", "登出", "绑定", "实名", "密码", "验证码",
    "删除", "卸载", "格式化", "清空", "移除", "移除文件", "删除文件", "抹掉",
    "覆盖保存", "发送", "发布", "提交订单", "退款",
    "pay", "purchase", "checkout", "transfer", "subscribe",
    "sign up", "signup", "sign out", "signout", "log in", "login", "logout",
    "register", "delete", "remove", "uninstall", "format", "erase", "wipe",
    "rm -rf", "drop table",
]


def _emergency_pressed() -> bool:
    """左右键同时按住 = 急停；Web 看板触发过急停也算。"""
    if STOP_FLAG.is_set():
        return True
    try:
        user32 = ctypes.windll.user32
        left = user32.GetAsyncKeyState(0x01) & 0x8000
        right = user32.GetAsyncKeyState(0x02) & 0x8000
        return bool(left and right)
    except Exception:
        return False


def _level_allows(level: str, cmd: str) -> bool:
    return LEVEL_ORDER[level] >= LEVEL_ORDER[CMD_LEVEL[cmd]]


def _danger_hits(text: str) -> list:
    low = text.lower()
    return [w for w in DANGER_WORDS if w in low]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _window_at(x: int, y: int) -> str:
    """返回屏幕坐标处所在窗口的标题，用来说明「点的是哪个程序的什么」。"""
    if not sys.platform.startswith("win"):
        return ""
    try:
        user32 = ctypes.windll.user32
        user32.WindowFromPoint.argtypes = [_POINT]
        user32.WindowFromPoint.restype = ctypes.c_void_p
        hwnd = user32.WindowFromPoint(_POINT(int(x), int(y)))
        if not hwnd:
            return ""
        root = user32.GetAncestor(hwnd, 2)  # GA_ROOT：取最外层窗口
        if not root:
            root = hwnd
        n = user32.GetWindowTextLengthW(root)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(root, buf, n + 1)
        return buf.value.strip()
    except Exception:
        return ""


def _where(x: int, y: int) -> str:
    """行动表里的位置说明：这个坐标落在哪个窗口上。"""
    win = _window_at(x, y)
    return f"（所在窗口：{win}）" if win else ""


# --- UI Automation：按控件名找位置，不靠眼睛估坐标 -------------------------

def _uia_import():
    try:
        import uiautomation as auto
    except ImportError:
        print("需要 uiautomation：pip install uiautomation", file=sys.stderr)
        raise SystemExit(2)
    return auto


def _uia_window(auto, kw: str):
    win = auto.WindowControl(searchDepth=1, SubName=kw)
    if win.Exists(1):
        return win
    win = auto.WindowControl(searchDepth=1, Name=kw)
    return win


def _uia_rect_text(r) -> str:
    return f"({r.left},{r.top})-({r.right},{r.bottom})"


def _do_uia(kw: str) -> int:
    auto = _uia_import()
    win = _uia_window(auto, kw)
    if not win.Exists(3):
        print(f"没找到标题包含「{kw}」的窗口", file=sys.stderr)
        return 1
    print(f"窗口：{win.Name}  {_uia_rect_text(win.BoundingRectangle)}")
    rows = 0
    for c in win.GetChildren():
        try:
            name = c.Name or ""
            rect = c.BoundingRectangle
            print(f"  [{c.ControlTypeName}] {name!r} {_uia_rect_text(rect)}")
            rows += 1
        except Exception:
            continue
    print(f"共 {rows} 个直接子控件（要更深就加 --deep）")
    if "--deep" in sys.argv:
        for c in win.GetChildren():
            try:
                for sub in c.GetChildren():
                    print(
                        f"    ▸ [{sub.ControlTypeName}] {sub.Name!r} "
                        f"{_uia_rect_text(sub.BoundingRectangle)}"
                    )
            except Exception:
                continue
    return 0


def _do_uia_click(kw: str, name: str) -> int:
    auto = _uia_import()
    win = _uia_window(auto, kw)
    if not win.Exists(3):
        print(f"没找到标题包含「{kw}」的窗口", file=sys.stderr)
        return 1
    ctrl = auto.Control(searchFromControl=win, Name=name, searchDepth=12)
    if not ctrl.Exists(3):
        print(f"窗口「{win.Name}」里没找到名为「{name}」的控件", file=sys.stderr)
        return 1
    rect = ctrl.BoundingRectangle
    print(f"控件：{ctrl.ControlTypeName} {ctrl.Name!r} {_uia_rect_text(rect)}")
    try:
        ctrl.Click()
        return 0
    except Exception as exc:
        print(f"控件 Invoke 失败（{exc}），退回坐标点击中心", file=sys.stderr)
        cx = (rect.left + rect.right) // 2
        cy = (rect.top + rect.bottom) // 2
        _click_at(cx, cy, "left", False)
        return 0


def _plan_text(cmd: str, args: list) -> str:
    if cmd == "size":
        return "读取屏幕分辨率（不改任何东西）"
    if cmd == "uia":
        return f"列出窗口「{args[0] if args else ''}」里的控件和坐标（不改任何东西）"
    if cmd == "uia-click":
        a = args[0] if args else ""
        b = args[1] if len(args) > 1 else ""
        return f"点击窗口「{a}」里名为「{b}」的控件（按控件找位置，不用估坐标）"
    if cmd == "scale":
        return "读取显示缩放比例（不改任何东西）"
    if cmd == "shot":
        return f"截屏并保存到 {args[0] if args else 'screen.png'}（不改任何东西）"
    if cmd == "windows":
        return "列出当前窗口和位置（不改任何东西）"
    if cmd == "desks":
        return "列出虚拟桌面和当前所在桌面（不改任何东西）"
    if cmd == "desk":
        sub = args[0] if args else "list"
        return f"虚拟桌面操作：{sub}（{' '.join(args[1:])}）"
    if cmd == "find":
        return f"在屏幕上查找图片 {args[0] if args else '?'}，返回坐标（不点击）"
    if cmd == "launch":
        return f"打开：{args[0] if args else '?'}"
    if cmd == "focus":
        return f"把标题含「{args[0] if args else '?'}」的窗口切到前台（不点击）"
    if cmd == "goto":
        return f"在浏览器地址栏打开：{args[0] if args else '?'}（输完等 0.6s 再回车）"
    if cmd == "wait":
        return f"等 {args[0] if args else '?'} 秒（什么都不做，等页面/动画加载）"
    if cmd == "move":
        x, y = int(args[0]), int(args[1])
        return f"把鼠标移动到 ({x}, {y}){_where(x, y)}，不点击"
    if cmd == "click":
        x, y = int(args[0]), int(args[1])
        return f"在 ({x}, {y}) 单击鼠标左键{_where(x, y)}"
    if cmd == "dclick":
        x, y = int(args[0]), int(args[1])
        return f"在 ({x}, {y}) 双击鼠标左键{_where(x, y)}"
    if cmd == "rclick":
        x, y = int(args[0]), int(args[1])
        return f"在 ({x}, {y}) 单击鼠标右键{_where(x, y)}"
    if cmd == "scroll":
        return f"滚动滚轮 {args[0]} 格"
    if cmd == "type":
        return f"输入文本：{' '.join(args)[:60]}"
    if cmd == "key":
        return f"按一次键：{args[0]}"
    if cmd == "hotkey":
        return f"按组合键：{' + '.join(args)}"
    return cmd


def _do_launch(target: str) -> None:
    if "://" in target:
        # http(s) 与 steam://、ms-settings: 这类自定义协议，都交给系统处理
        try:
            os.startfile(target)  # noqa: S606 - 用户明确要打开这个地址
            return
        except Exception:
            webbrowser.open(target)
            return
    if os.path.exists(target):
        os.startfile(target)  # noqa: S606 - 用户明确要打开这个文件
    else:
        subprocess.Popen(target, shell=False)  # noqa: S603 - 用户明确要启动的程序


def _clipboard_get() -> str:
    """读剪贴板文本（读不到返回空串）。"""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    CF_UNICODETEXT = 13
    out = ""
    user32.GetClipboardData.restype = ctypes.c_void_p
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    try:
        if not user32.OpenClipboard(None):
            return ""
        try:
            h = user32.GetClipboardData(CF_UNICODETEXT)
            if h:
                p = kernel32.GlobalLock(h)
                if p:
                    try:
                        out = ctypes.wstring_at(p)
                    finally:
                        kernel32.GlobalUnlock(h)
        finally:
            user32.CloseClipboard()
    except Exception:
        return ""
    return out


def _clipboard_set(text: str) -> None:
    """往剪贴板写文本（Win32 API，不依赖 tkinter）。"""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    try:
        if not user32.OpenClipboard(None):
            return
        try:
            user32.EmptyClipboard()
            buf = ctypes.create_unicode_buffer(text)
            size = ctypes.sizeof(buf)
            h = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
            if not h:
                return
            p = kernel32.GlobalLock(h)
            if not p:
                return
            ctypes.memmove(p, buf, size)
            kernel32.GlobalUnlock(h)
            user32.SetClipboardData(CF_UNICODETEXT, h)
        finally:
            user32.CloseClipboard()
    except Exception:
        pass


def _win_input() -> bool:
    return sys.platform.startswith("win")


def _click_at(x: int, y: int, button: str = "left", double: bool = False) -> None:
    """点一下。Windows 下直接走系统 API，不导入 pyautogui（省 0.4 秒）。"""
    if not _win_input():
        if button == "right":
            pyautogui.rightClick(x, y)
        elif double:
            pyautogui.doubleClick(x, y)
        else:
            pyautogui.click(x, y)
        return
    u = ctypes.windll.user32
    u.SetCursorPos(int(x), int(y))
    time.sleep(0.02)
    down, up = (0x0008, 0x0010) if button == "right" else (0x0002, 0x0004)
    for i in range(2 if double else 1):
        u.mouse_event(down, 0, 0, 0, 0)
        u.mouse_event(up, 0, 0, 0, 0)
        if double and i == 0:
            time.sleep(0.05)


def _move_to(x: int, y: int) -> None:
    """只移动光标。Windows 下直接用 SetCursorPos。"""
    if _win_input():
        ctypes.windll.user32.SetCursorPos(int(x), int(y))
    else:
        pyautogui.moveTo(x, y, duration=0.15)


def _scroll_at(n: int) -> None:
    """滚轮 n 格（正数向上）。Windows 下用 mouse_event 的 WHEEL。"""
    if not _win_input():
        pyautogui.scroll(n)
        return
    ctypes.windll.user32.mouse_event(0x0800, 0, 0, int(n) * 120, 0)


def _paste_ctrl_v() -> None:
    """发一次 Ctrl+V。Windows 下用 keybd_event，避免导入 pyautogui。"""
    if not _win_input():
        pyautogui.hotkey("ctrl", "v")
        return
    u = ctypes.windll.user32
    u.keybd_event(0x11, 0, 0, 0)      # Ctrl 按下
    u.keybd_event(0x56, 0, 0, 0)      # V 按下
    u.keybd_event(0x56, 0, 0x0002, 0)  # V 抬起
    u.keybd_event(0x11, 0, 0x0002, 0)  # Ctrl 抬起


def _type_text(text: str) -> None:
    # 统一走剪贴板 + Ctrl+V：比逐字符敲快，也不用为了输入去导入 pyautogui。
    try:
        import pyperclip
    except ImportError:
        pyperclip = None

    if pyperclip:
        try:
            old = pyperclip.paste()
        except Exception:
            old = ""
        pyperclip.copy(text)
    else:
        old = _clipboard_get()
        _clipboard_set(text)

    time.sleep(0.08)
    _paste_ctrl_v()
    time.sleep(0.1)

    try:
        if pyperclip:
            pyperclip.copy(old)
        else:
            _clipboard_set(old)
    except Exception:
        pass


def _list_windows() -> None:
    import pygetwindow as gw

    for w in gw.getAllWindows():
        title = (w.title or "").strip()
        if title:
            print(f"{w.left:>6} {w.top:>6} {w.width:>6} {w.height:>6}  {title}")


def _goto_cmd(args: list) -> int:
    """在浏览器地址栏打开 URL：Ctrl+L → 输入 → 等下拉稳定 → 回车。

    直接「Ctrl+L、type、enter」三连最容易翻车：地址栏的自动补全下拉还没收起来，
    回车会被它吃掉，页面不动。这里在输入后固定等 0.6s 再回车。
    """
    if not args:
        print("用法：paw.py goto <url>", file=sys.stderr)
        return 2
    url = args[0]
    pyautogui.hotkey("ctrl", "l")
    time.sleep(0.25)
    pyautogui.typewrite(url, interval=0.01)
    time.sleep(0.6)
    pyautogui.press("enter")
    time.sleep(0.3)
    print("goto", url)
    return 0


def _focus_cmd(args: list) -> int:
    """按标题关键词把窗口切到前台。点击之前先 focus，第一下才不会只落在「激活」上。"""
    try:
        import pygetwindow as gw
    except ImportError:
        print("需要 pygetwindow：pip install pygetwindow", file=sys.stderr)
        return 1

    if not args:
        print("用法：paw.py focus <标题关键词>", file=sys.stderr)
        return 2

    key = " ".join(args).lower()
    hits = [w for w in gw.getAllWindows() if key in (w.title or "").lower() and (w.title or "").strip()]
    if not hits:
        print(f"没找到标题含「{key}」的窗口", file=sys.stderr)
        return 1

    w = hits[0]
    try:
        if w.isMinimized:
            w.restore()
        w.activate()
        time.sleep(0.4)
        print("focused:", w.title)
        return 0
    except Exception as e:
        print(f"激活失败：{e}", file=sys.stderr)
        return 1


def _idle_seconds() -> float:
    """距上次键鼠输入过了多少秒。"""
    try:
        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        lii = LASTINPUTINFO()
        lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return -1.0
        millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
        return round(millis / 1000.0, 1)
    except Exception:
        return -1.0


def _watch_cmd(args: list) -> int:
    """实时说明「现在在干什么」：前台窗口、标题变化、鼠标位置、空闲时间。"""
    try:
        import pygetwindow as gw
    except ImportError:
        print("需要 pygetwindow：pip install pygetwindow", file=sys.stderr)
        return 1

    interval = float(args[0]) if args else 2.0
    prev = {}
    print(f"每 {interval}s 刷新一次，Ctrl+C 退出", file=sys.stderr)
    try:
        while True:
            now = time.strftime("%H:%M:%S")
            fg = gw.getActiveWindow()
            fg_title = (fg.title or "").strip() if fg else "(无)"
            fg_size = f"{fg.width}x{fg.height}" if fg else "-"
            pos = pyautogui.position()
            idle = _idle_seconds()
            idle_txt = f"{idle}s" if idle >= 0 else "?"
            print(
                f"[{now}] 前台：{fg_title}  [{fg_size}]  ｜ 鼠标 ({pos.x},{pos.y}) ｜ 空闲 {idle_txt}",
                flush=True,
            )

            cur = {}
            for w in gw.getAllWindows():
                t = (w.title or "").strip()
                if t and w.width > 0:
                    cur[w._hWnd] = t
            for hwnd, t in cur.items():
                if hwnd in prev and prev[hwnd] != t:
                    print(f"           ↳ 窗口变化：{prev[hwnd]!r} → {t!r}", flush=True)
            for hwnd, t in cur.items():
                if hwnd not in prev:
                    print(f"           ↳ 新窗口：{t!r}", flush=True)
            prev = cur

            time.sleep(interval)
    except KeyboardInterrupt:
        print("已停止", file=sys.stderr)
        return 0


def _screens_cmd(args: list) -> int:
    """用文字说清楚每个虚拟桌面上正开着什么（谁在前台）。"""
    try:
        import pygetwindow as gw
    except ImportError:
        print("需要 pygetwindow：pip install pygetwindow", file=sys.stderr)
        return 1
    try:
        from pyvda import VirtualDesktop
    except ImportError:
        print("需要 pyvda：pip install pyvda", file=sys.stderr)
        return 1

    try:
        cur = VirtualDesktop.current().number
    except Exception:
        cur = -1

    fg = gw.getActiveWindow()
    groups = {}
    for w in gw.getAllWindows():
        title = (w.title or "").strip()
        if not title or w.width <= 0 or w.height <= 0:
            continue
        try:
            desk = VirtualDesktop.get_desktop_of_window(w._hWnd).number
        except Exception:
            desk = cur  # 查不到归属时，按当前桌面算
        groups.setdefault(desk, []).append(w)

    if not groups:
        print("没有可见窗口")
        return 0

    for desk in sorted(groups):
        if desk == cur:
            head = f"桌面 {desk} *（当前）"
        elif desk == -1:
            head = "未归属桌面"
        else:
            head = f"桌面 {desk}"
        print(head)
        for w in groups[desk]:
            mark = "  ← 前台" if fg is not None and w._hWnd == fg._hWnd else ""
            print(f"  - {w.title}  [{w.width}x{w.height}]{mark}")
    return 0


def _desk_cmd(args: list) -> int:
    """虚拟桌面管理：list / new / switch <n> / close。"""
    try:
        from pyvda import VirtualDesktop, get_virtual_desktops
    except ImportError:
        print("需要 pyvda：pip install pyvda", file=sys.stderr)
        return 1

    sub = args[0] if args else "list"
    try:
        if sub == "list":
            cur = VirtualDesktop.current()
            for d in get_virtual_desktops():
                mark = " *" if d.number == cur.number else ""
                print(f"{d.number}{mark}")
            return 0
        if sub == "new":
            d = VirtualDesktop.create()
            print("created", d.number)
            return 0
        if sub == "switch":
            n = int(args[1])
            VirtualDesktop(n).go()
            print("switched", n)
            return 0
        if sub == "close":
            d = VirtualDesktop.current()
            n = d.number
            d.remove()
            print("closed", n)
            return 0
        print(f"未知子命令：{sub}（list / new / switch <n> / close）", file=sys.stderr)
        return 2
    except Exception as e:  # noqa: BLE001 - 桌面 API 出错时给出人能看懂的提示
        print(f"虚拟桌面操作失败：{e}", file=sys.stderr)
        return 1


PAWD_PID = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pawd.json")


def _pawd_info():
    try:
        with open(PAWD_PID, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _pid_alive(pid: int) -> bool:
    """判断进程还在不在（用 OpenProcess，比 tasklist 快 100ms）。"""
    if not sys.platform.startswith("win"):
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    except Exception:
        return False


def _pawd_alive() -> bool:
    info = _pawd_info()
    if not info:
        return False
    return _pid_alive(info.get("pid", 0))


def _do_start(port: int) -> int:
    if _pawd_alive():
        print(f"已经在跑：127.0.0.1:{(_pawd_info() or {}).get('port')}")
        return 0
    import subprocess as sp
    import urllib.request

    here = os.path.dirname(os.path.abspath(__file__))
    flags = 0
    if sys.platform.startswith("win"):
        flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    p = sp.Popen(
        [sys.executable, os.path.join(here, "serve.py"), str(port)],
        creationflags=flags,
        stdout=sp.DEVNULL,
        stderr=sp.DEVNULL,
        close_fds=True,
    )
    with open(PAWD_PID, "w", encoding="utf-8") as f:
        json.dump({"pid": p.pid, "port": port}, f)
    ready = False
    for _ in range(40):
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/action",
                data=b'{"line":"size"}',
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=1).read()
            ready = True
            break
        except Exception:
            time.sleep(0.2)
    if ready:
        print(f"已启动：127.0.0.1:{port}（pid {p.pid}），之后每条命令 ~9ms")
        return 0
    print("启动失败：服务没响应", file=sys.stderr)
    return 1


def _do_stop() -> int:
    info = _pawd_info()
    if not info:
        print("没有在跑的服务")
        return 0
    import subprocess as sp

    sp.run(["taskkill", "/PID", str(info["pid"]), "/F"], capture_output=True)
    try:
        os.remove(PAWD_PID)
    except Exception:
        pass
    print(f"已关闭（pid {info['pid']}）")
    return 0


def _remote_exec(port: str, cmd: str, args: list, as_json: bool) -> int:
    """把一条命令转发给常驻服务（serve.py），省掉每次解释器冷启动。"""
    import json as _json
    import urllib.request

    line = " ".join([cmd] + [str(a) for a in args])
    body = _json.dumps({"line": line}).encode("utf-8")
    url = f"http://127.0.0.1:{port}/api/action"
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except Exception as exc:
        print(f"常驻服务没在 127.0.0.1:{port} 上（{exc}）——先跑 python serve.py {port}", file=sys.stderr)
        return 1

    try:
        data = _json.loads(raw)
    except Exception:
        print(raw)
        return 0

    if as_json:
        print(_json.dumps(data, ensure_ascii=False))
        return 0 if data.get("ok") else 1
    out = (data.get("out") or "").strip()
    err = (data.get("err") or "").strip()
    if out:
        print(out)
    if err:
        print(err, file=sys.stderr)
    return 0 if data.get("ok") else 1


def run_one(
    cmd: str, args: list, level: str, show_plan: bool, dry_run: bool, note: str = ""
) -> int:
    """执行一条动作。返回 0 成功；非 0 表示失败/被拦截。"""
    if STOP_FLAG.is_set():
        print("急停中：拒绝执行（在 Web 看板点「解除急停」或重启进程）", file=sys.stderr)
        return 5

    if cmd not in CMD_LEVEL:
        print(f"未知动作：{cmd}", file=sys.stderr)
        return 2

    if not _level_allows(level, cmd):
        print(
            f"权限不足：当前等级 {level}，{cmd} 需要 {CMD_LEVEL[cmd]} 级权限",
            file=sys.stderr,
        )
        return 3

    payload = " ".join(args)
    if level == "safe":
        hits = _danger_hits(payload)
        if hits:
            print(
                f"安全拦截：这一步看起来涉及「{hits[0]}」，safe 级不允许——"
                "要人工确认，或改用 --level full",
                file=sys.stderr,
            )
            return 4

    if show_plan:
        plan = _plan_text(cmd, args)
        if note:
            plan = f"{plan}  · {note}"
        print(f"计划：{plan}", file=sys.stderr)

    if dry_run:
        return 0

    if cmd == "watch":
        return _watch_cmd(args)
    if cmd == "screens":
        return _screens_cmd(args)
    if cmd in ("desks", "desk"):
        return _desk_cmd(args if cmd == "desk" else ["list"])

    if cmd not in ("size", "shot", "windows") and _emergency_pressed():
        print("急停：检测到鼠标左右键同时按下，已中止", file=sys.stderr)
        return 1

    try:
        if cmd == "size":
            print(_screen_size())

        elif cmd == "scale":
            w, h = _screen_size()
            s = _dpi_scale()
            print(f"缩放 {s:.2f}x（{int(s * 100)}%）：物理 {w}x{h}，逻辑 {int(w / s)}x{int(h / s)}")
            print("shot 与 click 都用物理像素；按缩略图/预览图估坐标时，先乘这个比例。")

        elif cmd == "uia":
            return _do_uia(args[0] if args else "")

        elif cmd == "uia-click":
            if len(args) < 2:
                print("用法：paw.py uia-click <窗口标题关键词> <控件名>", file=sys.stderr)
                return 2
            return _do_uia_click(args[0], " ".join(args[1:]))

        elif cmd == "shot":
            rest = args
            crop = None
            jpg = False
            out = "screen.png"
            i = 0
            while i < len(rest):
                if rest[i] == "--crop" and i + 1 < len(rest):
                    c = tuple(int(v) for v in rest[i + 1].split(","))
                    if len(c) == 4:
                        crop = (c[0], c[1], c[0] + c[2], c[1] + c[3])
                    i += 2
                elif rest[i] == "--jpg":
                    jpg = True
                    i += 1
                else:
                    out = rest[i]
                    i += 1
            try:
                from PIL import ImageGrab

                img = ImageGrab.grab()  # 比 pyautogui.screenshot() 少导入一层
            except Exception:
                img = pyautogui.screenshot()
            if crop:
                img = img.crop(crop)
            if jpg:
                img = img.convert("RGB")
                img.save(out, quality=88)
            else:
                img.save(out)
            print(out, img.size)

        elif cmd == "drag":
            x1, y1, x2, y2 = (int(v) for v in args[:4])
            dur = float(args[4]) if len(args) > 4 else 0.4
            pyautogui.moveTo(x1, y1)
            time.sleep(0.05)
            pyautogui.dragTo(x2, y2, duration=dur, button="left")
            print("dragged", (x1, y1), "->", (x2, y2))

        elif cmd == "launch":
            _do_launch(args[0])
            print("launched", args[0])

        elif cmd == "focus":
            return _focus_cmd(args)

        elif cmd == "goto":
            return _goto_cmd(args)

        elif cmd == "wait":
            secs = float(args[0]) if args else 1.0
            time.sleep(max(0.0, secs))
            print("waited", secs, "s")

        elif cmd == "click":
            _click_at(int(args[0]), int(args[1]))
            print("clicked", args[0], args[1])

        elif cmd == "dclick":
            _click_at(int(args[0]), int(args[1]), double=True)
            print("double-clicked", args[0], args[1])

        elif cmd == "rclick":
            _click_at(int(args[0]), int(args[1]), button="right")
            print("right-clicked", args[0], args[1])

        elif cmd == "move":
            _move_to(int(args[0]), int(args[1]))
            print("moved", args[0], args[1])

        elif cmd == "type":
            _type_text(" ".join(args))
            print("typed")

        elif cmd == "key":
            pyautogui.press(args[0])
            print("key", args[0])

        elif cmd == "hotkey":
            pyautogui.hotkey(*args)
            print("hotkey", " ".join(args))

        elif cmd == "scroll":
            _scroll_at(int(args[0]))
            print("scrolled", args[0])

        elif cmd == "find":
            conf = float(args[1]) if len(args) > 1 else 0.85
            loc = pyautogui.locateOnScreen(args[0], confidence=conf)
            print(loc if loc else "not found")

        elif cmd == "windows":
            _list_windows()

    except (IndexError, ValueError) as e:
        print(f"参数错误：{e}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - failsafe 要单独认出来
        if type(exc).__name__ == "FailSafeException":
            print("failsafe: 已中止", file=sys.stderr)
            return 1
        raise
    return 0


def _split_step(line: str):
    """把一行 steps 拆成 (命令, 参数, 备注)。行内 # 之后是备注，会显示在行动表里。"""
    code, _, note = line.partition("#")
    parts = shlex.split(code.strip())
    if not parts:
        return "", [], note.strip()
    return parts[0], parts[1:], note.strip()


def _read_steps(path: str) -> list:
    if path == "-":
        text = sys.stdin.read()
    else:
        with open(path, "r", encoding="utf-8-sig") as f:
            text = f.read()
    steps = []
    for line in text.splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        cmd, args, note = _split_step(line)
        if cmd:
            steps.append((line, cmd, args, note))
    return steps


def main() -> int:
    ap = argparse.ArgumentParser(description="control the mouse and keyboard from the CLI")
    ap.add_argument("cmd", choices=ALL_CMDS + ["run"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--delay", type=float, default=0.0, help="动作前等待秒数")
    ap.add_argument(
        "--pause",
        type=float,
        default=0.15,
        help="每个动作之间的间隔秒数（默认 0.15；想更快设 0.05，页面慢就设 1）",
    )
    ap.add_argument(
        "--level",
        default=os.environ.get("PC_LEVEL", "safe"),
        choices=list(LEVEL_ORDER.keys()),
        help="权限等级：browse / safe / full（默认 safe，可用 PC_LEVEL 环境变量）",
    )
    ap.add_argument("--no-plan", action="store_true", help="不打印行动表")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果（方便 agent 解析）")
    ap.add_argument("--dry-run", action="store_true", help="只打印行动表，不执行")
    ap.add_argument(
        "--remote",
        nargs="?",
        const=os.environ.get("PAW_REMOTE", "8000"),
        default=None,
        help="转发给常驻服务执行（快 20 倍）：--remote 8899，或设 PAW_REMOTE",
    )
    ap.add_argument(
        "--parallel",
        action="store_true",
        help="run 时并行执行「打开/查看」类步骤（鼠标键盘动作仍串行，否则会打架）",
    )
    a, extra = ap.parse_known_args()
    if extra:
        a.args = list(a.args) + list(extra)

    if a.remote:
        return _remote_exec(a.remote, a.cmd, a.args, a.json)

    if a.cmd == "start":
        return _do_start(int(a.args[0]) if a.args else 8000)
    if a.cmd == "stop":
        return _do_stop()

    if a.delay:
        time.sleep(a.delay)
    if a.pause >= 0:
        pyautogui.PAUSE = a.pause

    try:
        if a.cmd == "run":
            if not a.args:
                print("用法：paw.py run <steps.txt|->", file=sys.stderr)
                return 2
            steps = _read_steps(a.args[0])
            if not steps:
                print("没有可执行的步骤", file=sys.stderr)
                return 2

            if a.parallel:
                mouse_cmds = {"click", "dclick", "rclick", "scroll", "type", "key", "hotkey", "move"}
                bad = [s[1] for s in steps if s[1] in mouse_cmds]
                if bad:
                    print(
                        "并行模式只并行「打开 / 查看」类步骤；鼠标键盘动作必须串行"
                        f"（这些会打架：{', '.join(bad[:3])}）",
                        file=sys.stderr,
                    )
                    return 2

                from concurrent.futures import ThreadPoolExecutor

                def _one(item):
                    idx, (line, cmd, args, note) = item
                    print(f"[{idx}/{len(steps)}] {line}", file=sys.stderr)
                    return run_one(cmd, args, a.level, not a.no_plan, a.dry_run, note)

                with ThreadPoolExecutor(max_workers=min(8, len(steps))) as ex:
                    results = list(ex.map(_one, enumerate(steps, 1)))
                bad_idx = next((i for i, rc in enumerate(results, 1) if rc != 0), None)
                if bad_idx is not None:
                    print(f"第 {bad_idx} 步失败（返回码 {results[bad_idx - 1]}）", file=sys.stderr)
                    return results[bad_idx - 1]
                print(f"完成，共 {len(steps)} 步（并行）", file=sys.stderr)
                return 0

            for idx, (line, cmd, args, note) in enumerate(steps, 1):
                print(f"[{idx}/{len(steps)}] {line}", file=sys.stderr)
                rc = run_one(cmd, args, a.level, not a.no_plan, a.dry_run, note)
                if rc != 0:
                    print(f"第 {idx} 步失败（返回码 {rc}），已停止", file=sys.stderr)
                    return rc
            print(f"完成，共 {len(steps)} 步", file=sys.stderr)
            return 0

        if a.json:
            import contextlib
            import io
            import json as _json

            try:
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = run_one(a.cmd, a.args, a.level, not a.no_plan, a.dry_run)
            rec = {
                "ok": rc == 0,
                "code": rc,
                "cmd": a.cmd,
                "args": a.args,
                "stdout": out.getvalue().strip(),
                "stderr": err.getvalue().strip(),
            }
            if a.cmd in ("click", "dclick", "rclick", "move") and len(a.args) >= 2:
                try:
                    rec["x"], rec["y"] = int(a.args[0]), int(a.args[1])
                    rec["window"] = _window_at(rec["x"], rec["y"])
                except (ValueError, OSError):
                    pass
            print(_json.dumps(rec, ensure_ascii=False))
            return rc

        return run_one(a.cmd, a.args, a.level, not a.no_plan, a.dry_run)

    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())




