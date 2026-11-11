# 更新记录

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
