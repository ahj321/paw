---
name: paw
description: Control this computer's mouse and keyboard from the command line — screenshot, click, type, switch windows, read the display scale. Use when a task needs real desktop GUI interaction (a game launcher, an installer, an old app, a site with no API) and no built-in tool can reach it.
---

# paw

`pc.py` 是这台机器上的电脑控制工具：一条命令做一件事，输出可直接读。

## 什么时候该用它

- 要操作**只有图形界面**的程序：启动器、安装向导、老软件、登录框；
- 要在网页上真正**点按钮、填表单**，而目标站点没有 API；
- 需要看**当前屏幕上到底有什么**（别的命令拿不到画面时）。

不该用的场合：能用现成 API、脚本、命令行工具解决的问题——那些更快也更稳。

## 先做这三件事

1. `python pc.py size` —— 屏幕物理像素，例如 `(3840, 2160)`；
2. `python pc.py scale` —— 显示缩放，例如 `1.50x（150%）`。**截图坐标是物理像素**；按缩略图/预览图估的坐标要先乘这个比例；
3. `python pc.py shot s.png` —— 截一张图，看着图决定下一步。

## 常用命令

```bash
python pc.py shot s.png                 # 截屏
python pc.py shot s.png --crop 0,0,800,600 --jpg
python pc.py click 960 540              # 左键
python pc.py dclick 960 540             # 双击
python pc.py rclick 960 540             # 右键
python pc.py move 960 540               # 只移动
python pc.py drag 100 100 900 700       # 拖拽
python pc.py type "hello"               # 输入（中文走剪贴板）
python pc.py key enter                  # 单键
python pc.py hotkey ctrl s              # 组合键
python pc.py scroll -3                  # 滚轮，负数是向下
python pc.py focus "记事本"              # 按标题把窗口切前台（点击前先做）
python pc.py windows                    # 列窗口
python pc.py launch "steam://open/main" # 打开程序/文件/网址/协议
python pc.py wait 1.5                   # 等待
```

批量动作写进 `steps.txt`（一行一条，`#` 后是备注），然后：

```bash
python pc.py run steps.txt              # 串行
python pc.py run steps.txt --parallel   # 打开/查看类并行，鼠标键盘仍串行
```

## 行动与安全

- 每条命令执行前都会打印计划，例如 `在 (960, 540) 单击鼠标左键（所在窗口：记事本 - 未命名）`——先看窗口标题对不对，再让它动手。
- 权限三档：`browse`（只读）/ `safe`（默认，拦截危险动作）/ `full`。加 `--level` 指定。
- 急停：左右键同时按下。
- 支付、账号、删除这类操作不要交给它，人工完成。

## MCP 方式

如果客户端支持 MCP，直接接 `paw_mcp.py`，工具名与上面命令一一对应（`screenshot`、`click`、`type_text`、`focus_window`…），不用自己拼命令行。
