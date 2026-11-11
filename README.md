# ai-pc-control

用命令行控制鼠标和键盘的小工具，给「能看图的模型 + 能执行命令的 agent」当手用。

> 本项目由 deepseek-V4.1-Flash-1017 生成。

没有后台常驻服务，没有截图上传，没有依赖一堆框架——只有一个 `pc.py`，跑一条命令做一件事。

## 为什么

现在的桌面自动化方案大多很重：要装桌面客户端、配 MCP、接云端 API。但很多时候你只需要有人（或某个 agent）能：

1. 截一张图看看屏幕长什么样；
2. 点一下按钮；
3. 打一行字。

这个工具只做这三件事，并且保持「人能看懂、人敢运行」。

## 安装

```bash
pip install pyautogui pillow
# 可选：列出窗口
pip install pygetwindow
```

Windows / macOS / Linux（X11）都可运行，Windows 上中文输入已处理好（走剪贴板）。

## 用法

```bash
python pc.py size                        # 屏幕分辨率
python pc.py shot screen.png             # 截屏到文件
python pc.py click 960 540               # 左键单击
python pc.py dclick 960 540              # 双击
python pc.py rclick 960 540              # 右键
python pc.py move 100 200                # 移动鼠标（不点）
python pc.py type "hello world"          # 输入英文
python pc.py type "你好呀"               # 输入中文（剪贴板）
python pc.py key enter                   # 按一个键
python pc.py hotkey ctrl s               # 组合键
python pc.py scroll -3                   # 向下滚 3 格
python pc.py find button.png 0.9         # 在屏幕上找图，返回坐标
python pc.py windows                     # 列出窗口及位置（需 pygetwindow）
python pc.py click 960 540 --delay 2     # 动作前先等 2 秒
```

## 权限等级

三档，用 `--level`（或环境变量 `PC_LEVEL`）选，默认 `safe`：

| 等级 | 允许 |
| --- | --- |
| `open` 末级 | 查看与打开：size / shot / windows / find / open / move |
| `safe` 次级 | 再加 click / dclick / rclick / scroll / type / key / hotkey；**但命中「付款、注册注销、删改东西」时拒绝** |
| `full` 最高级 | 允许一切 |

```bash
python pc.py open https://bilibili.com --level open   # 只打开网页
python pc.py click 960 540 --level safe               # 普通点击可以
python pc.py type "确认付款" --level safe             # 被拦截
python pc.py type "确认付款" --level full             # 放行
```

`safe` 级的拦截是按**命令 + 文字关键词**判断的「尽力而为」：拦得住明显的付款、注销、删除、发送、格式化，但不认识所有变体与流程（比如点进三步之后的确认页）。真正重要的账号操作，请人来做。

## 行动表

每次执行前会先打印一行「计划：…」，说明这一步接下来要做什么，例如：

```
计划：在 (960, 540) 单击鼠标左键
```

- `--no-plan`：关掉行动表（你信任它时）
- `--dry-run`：只打印行动表，不执行（预览模式）

## 和视觉模型一起用

典型循环（任何 chat 模型都能做，不绑定厂商）：

1. `python pc.py shot screen.png`，把 `screen.png` 发给模型；
2. 模型说出「点哪里 / 输入什么」；
3. `python pc.py click X Y` 或 `python pc.py type "..."`；
4. 重复。

坐标换算：截图是物理像素，`pyautogui.size()` 返回的也是物理像素（Windows 缩放 150% 时两者一致），所以截图上的坐标可以直接用。

## 安全

- **急停**：同时按住鼠标左键和右键，脚本会立刻中止、不执行任何动作（动作前都会检查一次）。
- 每个动作之间默认 0.3s 间隔，避免连点失控。
- 不联网、不上传、不常驻。
- 建议只在你自己的机器上、清醒地看着它跑。

## License

MIT
