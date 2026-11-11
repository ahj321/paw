#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai-pc-control —— 用命令行控制鼠标和键盘，配合视觉模型做 Windows 桌面自动化。

一个最小、无依赖滥用的命令行工具：截图 / 点击 / 输入 / 打开页面。
支持一条命令做一件事，也支持一次跑一串（run 子命令）。

示例：
    python pc.py size                       # 屏幕分辨率
    python pc.py shot screen.png            # 截屏
    python pc.py launch https://bilibili.com  # 打开网页
    python pc.py launch notepad               # 打开程序
    python pc.py focus "Edge"               # 把标题含 Edge 的窗口切到前台
    python pc.py wait 2                     # 等 2 秒（页面/动画加载）
    python pc.py click 960 540              # 左键单击
    python pc.py type "hello"               # 输入文字
    python pc.py click 960 540 --dry-run    # 只打印行动表，不执行

一次做多件事（每行一条，支持引号与 # 注释）：
    python pc.py run steps.txt
    python pc.py run -            # 从标准输入读

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

import pyautogui

try:
    sys.stdout.reconfigure(errors='replace')
    sys.stderr.reconfigure(errors='replace')
except Exception:
    pass

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.3

# 急停：命令行下靠鼠标左右键同按；Web 看板点「急停」时会置上这个标记。
STOP_FLAG = threading.Event()

LEVEL_ORDER = {"browse": 0, "safe": 1, "full": 2}
CMD_LEVEL = {
    "size": "browse", "shot": "browse", "windows": "browse", "find": "browse",
    "launch": "browse", "move": "browse",
    "desks": "browse", "desk": "browse", "screens": "browse", "watch": "browse",
    "focus": "browse", "wait": "browse",
    "click": "safe", "dclick": "safe", "rclick": "safe", "scroll": "safe",
    "type": "safe", "key": "safe", "hotkey": "safe",
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


def _plan_text(cmd: str, args: list) -> str:
    if cmd == "size":
        return "读取屏幕分辨率（不改任何东西）"
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
    if cmd == "wait":
        return f"等 {args[0] if args else '?'} 秒（什么都不做，等页面/动画加载）"
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


def _do_launch(target: str) -> None:
    if target.startswith(("http://", "https://")):
        webbrowser.open(target)
    else:
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


def _type_text(text: str) -> None:
    if all(ord(c) < 128 for c in text):
        pyautogui.typewrite(text, interval=0.02)
        return
    # 中文走剪贴板：写进去、等一会儿、粘贴、再把原来的剪贴板还回去，
    # 免得内容串到别的程序里（之前保存对话框文件名被搅乱就是这么来的）。
    # 优先用 pyperclip（稳定），没装就退回 Win32 兜底。
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

    time.sleep(0.15)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.2)

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


def _focus_cmd(args: list) -> int:
    """按标题关键词把窗口切到前台。点击之前先 focus，第一下才不会只落在「激活」上。"""
    try:
        import pygetwindow as gw
    except ImportError:
        print("需要 pygetwindow：pip install pygetwindow", file=sys.stderr)
        return 1

    if not args:
        print("用法：pc.py focus <标题关键词>", file=sys.stderr)
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


def run_one(cmd: str, args: list, level: str, show_plan: bool, dry_run: bool) -> int:
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
        print(f"计划：{_plan_text(cmd, args)}", file=sys.stderr)

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
            print(pyautogui.size())

        elif cmd == "shot":
            out = args[0] if args else "screen.png"
            img = pyautogui.screenshot()
            img.save(out)
            print(out, img.size)

        elif cmd == "launch":
            _do_launch(args[0])
            print("launched", args[0])

        elif cmd == "focus":
            return _focus_cmd(args)

        elif cmd == "wait":
            secs = float(args[0]) if args else 1.0
            time.sleep(max(0.0, secs))
            print("waited", secs, "s")

        elif cmd == "click":
            pyautogui.click(int(args[0]), int(args[1]))
            print("clicked", args[0], args[1])

        elif cmd == "dclick":
            pyautogui.doubleClick(int(args[0]), int(args[1]))
            print("double-clicked", args[0], args[1])

        elif cmd == "rclick":
            pyautogui.rightClick(int(args[0]), int(args[1]))
            print("right-clicked", args[0], args[1])

        elif cmd == "move":
            pyautogui.moveTo(int(args[0]), int(args[1]), duration=0.3)
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
            pyautogui.scroll(int(args[0]))
            print("scrolled", args[0])

        elif cmd == "find":
            conf = float(args[1]) if len(args) > 1 else 0.85
            loc = pyautogui.locateOnScreen(args[0], confidence=conf)
            print(loc if loc else "not found")

        elif cmd == "windows":
            _list_windows()

    except pyautogui.FailSafeException:
        print("failsafe: 已中止", file=sys.stderr)
        return 1
    except (IndexError, ValueError) as e:
        print(f"参数错误：{e}", file=sys.stderr)
        return 2
    return 0


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
        steps.append(line)
    return steps


