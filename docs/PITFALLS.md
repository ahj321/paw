# 踩过的坑

写这个工具时踩的坑，分成两半：**已经修掉的**（记下原因，免得以后又踩回去）和**还开着的**（知道有问题，但暂时没动）。

每条都写三件事：现象、原因、怎么处理。

---

## 一、已经修掉的

### 1. 点不中按钮：逻辑坐标和物理坐标混着用

**现象**：`paw click 960 540` 明明给了坐标，光标也动了，但点到的不是想点的地方；同一套坐标在 `paw windows` 里对得上，截图上却对不上。

**原因**：两种坐标混用。`paw shot` 截出来的是物理像素（`GetSystemMetrics`），脚本进程默认按逻辑像素（DPI 缩放）算。150% 缩放下，逻辑 2560×1440 对物理 3840×2160，坐标差 1.5 倍。

**处理**：进程启动时 `SetProcessDpiAwareness(2)` 声明 DPI 感知，所有坐标统一走物理像素；`paw scale` 专门打印当前比例，截图缩放时按它换算。

### 2. 截图只有壁纸 / 全黑

**现象**：`paw shot` 存下来的图是纯壁纸，任务栏和窗口都不见了。

**原因**：两个坑叠在一起。一是屏幕锁着的时候截屏本来就只有锁屏前的残留画面；二是 Pillow 的 `ImageGrab.grab()` 在 Windows 上会抛 `OSError: screen grab failed`，而 .NET 的 `CopyFromScreen` 报 `The handle is invalid`。

**处理**：不走 Pillow 抓屏，自己调 GDI：`GetDC(0)` → `CreateCompatibleDC` → `CreateCompatibleBitmap` → `SelectObject` → `BitBlt` → `GetDIBits`，再喂给 `Image.frombuffer`。**关键**：`GetDC`/`CreateCompatibleDC`/`CreateCompatibleBitmap`/`SelectObject` 的 `restype` 必须显式设成 `ctypes.c_void_p`，`DeleteDC`/`DeleteObject` 的 `argtypes` 也必须声明，否则 64 位句柄被截成 32 位，`BitBlt` 直接返 0 或者抛 `OverflowError: int too long to convert`。

### 3. `focus` 拉不起最小化的窗口

**现象**：窗口最小化之后，`paw focus "记事本"` 报成功，但窗口没到前台。

**原因**：底层用 pygetwindow 的 `activate()`，窗口最小化时它抛 `PyGetWindowException`。

**处理**：自己走 Win32：`ShowWindow(hwnd, 9)` 还原 → `SetForegroundWindow` → `BringWindowToTop`，再用 `GetForegroundWindow()` 校验；校验不过就 `AttachThreadInput` 把前台线程和自己的线程挂一起硬抢一次。

### 4. 依赖从 8 个减到 1 个

**现象**：一开始 `pip install pyautogui pillow`，装完连带进来 pygetwindow、pymsgbox、pytweening、pyscreeze、mouseinfo、pyrect 六个包。

**原因**：pyautogui 的输入/窗口/截图三件事全是包装其他库。

**处理**：全部换成 Win32 系统调用——`SendInput`（带 `KEYEVENTF_UNICODE`，中文和 emoji 直接打，不占剪贴板）、`mouse_event`、`SetCursorPos`、`EnumWindows`、GDI 截图。最后只剩 Pillow 一个，负责存 PNG/JPG。

### 5. `paw shot` 往仓库里堆截图

**现象**：随手 `paw shot` 几次，仓库根目录糊了一片 png。

**原因**：默认输出路径是当前目录。

