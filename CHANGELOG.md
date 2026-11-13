# 更新记录

## v2.3

**三段 elif 链换成三张表，顺手把记下来的七个坑修了。**

- `run_one` 从 214 行缩到 47 行：命令表 `_COMMANDS`（每条 `(动作, 打印前缀, 前缀后跟几个位置词)`）+ `_dispatch` 统一「先做动作、再按前缀 print」；
- 行动表改成 `_PLAN` 字典（26 条），`_plan_text` 只剩查表；
- 参数解析换成 argparse 子命令（30 个子命令 + 9 个全局开关），`paw click --help` 现在能看到用法；
- 修：行动表在 `try` 外打印，少给参数会甩 `IndexError` traceback → 现在统一 `参数错误：…` + 退出码 2；
- 修：`shot` 的预览文案与实际保存不是同一个文件（`paw shot a.png b.png`）；
- 修：`uia --deep` 走 serve / MCP 时永远不生效（原来判断 `sys.argv`）；
- 修：`run_one("start")` / `run_one("stop")` 只是空转返回 0；
- 修：`--remote` 转发时 Windows 路径反斜杠被对面 `shlex` 吃掉（现在逐参数 `shlex.quote`）；
- 修：`web` 预览文案里混进端口号；
- 修：`uia-click` 的控件名带空格时被重新切开；
- 全部踩过的坑整理进 [docs/PITFALLS.md](docs/PITFALLS.md)。

## v2.2

**依赖从 8 个减到 1 个：只留 Pillow。**

- `type` 改走 `SendInput` + `KEYEVENTF_UNICODE`：中文和 emoji 直接打，不再借剪贴板（以前会占用你的剪贴板）；
- 截图改走 GDI（`GetDC` → `CreateCompatibleDC` → `BitBlt` → `GetDIBits`），Pillow 只负责裁剪和编码，不再依赖 `ImageGrab.grab()`；
- 鼠标、滚轮、拖拽、按键、快捷键全部走 `SetCursorPos` / `mouse_event` / `SendInput`；
- 窗口枚举改走 `EnumWindows` + `GetWindowRect`，不再需要 `pygetwindow`；
- 修 `paw windows` 因 `ctypes.wintypes` 未导入而静默失败；
- 安装从 `pip install pyautogui pillow`（实际拖进 8 个包）降到 `pip install pillow`；
- `find` 不再内置图像识别：按图找的活交给 `uia`（控件）或 `web`（网页）。

## v2.1

**网页上的按钮也能按名字点了。**

- 新增 `web`：通过 Chrome DevTools 协议列出网页里每个可点元素（链接、按钮、`role=button`、提交框）的文字和坐标；
- 新增 `web-click "文字"`：按文字找元素并点击，默认走浏览器调试接口（`Input.dispatchMouseEvent`），不需要屏幕坐标、不怕窗口被挡；`--real` 改成移动真实光标；
- 手写 WebSocket 客户端（握手 / 帧封装 / 掩码 / 分片长度），**不引第三方库**，paw 依然零新依赖；
- 浏览器要用 `--remote-debugging-port=9222 --user-data-dir=<独立目录>` 启动（默认配置目录不允许开调试端口），命令连不上时会把完整启动命令打出来；
- 自动跳过 `edge://` 内部页和浏览器欢迎/致谢页，优先选真正的内容页；
- 中英 README 加「网页操作（web）」/「Web pages (web)」一节。

## v2.0

**⚠️ 破坏性版本：`pc.py` 没了，`shot` 默认存到临时目录。**

- **移除** `pc.py`（v1.3 留下的兼容转发层）。还在用 `python pc.py ...` 的脚本，改成 `paw ...` 或 `python paw.py ...`；
- **行为变更**：`paw shot` 不带路径时，从"存当前目录的 screen.png"改成"存 `%TEMP%\paw\shot-日期-时间.png`"，并且自动只保留最近 50 张；
- 带路径的 `paw shot D:/a.png` 不变，paw 永远不删你指定的文件；
- 新增 `clean` 命令：`paw clean` 立刻清一次临时截图，`paw clean 20` 指定保留数量；
- issue 模板里的命令名同步成 `paw`。

## v1.4

**按控件点击，常驻服务，一条命令几十毫秒。**

