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
import base64
import ctypes
import ctypes.wintypes
import json
import os
import shlex
import socket
import struct
import subprocess
import sys
import tempfile
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

# 动作之间的默认停顿，--pause 可改（更小更快，更大多留反应时间）。
_PG_CONF = {"pause": 0.15}


SHOT_KEEP = 50  # 临时截图保留张数


def _shot_dir() -> str:
    """paw 自己产生的截图放这里，和用户指定的路径分开，方便自动清理。"""
    d = os.path.join(tempfile.gettempdir(), "paw")
    os.makedirs(d, exist_ok=True)
    return d


def _default_shot_path() -> str:
    return os.path.join(_shot_dir(), time.strftime("shot-%Y%m%d-%H%M%S.png"))


def _clean_shots(keep: int = SHOT_KEEP) -> int:
    """删 %TEMP%\\paw 里最旧的截图，只留最近 keep 张，返回删除个数。

    只碰 paw 自己写进去的文件；用户 `shot 指定路径` 保存的图一张都不动。
    """
    d = _shot_dir()
    try:
        names = os.listdir(d)
    except OSError:
        return 0
    files = [os.path.join(d, n) for n in names
             if n.lower().endswith((".png", ".jpg", ".jpeg"))]
    if len(files) <= keep:
        return 0
    files.sort(key=os.path.getmtime)
    removed = 0
    for f in files[: len(files) - keep]:
        try:
            os.remove(f)
            removed += 1
        except OSError:
            pass
    return removed


def _screen_size():
    """屏幕物理像素尺寸（Windows 系统 API，不导入第三方库）。"""
    u = ctypes.windll.user32
    return (u.GetSystemMetrics(0), u.GetSystemMetrics(1))


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

def _grab_screen():
    """抓整屏，返回 Pillow 图像。走 GDI，Pillow 只负责裁剪和编码。"""
    from PIL import Image

    u = ctypes.windll.user32
    g = ctypes.windll.gdi32

    u.GetDC.restype = ctypes.c_void_p
    u.GetDC.argtypes = [ctypes.c_void_p]
    u.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    g.CreateCompatibleDC.restype = ctypes.c_void_p
    g.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    g.CreateCompatibleBitmap.restype = ctypes.c_void_p
    g.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    g.SelectObject.restype = ctypes.c_void_p
    g.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    g.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                         ctypes.c_uint]
    g.DeleteObject.argtypes = [ctypes.c_void_p]
    g.DeleteDC.argtypes = [ctypes.c_void_p]

    w, h = _screen_size()
    hdc = u.GetDC(0)
    if not hdc:
        raise OSError("取不到屏幕 DC")
    dc = bmp = None
    try:
        dc = g.CreateCompatibleDC(hdc)
        bmp = g.CreateCompatibleBitmap(hdc, w, h)
        if not dc or not bmp:
            raise OSError("创建兼容位图失败")
        g.SelectObject(dc, bmp)
        if not g.BitBlt(dc, 0, 0, w, h, hdc, 0, 0, 0x00CC0020):
            raise OSError("抓屏失败（BitBlt 被拒）")

        class _BMIH(ctypes.Structure):
            _fields_ = [("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                        ("biSizeImage", ctypes.c_uint32),
                        ("biXPelsPerMeter", ctypes.c_int32),
                        ("biYPelsPerMeter", ctypes.c_int32),
                        ("biClrUsed", ctypes.c_uint32),
                        ("biClrImportant", ctypes.c_uint32)]

        class _BMI(ctypes.Structure):
            _fields_ = [("bmiHeader", _BMIH), ("bmiColors", ctypes.c_uint32 * 3)]

        bmi = _BMI()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BMIH)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h  # 负高度 = 从上往下存
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0  # BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        g.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
                                ctypes.c_uint, ctypes.c_void_p,
                                ctypes.POINTER(_BMI), ctypes.c_uint]
        if not g.GetDIBits(dc, bmp, 0, h, buf, ctypes.byref(bmi), 0):
            raise OSError("读取像素失败（GetDIBits）")
        return Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)
    finally:
        if bmp:
            g.DeleteObject(bmp)
        if dc:
            g.DeleteDC(dc)
        u.ReleaseDC(0, hdc)


# 急停：命令行下靠鼠标左右键同按；Web 看板点「急停」时会置上这个标记。
STOP_FLAG = threading.Event()
LEVEL_ORDER = {"browse": 0, "safe": 1, "full": 2}
CMD_LEVEL = {
    "size": "browse", "shot": "browse", "windows": "browse", "find": "browse",
    "scale": "browse",
    "launch": "browse", "move": "browse",
    "desks": "browse", "desk": "browse", "screens": "browse", "watch": "browse",
    "focus": "browse", "wait": "browse", "goto": "browse",
    "uia": "browse", "start": "browse", "stop": "browse", "clean": "browse", "web": "browse",
    "web-click": "safe",
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


# ---------- 网页（CDP）：让浏览器报出 DOM 里每个控件的位置 ----------

CDP_PORT = 9222


def _cdp_pages(port: int = CDP_PORT):
    """列出调试端口上可操作的页面。"""
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=3) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as exc:
        raise RuntimeError(
            f"连不上 {port} 端口的调试接口（{exc.__class__.__name__}）。\n"
            "浏览器要用调试端口启动，而且必须配一个独立的 --user-data-dir\n"
            "（默认配置目录从 Chromium 某版起不再允许开调试端口）：\n\n"
            '  msedge.exe --remote-debugging-port=9222 --user-data-dir="%TEMP%\\paw-edge"\n\n'
            "打开后在里面登录一次要操作的网站，之后 paw web 就能用了。"
        )
    return [p for p in data if p.get("type") == "page"]


