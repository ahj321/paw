#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
paw 的本地 Web 看板。

    python serve.py            # 打开 http://127.0.0.1:8000
    python serve.py 8765       # 换端口

页面上能看：每块虚拟桌面上开着什么、前台是什么、鼠标在哪、多久没动；
能点：急停（红色大按钮）、动作执行（和命令行同一套权限）、权限切换。
只监听本机回环地址，不联网、不常驻。
"""

import contextlib
import io
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pc

LEVEL = {"v": os.environ.get("PC_LEVEL", "safe")}
LOG = []
LOG_LOCK = threading.Lock()


def _log(line: str) -> None:
    with LOG_LOCK:
        LOG.append(f"{time.strftime('%H:%M:%S')} {line}")
        del LOG[:-200]


def _collect_windows():
    try:
        import pygetwindow as gw
    except ImportError:
        return []
    rows = []
    for w in gw.getAllWindows():
        title = (w.title or "").strip()
        if not title or w.width <= 0:
            continue
        rows.append({"title": title, "w": w.width, "h": w.height})
    return rows


def _state():
    import pyautogui

    try:
        import pygetwindow as gw

        fg = gw.getActiveWindow()
        fg_title = (fg.title or "").strip() if fg else ""
        fg_size = f"{fg.width}x{fg.height}" if fg else ""
    except Exception:
        fg_title, fg_size = "", ""
    pos = pyautogui.position()
    idle = pc._idle_seconds() if hasattr(pc, "_idle_seconds") else -1
    return {
        "fg": fg_title,
        "fg_size": fg_size,
        "mouse": [pos.x, pos.y],
        "idle": idle,
        "level": LEVEL["v"],
        "stopped": pc.STOP_FLAG.is_set(),
        "windows": _collect_windows(),
        "log": LOG[-40:],
    }


def _run(cmd: str, args: list) -> dict:
    buf = io.StringIO()
    t0 = time.time()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = pc.run_one(cmd, args, LEVEL["v"], True, False)
    out = buf.getvalue().strip()
    for line in out.splitlines():
        _log(line)
    return {
        "ok": code == 0,
        "code": code,
        "out": out,
        "ms": int((time.time() - t0) * 1000),
    }


PAGE = r"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>paw</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#14161a;color:#e6e8eb;font:14px/1.5 -apple-system,"Segoe UI",system-ui,sans-serif}
header{display:flex;gap:12px;align-items:center;padding:12px 16px;background:#1b1e24;border-bottom:1px solid #2a2f38;position:sticky;top:0;flex-wrap:wrap}
h1{font-size:15px;margin:0 8px 0 0;font-weight:600;letter-spacing:.02em}
.pill{padding:3px 9px;border-radius:999px;background:#23272f;color:#9aa4b2;font-size:12px}
.fg{max-width:44vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
button{cursor:pointer;border:1px solid #333a45;background:#23272f;color:#e6e8eb;border-radius:8px;padding:6px 10px;font-size:13px}
button:hover{background:#2b313a}
.stop{background:#c62828;border-color:#e53935;font-weight:700;padding:9px 18px;font-size:15px}
.stop:hover{background:#e53935}
.stop.on{background:#4a4a4a;border-color:#666}
select{background:#23272f;color:#e6e8eb;border:1px solid #333a45;border-radius:8px;padding:6px}
main{display:grid;grid-template-columns:1.1fr 1fr;gap:14px;padding:14px;align-items:start}
@media(max-width:900px){main{grid-template-columns:1fr}}
section{background:#1b1e24;border:1px solid #262b33;border-radius:12px;padding:12px}
h2{font-size:13px;color:#8b95a3;margin:0 0 10px;font-weight:600}
ul{list-style:none;margin:0;padding:0}
li{padding:5px 0;border-bottom:1px dotted #262b33;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
li:last-child{border:0}
li .dim{color:#727c89;font-size:12px}
.row{display:flex;gap:8px;margin-bottom:8px}
input[type=text]{flex:1;background:#14161a;border:1px solid #333a45;color:#e6e8eb;border-radius:8px;padding:7px 9px;font-size:13px}
pre{background:#14161a;border:1px solid #262b33;border-radius:8px;padding:10px;max-height:38vh;overflow:auto;font-size:12px;margin:0;white-space:pre-wrap;word-break:break-all}
.bad{color:#ff8a80}
.good{color:#8fe3a3}
</style></head><body>
<header>
  <h1>paw</h1>
  <span class="pill">权限 <select id="level">
    <option value="browse">browse</option><option value="safe">safe</option><option value="full">full</option>
  </select></span>
  <span class="pill fg" id="fg">前台：—</span>
  <span class="pill" id="mouse">鼠标 —</span>
  <span class="pill" id="idle">空闲 —</span>
  <span style="flex:1"></span>
  <button class="stop" id="stop">急停</button>
</header>
<main>
  <section>
    <h2>每块屏幕在干什么</h2>
    <ul id="wins"><li class="dim">读取中…</li></ul>
  </section>
  <section>
    <h2>动作</h2>
    <div class="row">
      <input id="cmd" type="text" placeholder="和命令行一样：shot a.png / click 500 400 / launch notepad / run steps.txt">
      <button id="go">执行</button>
    </div>
    <pre id="out" class="dim">还没有执行动作。</pre>
  </section>
  <section style="grid-column:1/-1">
    <h2>记录</h2>
    <pre id="log" class="dim">—</pre>
  </section>
</main>
<script>
const $ = (s) => document.querySelector(s);
let stopped = false;
async function api(path, body) {
  const r = await fetch(path, body ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : {});
  return r.json();
}
function esc(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
async function refresh(){
  try{
    const s = await api('/api/state');
    $('#fg').textContent = '前台：' + (s.fg || '—') + (s.fg_size ? '  ['+s.fg_size+']' : '');
    $('#mouse').textContent = '鼠标 ' + s.mouse.join(',');
    $('#idle').textContent = '空闲 ' + (s.idle>=0 ? s.idle+'s' : '?');
    $('#level').value = s.level;
    stopped = s.stopped;
    $('#stop').textContent = stopped ? '已急停 · 点此解除' : '急停';
    $('#stop').classList.toggle('on', stopped);
    $('#wins').innerHTML = s.windows.length ? s.windows.map(w=>'<li>'+esc(w.title)+' <span class="dim">['+w.w+'×'+w.h+']</span></li>').join('') : '<li class="dim">没有窗口</li>';
    $('#log').textContent = (s.log||[]).join('\n') || '—';
  }catch(e){ $('#fg').textContent = '看板连不上（serve.py 还在跑吗？）'; }
}
$('#stop').onclick = async () => {
  const r = await api(stopped ? '/api/reset' : '/api/stop', {});
  await refresh();
};
$('#level').onchange = async () => {
  const lv = $('#level').value;
  const r = await api('/api/level', {level: lv, confirm: lv==='full' ? 'yes' : ''});
  if (!r.ok) alert(r.error || '切换失败');
};
$('#go').onclick = async () => {
  const line = $('#cmd').value.trim(); if(!line) return;
  $('#out').textContent = '执行中…';
  const r = await api('/api/action', {line});
  $('#out').innerHTML = '<span class="'+(r.ok?'good':'bad')+'">'+(r.ok?'成功':'失败（'+r.code+'）')+' '+r.ms+'ms</span>\n' + esc(r.out || '');
  await refresh();
};
$('#cmd').addEventListener('keydown', e => { if(e.key==='Enter') $('#go').click(); });
refresh(); setInterval(refresh, 1500);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "paw"

    def _send(self, body: bytes, ctype: str, code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8", code)

    def do_GET(self):  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._send(PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path.startswith("/api/state"):
            self._json(_state())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            body = json.loads(raw or b"{}")
        except Exception:
            return self._json({"ok": False, "error": "bad json"}, 400)

        if self.path.startswith("/api/stop"):
            pc.STOP_FLAG.set()
            _log("急停")
            return self._json({"ok": True, "stopped": True})
        if self.path.startswith("/api/reset"):
            pc.STOP_FLAG.clear()
            _log("解除急停")
            return self._json({"ok": True, "stopped": False})
        if self.path.startswith("/api/level"):
            lv = str(body.get("level", ""))
            if lv not in ("browse", "safe", "full"):
                return self._json({"ok": False, "error": "未知等级"}, 400)
            if lv == "full" and str(body.get("confirm")) != "yes":
                return self._json({"ok": False, "error": "切 full 需要确认"}, 400)
            LEVEL["v"] = lv
            _log(f"权限切到 {lv}")
            return self._json({"ok": True, "level": lv})
        if self.path.startswith("/api/action"):
            line = str(body.get("line", "")).strip()
            if not line:
                return self._json({"ok": False, "code": 2, "out": "空动作"}, 400)
            import shlex

            try:
                parts = shlex.split(line)
            except ValueError as e:
                return self._json({"ok": False, "code": 2, "out": f"参数解析失败：{e}"}, 400)
            if not parts:
                return self._json({"ok": False, "code": 2, "out": "空动作"}, 400)
            _log("执行 " + line)
            return self._json(_run(parts[0], parts[1:]))
        return self._json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):  # 静音默认访问日志
        return


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="paw 本地看板")
    ap.add_argument("port", nargs="?", type=int, default=8000, help="端口，默认 8000")
    ap.add_argument("--host", default="127.0.0.1", help="监听地址，默认只看本机")
    a = ap.parse_args()

    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"看板：http://{'127.0.0.1' if a.host in ('0.0.0.0', '::') else a.host}:{a.port}   （Ctrl+C 退出）", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("已停止", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