**处理**：不带路径时存到 `%TEMP%\paw\`，只保留最近 `SHOT_KEEP=50` 张，每次截完自动清。**带路径的截图永远不删**——用户指名的文件工具不碰。

### 6. `pc.py` 和 `paw.py` 双入口

**现象**：改名前叫 `pc.py`，改名后老脚本 `python pc.py click ...` 全挂；有一阵两个文件都在，改一处忘另一处。

**处理**：v1.3 先留 `pc.py` 当转发壳（`import paw`），v2.0 直接删掉，只留 `paw.py`。

### 7. `paw.bat` 一跑就崩

**现象**：双击 `paw.bat` 闪一下就没，什么都不发生。

**原因**：三个都踩了。文件里有中文注释，cmd 按 GBK 解析直接崩；`timeout /t 2` 在非交互环境报错；`start /b` 起后台服务会把控制台卡住。

**处理**：整个 bat 只写 ASCII；用 `start "" /min python "%~dp0paw.py" start %PORT%` 起服务；等待用 `ping -n 4 127.0.0.1 >nul` 代替 `timeout`。

### 8. 常驻服务起不来 / 进程判活不准

**现象**：`serve.py` 报 `ImportError`；有时服务其实活着却被判成没起，或者反过来说已经在跑其实是上次的僵尸进程。

**原因**：模块重命名后 `import pc` 没跟着改；判活用 `tasklist` 解析文本，遇到同名进程就乱。

**处理**：`import paw as pc`；判活改成 `OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION)` 拿句柄，拿到就算活着。

### 9. `paw windows` 报 `AttributeError: module 'ctypes' has no attribute 'wintypes'`

**原因**：`ctypes.wintypes` 是子模块，必须先 `import ctypes.wintypes` 才能用 `ctypes.wintypes.RECT`。

**处理**：文件头显式 `import ctypes.wintypes`。

### 10. `hotkey` 少打一个空格

**现象**：`paw hotkey` 不带参数时，输出是 `hotkey `（尾巴多一个空格）。

**原因**：拼字符串时没去掉尾部。

**处理**：单独留了 `_hotkey_cmd` 保住原来那一个空格的输出——这是既有行为，重构时对拍抓到的，不擅自改。

### 11. MCP 子进程卡死 120 秒 / 中文乱码

**现象**：`paw_mcp.py` 起起来之后，客户端调一次要等两分钟超时。

**原因**：MCP server 是父进程序列化后 spawn 的子进程，继承了 stdin，读不到 EOF 就一直等。

**处理**：子进程 `stdin=subprocess.DEVNULL`，输出强制 UTF-8。

### 12. 网页元素列表永远是 0 个

**现象**：`paw web` 返回空数组，但页面上明明有按钮。

**原因**：注入的 JS 末尾多写了一个 `)`。`Runtime.evaluate` 不会抛异常，它在返回值里塞 `exceptionDetails`，而这一层只看 `value`，于是 `None` 被当成空列表。

**处理**：取结果时先看 `exceptionDetails`，有就报错，别静默返回 0。

### 13. CDP 连上了但页面选错

**现象**：`web-click` 点不到东西，或者报找不到。

**原因**：浏览器的 `/json` 会列出一堆 target：`edge://newtab`、欢迎页、扩展页……随便挑第一个就挑错了。

**处理**：`_pick_page` 先跳过 `edge://` 和 `chrome://` 内部页、再跳过欢迎/致谢页，优先选真内容页。

### 14. 旧命令行把开关的值当成了文件名

**现象**：`paw shot out.png --crop 0,0,800,600` 存出来一个名字叫 `--crop` 的文件，裁剪也没生效。

**原因**：`parse_known_args` 把不认识的开关和它的值一起丢进位置参数，`args[-1]` 就变成了 `0,0,800,600`。

**处理**：改成 argparse 子命令，每个开关在表里有明确声明，这个坑自然就没了。

### 15. `ssh` 每次多出一个叫 `NUL` 的文件

**现象**：仓库根目录反复出现一个 102 字节、名字叫 `NUL` 的普通文件，删了还会再长。

**原因**：`~/.ssh/config` 里写了 `UserKnownHostsFile NUL`，本意是「不写 known_hosts」，但 ssh 把它当成文件名去创建——`NUL` 是 Windows 保留名，于是落成一个普通文件。

**处理**：改成 `UserKnownHostsFile "C:/Users/<用户名>/.ssh/known_hosts"` + `StrictHostKeyChecking accept-new`。

### 16. 浏览器 / pip 连不上外网

**现象**：GitHub 打不开（`ERR_CONNECTION_CLOSED`），但命令行 `curl` 通；`pip install` 要 30 多秒。

**原因**：Edge 走 QUIC/HTTP3 会绕过系统代理；pypi 官方源对代理出口 IP 限速。

**处理**：浏览器加 `--proxy-server=http://127.0.0.1:7897` 启动；pip 换国内镜像 `pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple`。

---