def _ws_connect(host: str, port: int, path: str, timeout: float = 10.0):
    s = socket.create_connection((host, port), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    s.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            raise RuntimeError("握手时连接被关闭")
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    if b"101" not in head.split(b"\r\n")[0]:
        raise RuntimeError("握手失败：" + head.decode("latin1")[:200])
    return s, rest


def _ws_send(s, text: str) -> None:
    data = text.encode()
    header = bytearray([0x81])  # FIN + 文本帧
    n = len(data)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", n)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", n)
    mask = os.urandom(4)
    header += mask
    s.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))


def _recv_exact(s, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise RuntimeError("连接关闭")
        buf += chunk
    return buf


def _ws_recv(s, pending: bytes = b"") -> str:
    buf = pending
    while True:
        while len(buf) < 2:
            buf += _recv_exact(s, 2 - len(buf))
        b0, b1 = buf[0], buf[1]
        fin = b0 & 0x80
        opcode = b0 & 0x0F
        masked = b1 & 0x80
        n = b1 & 0x7F
        pos = 2
        if n == 126:
            while len(buf) < pos + 2:
                buf += _recv_exact(s, pos + 2 - len(buf))
            n = struct.unpack(">H", buf[pos:pos + 2])[0]
            pos += 2
        elif n == 127:
            while len(buf) < pos + 8:
                buf += _recv_exact(s, pos + 8 - len(buf))
            n = struct.unpack(">Q", buf[pos:pos + 8])[0]
            pos += 8
        mkey = b""
        if masked:
            while len(buf) < pos + 4:
                buf += _recv_exact(s, pos + 4 - len(buf))
            mkey = buf[pos:pos + 4]
            pos += 4
        while len(buf) < pos + n:
            buf += _recv_exact(s, pos + n - len(buf))
        payload = buf[pos:pos + n]
        buf = buf[pos + n:]
        if mkey:
            payload = bytes(b ^ mkey[i % 4] for i, b in enumerate(payload))
        if opcode == 0x8:
            raise RuntimeError("服务器关闭了连接")
        if opcode == 0x9:
            continue
        if opcode in (0x1, 0x2):
            if not fin:
                continue
            return payload.decode("utf-8", "replace")


def _cdp_call(s, counter, method: str, params=None):
    counter[0] += 1
    mid = counter[0]
    _ws_send(s, json.dumps({"id": mid, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(_ws_recv(s))
        if msg.get("id") == mid:
            return msg


def _pick_page(pages):
    """优先挑真正的内容页：跳过 edge:// 内部页与浏览器欢迎/致谢页。"""
    for p in pages:
        url = (p.get("url") or "").lower()
        if url.startswith(("edge://", "chrome-extension://", "devtools://")):
            continue
        if "welcome." in url or "thankyou" in url:
            continue
        return p
    return pages[0] if pages else None


def _web_elements(port: int = CDP_PORT, keyword: str = ""):
    """连上内容页，拿回可点元素 [(文字, 中心x, 中心y)]。"""
    pages = _cdp_pages(port)
    if not pages:
        raise RuntimeError("没有打开的页面")
    page = _pick_page(pages)
    path = page["webSocketDebuggerUrl"].split(str(port), 1)[1]
    s, pending = _ws_connect("127.0.0.1", port, path)
    counter = [0]
    try:
        js = (
            "JSON.stringify(([...document.querySelectorAll("
            "'a,button,[role=button],input[type=submit]')]"
            ".filter(e=>{const r=e.getBoundingClientRect();"
            "return r.width>0&&r.height>0&&r.top>=0&&r.left>=0;})"
            ".map(e=>{const r=e.getBoundingClientRect();"
            "const t=(e.innerText||e.value||e.getAttribute('aria-label')||'').trim();"
            "return [t, Math.round(r.x+r.width/2), Math.round(r.y+r.height/2)];})"
            ".filter(x=>x[0])))"
        )
        r = _cdp_call(s, counter, "Runtime.evaluate", {"expression": js, "returnByValue": True})
        if r.get("result", {}).get("exceptionDetails"):
            detail = r["result"]["exceptionDetails"].get("text", "JS 执行出错")
            raise RuntimeError(f"页面脚本出错：{detail}")
        raw = r.get("result", {}).get("result", {}).get("value") or "[]"
        items = json.loads(raw)
        if keyword:
            kw = keyword.lower()
            items = [x for x in items if kw in x[0].lower()]
        return page.get("title", ""), items
    finally:
        try:
            s.close()
        except Exception:
            pass


def _do_web(keyword: str = "", port: int = CDP_PORT) -> int:
    title, items = _web_elements(port, keyword)
    print(f"页面：{title}")
    if not items:
        print("没找到可点元素" + (f"（关键词「{keyword}」）" if keyword else ""))
        return 0
    print(f"共 {len(items)} 个：")
    for text, x, y in items[:40]:
        print(f"  {text[:34]:36s} ({x}, {y})")
    return 0


def _do_web_click(text: str, port: int = CDP_PORT, real: bool = False) -> int:
    pages = _cdp_pages(port)
    if not pages:
        print("没有打开的页面", file=sys.stderr)
        return 1
    page = _pick_page(pages)
    path = page["webSocketDebuggerUrl"].split(str(port), 1)[1]
    s, pending = _ws_connect("127.0.0.1", port, path)
    counter = [0]
    try:
        js = (
            "JSON.stringify(([...document.querySelectorAll("
            "'a,button,[role=button],input[type=submit]')]"
            ".filter(e=>{const r=e.getBoundingClientRect();"
            "return r.width>0&&r.height>0&&r.top>=0&&r.left>=0;})"
            ".map(e=>{const r=e.getBoundingClientRect();"
            "const t=(e.innerText||e.value||e.getAttribute('aria-label')||'').trim();"
            "return [t, Math.round(r.x+r.width/2), Math.round(r.y+r.height/2)];})"
            ".filter(x=>x[0])))"
        )
        r = _cdp_call(s, counter, "Runtime.evaluate", {"expression": js, "returnByValue": True})
        if r.get("result", {}).get("exceptionDetails"):
            raise RuntimeError("页面脚本出错：" + r["result"]["exceptionDetails"].get("text", "?"))
        items = json.loads(r.get("result", {}).get("result", {}).get("value") or "[]")
        match = [it for it in items if it[0].lower() == text.lower()]
        if not match:
            match = [it for it in items if text.lower() in it[0].lower()]
        if not match:
            print(f"页面「{page.get('title', '')}」里没有文字含「{text}」的可点元素", file=sys.stderr)
            return 1
        label, x, y = match[0]
        print(f"找到：{label!r} 在页面坐标 ({x}, {y})")
        if real:
            _click_at(x, y, "left", False)
            print("已点击（真实鼠标事件，按页面坐标）")
        else:
            _cdp_call(s, counter, "Input.dispatchMouseEvent",
                      {"type": "mouseMoved", "x": x, "y": y})
            _cdp_call(s, counter, "Input.dispatchMouseEvent",
                      {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
            _cdp_call(s, counter, "Input.dispatchMouseEvent",
                      {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})
            print("已点击（通过浏览器调试接口，不需要屏幕坐标）")
        return 0
    finally:
        try:
            s.close()
        except Exception:
            pass


def _xy(a: list):
    """行动表里的坐标 + 所在窗口，配 % 格式化用。"""
    x, y = int(a[0]), int(a[1])
    return x, y, _where(x, y)


def _plan_shot(a: list, ns) -> str:
    if a and not a[0].startswith("--"):
        target = a[0]
    else:
        target = "临时目录（自动保留最近 %d 张）" % SHOT_KEEP
    return f"截屏并保存到 {target}（不改任何东西）"


def _plan_web_click(a: list, ns) -> str:
    how = "真实鼠标事件" if ns.real else "浏览器调试接口"
    return "在网页里找文字含「%s」的元素并点击（%s）" % (" ".join(a), how)


def _plan_uia_click(a: list, ns) -> str:
    win, name = (a[0] if a else ""), (a[1] if len(a) > 1 else "")
    return "点击窗口「%s」里名为「%s」的控件（按控件找位置，不用估坐标）" % (win, name)


# 行动表：命令 → 文案。固定文案直接写字符串，要拼参数的用函数；表里没有的命令
# （drag / screens / watch / start / stop 等）原样返回命令名，和以前一样。
_PLAN = {
    "size": "读取屏幕分辨率（不改任何东西）",
    "scale": "读取显示缩放比例（不改任何东西）",
    "windows": "列出当前窗口和位置（不改任何东西）",
    "desks": "列出虚拟桌面和当前所在桌面（不改任何东西）",
    "shot": _plan_shot,
    "web": lambda a, ns: f"列出网页里可点的元素{'（过滤：' + ' '.join(a) + '）' if a else ''}（不改任何东西）",
    "web-click": _plan_web_click,
    "uia": lambda a, ns: f"列出窗口「{a[0] if a else ''}」里的控件和坐标（不改任何东西）",
    "uia-click": _plan_uia_click,
    "clean": lambda a, ns: f"清理临时截图，只保留最近 {a[0] if a else SHOT_KEEP} 张（{_shot_dir()}）",
    "desk": lambda a, ns: f"虚拟桌面操作：{a[0] if a else 'list'}（{' '.join(a[1:])}）",
    "find": lambda a, ns: f"在屏幕上查找图片 {a[0] if a else '?'}，返回坐标（不点击）",
    "launch": lambda a, ns: f"打开：{a[0] if a else '?'}",
    "focus": lambda a, ns: f"把标题含「{a[0] if a else '?'}」的窗口切到前台（不点击）",
    "goto": lambda a, ns: f"在浏览器地址栏打开：{a[0] if a else '?'}（输完等 0.6s 再回车）",
    "wait": lambda a, ns: f"等 {a[0] if a else '?'} 秒（什么都不做，等页面/动画加载）",
    "move": lambda a, ns: "把鼠标移动到 (%d, %d)%s，不点击" % _xy(a),
    "click": lambda a, ns: "在 (%d, %d) 单击鼠标左键%s" % _xy(a),
    "dclick": lambda a, ns: "在 (%d, %d) 双击鼠标左键%s" % _xy(a),
    "rclick": lambda a, ns: "在 (%d, %d) 单击鼠标右键%s" % _xy(a),
    "scroll": lambda a, ns: f"滚动滚轮 {a[0]} 格",
    "type": lambda a, ns: f"输入文本：{' '.join(a)[:60]}",
    "key": lambda a, ns: f"按一次键：{a[0]}",
    "hotkey": lambda a, ns: f"按组合键：{' + '.join(a)}",
}


def _plan_text(cmd: str, args) -> str:
    """行动表文案。args 可以是 Namespace（CLI），也可以是字符串列表（serve.py、测试）。"""
    fn = _PLAN.get(cmd)
    if fn is None:
        return cmd
    ns = args if isinstance(args, argparse.Namespace) else _parse_list(cmd, args)
    return fn(list(getattr(ns, "args", None) or []), ns) if callable(fn) else fn


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
    """点一下。直接走系统 API（SetCursorPos + mouse_event），不依赖任何第三方库。"""
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
    """只移动光标，用 SetCursorPos。"""
    ctypes.windll.user32.SetCursorPos(int(x), int(y))


def _scroll_at(n: int) -> None:
    """滚轮 n 格（正数向上），用 mouse_event 的 WHEEL。"""
    ctypes.windll.user32.mouse_event(0x0800, 0, 0, int(n) * 120, 0)


# ---- Win32 输入层：SendInput / keybd_event，不依赖 pyautogui ----


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("u", _INPUTUNION)]


_INPUT_KEYBOARD = 1
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004

_VK_NAMES = {
    "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "backspace": 0x08, "delete": 0x2E, "del": 0x2E,
    "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10,
    "win": 0x5B, "cmd": 0x5B, "capslock": 0x14, "printscreen": 0x2C,
}
for _i in range(1, 13):
    _VK_NAMES[f"f{_i}"] = 0x6F + _i


def _send_unicode(code: int) -> None:
    """发一个 UTF-16 码元。KEYEVENTF_UNICODE 不经过键盘布局，任何字符都能进。"""
    u = ctypes.windll.user32
    for flags in (0, _KEYEVENTF_KEYUP):
        inp = _INPUT(type=_INPUT_KEYBOARD)
        inp.u.ki = _KEYBDINPUT(
            wVk=0, wScan=code,
            dwFlags=_KEYEVENTF_UNICODE | flags,
            time=0, dwExtraInfo=None,
        )
        u.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def _send_text(text: str) -> None:
    """逐字符发文字：不碰剪贴板，中文、emoji 都能直接打进去。"""
    for ch in text:
        if ch == "\n":
            _press("enter")
        elif ch == "\t":
            _press("tab")
        else:
            code = ord(ch)
            if code > 0xFFFF:  # 超出 BMP：拆代理对
                code -= 0x10000
                _send_unicode(0xD800 + (code >> 10))
                _send_unicode(0xDC00 + (code & 0x3FF))
            else:
                _send_unicode(code)


def _vk_of(name: str) -> int:
    key = name.strip().lower()
    if key in _VK_NAMES:
        return _VK_NAMES[key]
    if len(key) == 1:
        return ord(key.upper())
    raise ValueError(f"不认识的键：{name}")


def _key_down(vk: int) -> None:
    ctypes.windll.user32.keybd_event(vk, 0, 0, 0)


def _key_up(vk: int) -> None:
    ctypes.windll.user32.keybd_event(vk, 0, _KEYEVENTF_KEYUP, 0)


def _press(name: str) -> None:
    vk = _vk_of(name)
    _key_down(vk)
    _key_up(vk)


def _hotkey(*names: str) -> None:
    vks = [_vk_of(n) for n in names]
    for vk in vks:
        _key_down(vk)
    for vk in reversed(vks):
        _key_up(vk)


def _paste_ctrl_v() -> None:
    """发一次 Ctrl+V（保留给需要粘贴的场景）。"""
    _hotkey("ctrl", "v")


def _type_text(text: str) -> None:
    """输入文字。走 SendInput，不占剪贴板、不用逐键映射，中文和 emoji 都能打。"""
    _send_text(text)


def _cursor_pos() -> tuple:
    """当前鼠标位置（物理像素）。"""
    pt = ctypes.wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def _drag_to(x1: int, y1: int, x2: int, y2: int, dur: float = 0.4) -> None:
    """按住左键从 (x1,y1) 拖到 (x2,y2)。"""
    u = ctypes.windll.user32
    u.SetCursorPos(int(x1), int(y1))
    time.sleep(0.05)
    u.mouse_event(0x0002, 0, 0, 0, 0)  # 左键按下
    steps = max(8, int(dur * 60))
    for i in range(1, steps + 1):
        nx = int(x1 + (x2 - x1) * i / steps)
        ny = int(y1 + (y2 - y1) * i / steps)
        u.SetCursorPos(nx, ny)
        time.sleep(dur / steps)
    u.mouse_event(0x0004, 0, 0, 0, 0)  # 左键抬起


_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)


def _enum_top_windows(include_minimized: bool = False) -> list:
    """枚举顶层窗口：[(hwnd, title, (l, t, r, b)), ...]，按 z 序（前台在前）。"""
    u = ctypes.windll.user32
    out = []

    def _cb(hwnd, _lparam):
        if not u.IsWindowVisible(hwnd):
            return True
        length = u.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        u.GetWindowTextW(hwnd, buf, length + 1)
        minimized = bool(u.IsIconic(hwnd))
        if minimized and not include_minimized:
            return True
        rect = ctypes.wintypes.RECT()
        u.GetWindowRect(hwnd, ctypes.byref(rect))
        box = (0, 0, 0, 0) if minimized else (rect.left, rect.top, rect.right, rect.bottom)
        out.append((hwnd, buf.value, box))
        return True

    u.EnumWindows(_WNDENUMPROC(_cb), 0)
    return out


def _window_size(hwnd) -> tuple:
    rect = ctypes.wintypes.RECT()
    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.right - rect.left, rect.bottom - rect.top


def _list_windows() -> None:
    for hwnd, title, (l, t, r, b) in _enum_top_windows():
        print(f"{l:>6} {t:>6} {r - l:>6} {b - t:>6}  {title}")


def _goto_cmd(args: list) -> int:
    """在浏览器地址栏打开 URL：Ctrl+L → 输入 → 等下拉稳定 → 回车。

    直接「Ctrl+L、type、enter」三连最容易翻车：地址栏的自动补全下拉还没收起来，
    回车会被它吃掉，页面不动。这里在输入后固定等 0.6s 再回车。
    """
    if not args:
        print("用法：paw.py goto <url>", file=sys.stderr)
        return 2
    url = args[0]
    _hotkey("ctrl", "l")
    time.sleep(0.25)
    _type_text(url)
    time.sleep(0.6)
    _press("enter")
    time.sleep(0.3)
    print("goto", url)
    return 0


def _force_foreground(hwnd) -> bool:
    """还原窗口并抢到前台。系统拒绝 SetForegroundWindow 时用 AttachThreadInput 强抢。"""
    u = ctypes.windll.user32
    u.ShowWindow(hwnd, 9)  # SW_RESTORE：最小化的也能还原
    time.sleep(0.15)
    try:
        u.SetForegroundWindow(hwnd)
    except Exception:
        pass
    u.BringWindowToTop(hwnd)
    time.sleep(0.15)
    if u.GetForegroundWindow() == hwnd:
        return True
    fg = u.GetForegroundWindow()
    tid_fg = u.GetWindowThreadProcessId(fg, None) if fg else 0
    tid_me = u.GetWindowThreadProcessId(hwnd, None)
    if tid_fg and tid_me:
        u.AttachThreadInput(tid_me, tid_fg, True)
        try:
            u.ShowWindow(hwnd, 9)
            u.SetForegroundWindow(hwnd)
            u.BringWindowToTop(hwnd)
        finally:
            u.AttachThreadInput(tid_me, tid_fg, False)
    time.sleep(0.15)
    return u.GetForegroundWindow() == hwnd


def _focus_cmd(args: list) -> int:
    """按标题关键词把窗口切到前台。点击之前先 focus，第一下才不会只落在「激活」上。

    最小化的窗口也能拉起来：先 ShowWindow(SW_RESTORE) 再抢前台。
    """
    if not args:
        print("用法：paw.py focus <标题关键词>", file=sys.stderr)
        return 2

    key = " ".join(args).lower()
    hits = [(h, t) for h, t, _ in _enum_top_windows(include_minimized=True) if key in t.lower()]
    if not hits:
        print(f"没找到标题含「{key}」的窗口", file=sys.stderr)
        return 1

    hwnd, title = hits[0]
    if _force_foreground(hwnd):
        print("focused:", title)
        return 0
    print("激活失败：系统拒绝切前台（试试先点一下别的窗口）", file=sys.stderr)
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


def _foreground_info() -> tuple:
    """前台窗口的 (标题, 宽, 高)；没有就返回 ("(无)", 0, 0)。"""
    u = ctypes.windll.user32
    hwnd = u.GetForegroundWindow()
    if not hwnd:
        return "(无)", 0, 0
    length = u.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    u.GetWindowTextW(hwnd, buf, length + 1)
    w, h = _window_size(hwnd)
    return (buf.value.strip() or "(无标题)"), w, h


def _watch_cmd(args: list) -> int:
    """实时说明「现在在干什么」：前台窗口、标题变化、鼠标位置、空闲时间。"""
    interval = float(args[0]) if args else 2.0
    prev = {}
    print(f"每 {interval}s 刷新一次，Ctrl+C 退出", file=sys.stderr)
    try:
        while True:
            now = time.strftime("%H:%M:%S")
            fg_title, fg_w, fg_h = _foreground_info()
            msg = self = None
            pos = _cursor_pos()
            idle = _idle_seconds()
            idle_txt = f"{idle}s" if idle >= 0 else "?"
            print(
                f"[{now}] 前台：{fg_title}  [{fg_w}x{fg_h}]"
                f"  ｜ 鼠标 ({pos[0]},{pos[1]}) ｜ 空闲 {idle_txt}",
                flush=True,
            )

            cur = {h: t for h, t, _ in _enum_top_windows()}
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
        from pyvda import VirtualDesktop
    except ImportError:
        print("需要 pyvda：pip install pyvda", file=sys.stderr)
        return 1

    try:
        cur = VirtualDesktop.current().number
    except Exception:
        cur = -1

    fg_hwnd = ctypes.windll.user32.GetForegroundWindow()
    groups = {}
    for hwnd, title, (l, t, r, b) in _enum_top_windows():
        try:
            desk = VirtualDesktop.get_desktop_of_window(hwnd).number
        except Exception:
            desk = cur  # 查不到归属时，按当前桌面算
        groups.setdefault(desk, []).append((hwnd, title, r - l, b - t))

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
        for hwnd, title, w, h in groups[desk]:
            mark = "  ← 前台" if hwnd == fg_hwnd else ""
            print(f"  - {title}  [{w}x{h}]{mark}")
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


def _parse_list(cmd: str, args: list) -> argparse.Namespace:
    """把参数列表当成命令行解析一遍（serve.py、steps、单元测试走这条路）。

    认不出的开关不报错，照旧留在位置词里；解析失败按参数错误处理。
    """
    try:
        ns, rest = _PARSER.parse_known_args([cmd] + list(args))
    except SystemExit:
        raise ValueError("开关写法不对")
    ns.args = list(getattr(ns, "args", None) or []) + list(rest)
    return ns


def _scale(a: list, ns) -> int:
    w, h = _screen_size()
    s = _dpi_scale()
    print(f"缩放 {s:.2f}x（{int(s * 100)}%）：物理 {w}x{h}，逻辑 {int(w / s)}x{int(h / s)}")
    print("shot 与 click 都用物理像素；按缩略图/预览图估坐标时，先乘这个比例。")
    return 0


def _web(a: list, ns) -> int:
    port = int(ns.port) if ns.port else CDP_PORT
    try:
        return _do_web(" ".join(a), port)
    except Exception as exc:
        print(f"网页读取失败：{exc}", file=sys.stderr)
        return 1


def _web_click(a: list, ns) -> int:
    port = int(ns.port) if ns.port else CDP_PORT
    if not a:
        print("用法：paw.py web-click \"按钮文字\" [--port 9222] [--real]", file=sys.stderr)
        return 2
    try:
        return _do_web_click(" ".join(a), port, real=bool(ns.real))
    except Exception as exc:
        print(f"网页点击失败：{exc}", file=sys.stderr)
        return 1


def _uia_click(a: list, ns) -> int:
    if len(a) < 2:
        print("用法：paw.py uia-click <窗口标题关键词> <控件名>", file=sys.stderr)
        return 2
    return _do_uia_click(a[0], " ".join(a[1:]))


def _clean(a: list, ns) -> int:
    keep = int(a[0]) if a else SHOT_KEEP
    print(f"清理了 {_clean_shots(keep)} 张（保留最近 {keep} 张）：{_shot_dir()}")
    return 0


def _shot(a: list, ns) -> int:
    c = [int(v) for v in str(ns.crop).split(",")] if ns.crop else []
    crop = (c[0], c[1], c[0] + c[2], c[1] + c[3]) if len(c) == 4 else None
    out = (a[-1] if a else None) or _default_shot_path()
    img = _grab_screen()
    if crop:
        img = img.crop(crop)
    if ns.jpg:
        img.convert("RGB").save(out, quality=88)
    else:
        img.save(out)
    _clean_shots()
    print(out, img.size)
    return 0


def _drag(a: list, ns) -> int:
    x1, y1, x2, y2 = (int(v) for v in a[:4])
    dur = float(a[4]) if len(a) > 4 else 0.4
    _drag_to(x1, y1, x2, y2, dur)
    print("dragged", (x1, y1), "->", (x2, y2))
    return 0


def _wait(a: list, ns) -> int:
    secs = float(a[0]) if a else 1.0
    time.sleep(max(0.0, secs))
    print("waited", secs, "s")
    return 0


def _hotkey_cmd(a: list, ns) -> int:
    _hotkey(*a)
    print("hotkey", " ".join(a))  # 空参数时是 "hotkey "，和老版本一样
    return 0


# 命令表：命令 → (动作, 打印前缀, 前缀后跟几个位置词；None 全部、0 不跟)。
# 动作拿 (位置词, 解析好的开关)，返回退出码（None 当 0）；前缀为 None 时由
# 动作自己打印。顺序照 CMD_LEVEL，方便对着权限表读。
_COMMANDS = {
    "size": (lambda a, ns: print(_screen_size()), None, 0),
    "shot": (_shot, None, 0),
    "windows": (lambda a, ns: _list_windows(), None, 0),
    "find": (lambda a, ns: 0, "find 需要图像识别库，已不再内置；要按图找，用 uia（控件）或 web（网页）", 0),
    "scale": (_scale, None, 0),
    "launch": (lambda a, ns: _do_launch(a[0]), "launched", 1),
    "move": (lambda a, ns: _move_to(int(a[0]), int(a[1])), "moved", 2),
    "desks": (lambda a, ns: _desk_cmd(["list"]), None, 0),
    "desk": (lambda a, ns: _desk_cmd(a), None, 0),
    "screens": (lambda a, ns: _screens_cmd(a), None, 0),
    "watch": (lambda a, ns: _watch_cmd(a), None, 0),
    "focus": (lambda a, ns: _focus_cmd(a), None, 0),
    "wait": (_wait, None, 0),
    "goto": (lambda a, ns: _goto_cmd(a), None, 0),
    "uia": (lambda a, ns: _do_uia(a[0] if a else ""), None, 0),
    "start": (lambda a, ns: 0, None, 0),
    "stop": (lambda a, ns: 0, None, 0),
    "clean": (_clean, None, 0),
    "web": (_web, None, 0),
    "web-click": (_web_click, None, 0),
    "click": (lambda a, ns: _click_at(int(a[0]), int(a[1])), "clicked", 2),
    "dclick": (lambda a, ns: _click_at(int(a[0]), int(a[1]), double=True), "double-clicked", 2),
    "rclick": (lambda a, ns: _click_at(int(a[0]), int(a[1]), button="right"), "right-clicked", 2),
    "scroll": (lambda a, ns: _scroll_at(int(a[0])), "scrolled", 1),
    "drag": (_drag, None, 0),
    "type": (lambda a, ns: _type_text(" ".join(a)), "typed", 0),
    "key": (lambda a, ns: _press(a[0]), "key", 1),
    "hotkey": (_hotkey_cmd, None, 0),
    "uia-click": (_uia_click, None, 0),
}

# 这几个命令在急停检查之前执行（看板、桌面切换不该被急停挡住）。
_EARLY = frozenset(("watch", "screens", "desks", "desk"))


def _dispatch(cmd: str, a: list, ns) -> int:
    """查表执行一条命令：先做动作，再按表里的前缀打印。"""
    fn, say, n = _COMMANDS[cmd]
    rc = fn(a, ns)
    if say:
        print(say, *(a if n is None else a[:n]))
    return 0 if rc is None else rc


def run_one(
    cmd: str, args, level: str, show_plan: bool, dry_run: bool, note: str = ""
) -> int:
    """执行一条动作，返回退出码（0 成功）。args 可以是 Namespace 或字符串列表。"""
    if STOP_FLAG.is_set():
        print("急停中：拒绝执行（在 Web 看板点「解除急停」或重启进程）", file=sys.stderr)
        return 5
    if cmd not in CMD_LEVEL:
        print(f"未知动作：{cmd}", file=sys.stderr)
        return 2
    if not isinstance(args, argparse.Namespace):
        args = _parse_list(cmd, args)
    words = list(getattr(args, "args", None) or [])

    if not _level_allows(level, cmd):
        print(f"权限不足：当前等级 {level}，{cmd} 需要 {CMD_LEVEL[cmd]} 级权限", file=sys.stderr)
        return 3

    hits = _danger_hits(" ".join(words)) if level == "safe" else []
    if hits:
        print(f"安全拦截：这一步看起来涉及「{hits[0]}」，safe 级不允许——"
              "要人工确认，或改用 --level full", file=sys.stderr)
        return 4

    if show_plan:
        plan = _plan_text(cmd, args)
        print(f"计划：{plan + '  · ' + note if note else plan}", file=sys.stderr)
    if dry_run:
        return 0
    if cmd in _EARLY:
        return _dispatch(cmd, words, args)
    if cmd not in ("size", "shot", "windows") and _emergency_pressed():
        print("急停：检测到鼠标左右键同时按下，已中止", file=sys.stderr)
        return 1

    try:
        return _dispatch(cmd, words, args)
    except (IndexError, ValueError) as e:
        print(f"参数错误：{e}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - failsafe 要单独认出来
        if type(exc).__name__ == "FailSafeException":
            print("failsafe: 已中止", file=sys.stderr)
            return 1
        raise


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


# 子命令表：命令 → (位置参数的提示文字或 None, 开关列表, 一句话说明)。
# 开关项形如 ("crop", "x,y,w,h")：第二个值为 None 表示不带值的开关。
# 位置的取值一律当字符串收下，校验留在命令函数里：--json 的 args、行动表文案、
# 「参数错误：…」的提示都要和以前逐字一致。
_SUBCOMMANDS = {
    "size": (None, [], "读屏幕分辨率"),
    "shot": ("path", [("crop", "x,y,w,h"), ("jpg", None)], "截屏保存到文件"),
    "windows": (None, [], "列出窗口标题和位置"),
    "find": ("图片", [], "按图找坐标（需要图像识别库）"),
    "scale": (None, [], "读显示缩放比例"),
    "launch": ("目标", [], "打开网址或程序"),
    "move": ("x y", [], "把鼠标移到坐标"),
    "desks": (None, [], "列出虚拟桌面"),
    "desk": ("子命令 [参数]", [], "虚拟桌面操作：list / new / switch / close"),
    "screens": (None, [], "每个虚拟桌面上开着什么"),
    "watch": ("秒", [], "实时打印当前在做什么"),
    "focus": ("标题关键词", [], "把窗口切到前台"),
    "wait": ("秒", [], "等一会儿"),
    "goto": ("网址", [], "在浏览器地址栏打开"),
    "uia": ("窗口标题", [("deep", None)], "列出窗口里的控件"),
    "start": ("端口", [], "启动常驻服务"),
    "stop": (None, [], "停止常驻服务"),
    "clean": ("保留张数", [], "清理临时截图"),
    "web": ("关键词", [("port", "端口")], "列出网页里可点的元素"),
    "web-click": ("按钮文字", [("port", "端口"), ("real", None)], "点击网页里的元素"),
    "click": ("x y", [], "左键单击"),
    "dclick": ("x y", [], "左键双击"),
    "rclick": ("x y", [], "右键单击"),
    "scroll": ("格数", [], "滚动滚轮"),
    "drag": ("x1 y1 x2 y2 [时长]", [], "按住左键拖拽"),
    "type": ("文字", [], "输入文字"),
    "key": ("键名", [], "按一次键"),
    "hotkey": ("键名...", [], "按组合键"),
    "uia-click": ("窗口标题 控件名", [], "按控件名点击"),
    "run": ("steps.txt 或 -", [], "跑一串步骤"),
}

_FLAGS_BY_CMD = {name: item[1] for name, item in _SUBCOMMANDS.items()}

# 公共开关：开关名 → (argparse 关键字参数, 说明)。默认值在 _DEFAULTS 里。
_COMMON = [
    ("--delay", {"type": float}, "动作前等待秒数"),
    ("--pause", {"type": float}, "每个动作之间的间隔秒数（默认 0.15；想更快设 0.05，页面慢就设 1）"),
    ("--level", {"choices": list(LEVEL_ORDER.keys())}, "权限等级：browse / safe / full（默认 safe，可用 PC_LEVEL 环境变量）"),
    ("--no-plan", {"action": "store_true"}, "不打印行动表"),
    ("--json", {"action": "store_true"}, "以 JSON 输出结果（方便 agent 解析）"),
    ("--dry-run", {"action": "store_true"}, "只打印行动表，不执行"),
    ("--remote", {"nargs": "?", "const": os.environ.get("PAW_REMOTE", "8000")}, "转发给常驻服务执行（快 20 倍）：--remote 8899，或设 PAW_REMOTE"),
    ("--parallel", {"action": "store_true"}, "run 时并行执行「打开/查看」类步骤（鼠标键盘动作仍串行，否则会打架）"),
]

_DEFAULTS = {
    "delay": 0.0, "pause": 0.15, "level": os.environ.get("PC_LEVEL", "safe"), "remote": None,
    "no_plan": False, "json": False, "dry_run": False, "parallel": False,
}


def _common_parser(quiet: bool = False) -> argparse.ArgumentParser:
    """公共开关。子 parser 用 SUPPRESS，否则子层的默认值会盖掉命令前面那份。"""
    p = argparse.ArgumentParser(add_help=False)
    for flag, kw, help_text in _COMMON:
        default = argparse.SUPPRESS if quiet else _DEFAULTS[flag[2:].replace("-", "_")]
        p.add_argument(flag, default=default, help=help_text, **kw)
    return p


def _build_parser() -> argparse.ArgumentParser:
    """子命令解析器：CLI 和 _parse_list 共用一份。"""
    common = _common_parser(quiet=True)
    ap = argparse.ArgumentParser(
        description="control the mouse and keyboard from the CLI", parents=[_common_parser()]
    )
    subs = ap.add_subparsers(dest="cmd", metavar="cmd", required=True)
    for name in ALL_CMDS + ["run"]:
        metavar, flags, help_text = _SUBCOMMANDS[name]
        sp = subs.add_parser(name, parents=[common], help=help_text, description=help_text)
        if metavar:
            sp.add_argument("args", nargs="*", metavar=metavar)
        for flag, hint in flags:
            extra = {"action": "store_true"} if hint is None else {"metavar": hint}
            sp.add_argument("--" + flag, **extra)
    return ap


_PARSER = _build_parser()


def _send_args(cmd: str, ns, words: list) -> list:
    """把开关拼回参数表：--remote 转发用的是纯文本，对面收到还要再解析一遍。"""
    out = list(words)
    for flag, hint in _FLAGS_BY_CMD.get(cmd, ()):
        value = getattr(ns, flag, None)
        if value not in (None, False):
            out += ["--" + flag] + ([str(value)] if hint is not None else [])
    return out


def _run_steps(a, args: list) -> int:
    """跑 steps 文件。--parallel 只并行「打开 / 查看」类步骤，鼠标键盘仍串行。"""
    if not args:
        print("用法：paw.py run <steps.txt|->", file=sys.stderr)
        return 2
    steps = _read_steps(args[0])
    if not steps:
        print("没有可执行的步骤", file=sys.stderr)
        return 2

    if a.parallel:
        mouse_cmds = {"click", "dclick", "rclick", "scroll", "type", "key", "hotkey", "move"}
        bad = [s[1] for s in steps if s[1] in mouse_cmds]
        if bad:
            print("并行模式只并行「打开 / 查看」类步骤；鼠标键盘动作必须串行"
                  f"（这些会打架：{', '.join(bad[:3])}）", file=sys.stderr)
            return 2
        from concurrent.futures import ThreadPoolExecutor

        def _one(item):
            idx, (line, cmd, sargs, note) = item
            print(f"[{idx}/{len(steps)}] {line}", file=sys.stderr)
            return run_one(cmd, sargs, a.level, not a.no_plan, a.dry_run, note)

        with ThreadPoolExecutor(max_workers=min(8, len(steps))) as ex:
            results = list(ex.map(_one, enumerate(steps, 1)))
        bad_idx = next((i for i, rc in enumerate(results, 1) if rc != 0), None)
        if bad_idx is not None:
            print(f"第 {bad_idx} 步失败（返回码 {results[bad_idx - 1]}）", file=sys.stderr)
            return results[bad_idx - 1]
        print(f"完成，共 {len(steps)} 步（并行）", file=sys.stderr)
        return 0

    for idx, (line, cmd, sargs, note) in enumerate(steps, 1):
        print(f"[{idx}/{len(steps)}] {line}", file=sys.stderr)
        rc = run_one(cmd, sargs, a.level, not a.no_plan, a.dry_run, note)
        if rc != 0:
            print(f"第 {idx} 步失败（返回码 {rc}），已停止", file=sys.stderr)
            return rc
    print(f"完成，共 {len(steps)} 步", file=sys.stderr)
    return 0


def main() -> int:
    a = _PARSER.parse_args()
    args = list(getattr(a, "args", None) or [])

    if a.remote:
        return _remote_exec(a.remote, a.cmd, _send_args(a.cmd, a, args), a.json)
    if a.cmd in ("start", "stop"):
        return _do_start(int(args[0]) if args else 8000) if a.cmd == "start" else _do_stop()

    if a.delay:
        time.sleep(a.delay)
    if a.pause >= 0:
        _PG_CONF["pause"] = a.pause

    try:
        if a.cmd == "run":
            return _run_steps(a, args)
        if not a.json:
            return run_one(a.cmd, a, a.level, not a.no_plan, a.dry_run)

        import contextlib
        import io
        import json as _json

        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = run_one(a.cmd, a, a.level, not a.no_plan, a.dry_run)
        rec = {"ok": rc == 0, "code": rc, "cmd": a.cmd, "args": args,
               "stdout": out.getvalue().strip(), "stderr": err.getvalue().strip()}
        if a.cmd in ("click", "dclick", "rclick", "move") and len(args) >= 2:
            try:
                rec["x"], rec["y"] = int(args[0]), int(args[1])
                rec["window"] = _window_at(rec["x"], rec["y"])
            except (ValueError, OSError):
                pass
        print(_json.dumps(rec, ensure_ascii=False))
        return rc

    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