- 新增 `uia` / `uia-click`：走 Windows UI Automation，列出窗口里每个控件的精确矩形，或直接按名字点击——不用截图估坐标，窗口被挡住也能点；
- 新增 `start` / `stop`：后台常驻服务，之后每条命令走 HTTP（约 9ms），不再每次启动 Python；
- 新增 `paw.bat`：Windows 包装脚本，加进 PATH 后任意目录敲 `paw click ...`，第一次自动起服务；
- `serve.py` 修 `import pc` 的错（pc.py 只是转发壳，导致所有 API 报 `AttributeError`）；
- 速度：`python paw.py size` 160ms → 常驻服务内 0ms，`click` 24ms；
- README 中英同步新增「按控件点击（uia）」「常驻服务（start / stop）」两节。

## v1.3

**入口改回本名。**

- 主程序 `pc.py` → `paw.py`，命令统一成 `python paw.py ...`；
- 旧的 `pc.py` 保留为兼容转发（`import paw`），已经在用的脚本一行都不用改；
- 文档、示例、`steps.txt.example`、MCP server 全部指向 `paw.py`。

## v1.2

**接进 agent 只要粘一段配置。**

- 新增 `paw_mcp.py`：MCP server，把命令包成 14 个工具（`screenshot` / `click` / `type_text` / `focus_window`…）。Claude Desktop、Cursor、Cline、Continue、Windsurf 直接可用，不用改 paw 的代码。
- 新增 `SKILL.md`：给 agent 读的说明书——什么时候该用它、三条起手命令、命令表、安全边界。
- README 增「在 agent 里用（MCP）」一节，附各家客户端的配置片段。
- 修 MCP 子进程继承 stdin 导致卡死 120 秒的问题（`stdin=DEVNULL`）；强制 UTF-8 输出，中文不再乱码。

## v1.1

**更快，而且行动表能说清「点的是谁」。**

- 速度：`click` / `dclick` / `rclick` / `move` / `scroll` / `type` 改走 Win32 系统调用，不再加载 pyautogui；`size` 改用 `GetSystemMetrics`，`shot` 直接用 `PIL.ImageGrab`。实测单次调用从 0.5–1.1 秒降到 0.18–0.42 秒。
- 新增 `scale`：打印显示缩放比例（如 `1.50x（150%）：物理 3840x2160，逻辑 2560x1440`），提醒坐标用物理像素。踩过的坑：按截图预览图估坐标会偏，150% 缩放下要乘 1.5。
- 默认动作间隔 `--pause` 从 0.3 秒降到 0.15 秒。
- 行动表现在带上坐标所在的窗口标题：`计划：在 (960, 540) 单击鼠标左键（所在窗口：记事本 - 未命名）`，坐标不再只是两个数字。
- `steps.txt` 支持行内 `#` 备注，备注会附在对应步骤的行动表后面，如 `click 960 540  # 点保存按钮`。
- README 重写：去掉重复段落，按「是什么 / 为什么 / 怎么用 / 风险」重排。

## v1.0

**改了底层：从「能点」变成「点得准、打得快」。**

- 新增 `focus <标题>`：按标题把窗口切到前台。点击之前先 focus，第一下才真的落在目标上（之前第一下常常只是激活窗口）。
- 启动时声明 DPI 感知（`SetProcessDpiAwareness(2)`）：4K + 150% 缩放下，坐标和物理像素对齐，点击不再偏。
- 中文输入改走 pyperclip：写剪贴板 → 稍等 → 粘贴 → 把原来的剪贴板还回去。修掉了「保存对话框里文件名被别的文字搅乱」的老毛病。
- 新增 `wait <秒>`：显式等待页面/动画加载，不用靠截图硬猜。
- 新增 `--pause <秒>`：动作间隔可调（默认 0.3；批量执行时 0.05 能快不少）。
- 新增 `screens`：按虚拟桌面分组列窗口，标出前台窗口。
- 新增 `watch [间隔]`：实时打印「前台窗口/标题变化/鼠标位置/空闲秒数」。
- 新增 `run --parallel`：只并行「打开/查看」类步骤，鼠标键盘动作仍然串行。
- 修复控制台 GBK 下打印特殊字符（零宽空格等）崩溃：stdout/stderr 改用 `errors='replace'`。
- 本地 Web 看板 `serve.py`：状态条、急停按钮、动作输入、权限切换、日志，1.5 秒刷新。
- 权限三档定型：`browse` / `safe`（危险动作拦截）/ `full`；`open` 命令与 `open` 权限名已彻底移除，统一为 `launch` / `browse`。

## v0.0

最初版本：`size` / `shot` / `click` / `dclick` / `rclick` / `move` / `type` / `key` / `hotkey` / `scroll` / `find` / `windows` / `launch`，加左右键同按急停。