## 二、后面又修掉的（v2.3）

这七条是「知道有问题、先记下来」的，后来一起处理掉了。

### 17. 行动表在 `try` 外面打印

**现象**：`paw click 960`（少给一个坐标）直接把 `IndexError` 的 traceback 甩到终端，退出码 1。

**原因**：`run_one` 里计划打印在 `try` 之前，异常没人接。

**处理**：计划那段自己包一层 `try`，`IndexError` / `ValueError` 一律变成 `参数错误：…` + 退出码 2，和真正的执行错误一个口径。

### 18. `shot` 的计划文案和实际保存不是同一个文件

**现象**：`paw shot a.png b.png` 预览说存 `a.png`，实际存的是 `b.png`。

**原因**：计划取 `a[0]`，执行取 `a[-1]`。

**处理**：计划也取最后一个非开关词，两边用同一份判断。

### 19. `uia --deep` 走服务时永远不生效

**现象**：命令行加 `--deep` 能看到子控件，`serve.py` / MCP 过来加 `--deep` 没反应。

**原因**：`_do_uia` 判断的是 `"--deep" in sys.argv`，压根没看传进来的参数。

**处理**：`_do_uia(kw, deep=False)` 显式收参数，命令表里从 `ns.deep` 取。

### 20. `run_one("start")` / `run_one("stop")` 只是空转

**现象**：只有命令行入口能真的启停常驻服务，程序里调用这两个命令什么都不做、还返回 0。

**原因**：命令表里它们被写成了 `lambda a, ns: 0` 占位。

**处理**：接回真正的 `_do_start(port)` / `_do_stop()`，命令行和程序调用走同一套。

### 21. `--remote` 把 Windows 路径的反斜杠吃掉了

**现象**：`paw --remote 8899 shot C:\a\b.png`，服务端存成 `C:ab.png`。

**原因**：转发时用空格拼接纯文本，对面 `shlex.split` 把 `\` 当转义符。

**处理**：发送前每个参数过一遍 `shlex.quote`，往返之后原样还原（`C:\a b\c.png` 这种带空格也保住）。

### 22. `web` 的预览里混进端口号

**现象**：`paw web --port 9222` 的计划文案里多一个 `9222`。

**原因**：旧版只按 `--` 前缀过滤，`--port` 后面那个值被当成过滤词。

**处理**：argparse 子命令之后开关和位置参数本来就分开了，计划只用位置词。

### 23. `uia-click` 的控件名被空格切开

**现象**：控件名里带空格（比如 `4K Camera`）时，`uia-click` 找不到控件。

**原因**：旧命令行路径把第 2 个及之后的参数用空格拼回去，再被重新切分。

**处理**：参数按表传，名字原样保留；`uia-click "窗口" "控件 名字"` 现在能对上。

### 24. 窗口比屏幕还大：滚轮和按键都"失灵"

**现象**：浏览器里的页面明明能滚，但 `paw scroll`、`paw key pagedown`、`paw key end` 全都没反应，页面纹丝不动；要点的按钮永远在"下一屏"，点不到也滚不到。窗口标题栏、地址栏都正常，点击和打字也正常。

**原因**：窗口被拉到了屏幕外。调用 `MoveWindow`（或手动拖拽）给的是**物理像素**，而 Windows 在 150% 缩放下按**逻辑像素**解释——传 `1800×1140` 实际变成 `2700×1710`，比 2560×1440 的屏幕还大。结果窗口右下角整块跑到屏幕之外：滚动条在那里、按钮也在那里，滚轮坐标又落在窗口内，于是"看着能滚，其实滚的是看不见的部分"。

**处理**：`paw windows` 报的宽高是缩放前的值，**乘以缩放比才是实际像素**；`paw scale` 能打出这个比例（本机 1.50x）。要让窗口落进 2560×1440，`MoveWindow` 传 `1000×640` 这一档而不是 `1800×1140`。判断方法：`paw windows` 的宽 × 缩放比 > 屏幕宽，就说明窗口伸出屏幕了——先把窗口缩回屏幕内，再谈滚动和点击。

---

## 三、记录方式

发现新坑就往这里加一条，写上**现象 / 原因 / 处理**。修掉之后从「还开着的」搬到「已经修掉的」，别删——同一个坑踩第二次的时候，这几行能省半天。