def main() -> int:
    ap = argparse.ArgumentParser(description="control the mouse and keyboard from the CLI")
    ap.add_argument("cmd", choices=ALL_CMDS + ["run"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--delay", type=float, default=0.0, help="动作前等待秒数")
    ap.add_argument(
        "--pause",
        type=float,
        default=0.3,
        help="每个动作之间的间隔秒数（默认 0.3；想快就设 0.05，页面慢就设 1）",
    )
    ap.add_argument(
        "--level",
        default=os.environ.get("PC_LEVEL", "safe"),
        choices=list(LEVEL_ORDER.keys()),
        help="权限等级：browse / safe / full（默认 safe，可用 PC_LEVEL 环境变量）",
    )
    ap.add_argument("--no-plan", action="store_true", help="不打印行动表")
    ap.add_argument("--dry-run", action="store_true", help="只打印行动表，不执行")
    ap.add_argument(
        "--parallel",
        action="store_true",
        help="run 时并行执行「打开/查看」类步骤（鼠标键盘动作仍串行，否则会打架）",
    )
    a = ap.parse_args()

    if a.delay:
        time.sleep(a.delay)
    if a.pause >= 0:
        pyautogui.PAUSE = a.pause

    try:
        if a.cmd == "run":
            if not a.args:
                print("用法：pc.py run <steps.txt|->", file=sys.stderr)
                return 2
            steps = _read_steps(a.args[0])
            if not steps:
                print("没有可执行的步骤", file=sys.stderr)
                return 2

            if a.parallel:
                mouse_cmds = {"click", "dclick", "rclick", "scroll", "type", "key", "hotkey", "move"}
                bad = [s for s in steps if shlex.split(s)[0] in mouse_cmds]
                if bad:
                    print(
                        "并行模式只并行「打开 / 查看」类步骤；鼠标键盘动作必须串行"
                        f"（这些会打架：{', '.join(bad[:3])}）",
                        file=sys.stderr,
                    )
                    return 2

                from concurrent.futures import ThreadPoolExecutor

                def _one(item):
                    idx, line = item
                    parts = shlex.split(line)
                    print(f"[{idx}/{len(steps)}] {line}", file=sys.stderr)
                    return run_one(parts[0], parts[1:], a.level, not a.no_plan, a.dry_run)

                with ThreadPoolExecutor(max_workers=min(8, len(steps))) as ex:
                    results = list(ex.map(_one, enumerate(steps, 1)))
                bad_idx = next((i for i, rc in enumerate(results, 1) if rc != 0), None)
                if bad_idx is not None:
                    print(f"第 {bad_idx} 步失败（返回码 {results[bad_idx - 1]}）", file=sys.stderr)
                    return results[bad_idx - 1]
                print(f"完成，共 {len(steps)} 步（并行）", file=sys.stderr)
                return 0

            for idx, line in enumerate(steps, 1):
                parts = shlex.split(line)
                cmd, rest = parts[0], parts[1:]
                print(f"[{idx}/{len(steps)}] {line}", file=sys.stderr)
                rc = run_one(cmd, rest, a.level, not a.no_plan, a.dry_run)
                if rc != 0:
                    print(f"第 {idx} 步失败（返回码 {rc}），已停止", file=sys.stderr)
                    return rc
            print(f"完成，共 {len(steps)} 步", file=sys.stderr)
            return 0

        return run_one(a.cmd, a.args, a.level, not a.no_plan, a.dry_run)

    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())




