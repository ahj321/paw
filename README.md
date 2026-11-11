# ai-pc-control

给「能看图的模型 + 能执行命令的 agent」当手：一条命令控制鼠标、键盘、窗口和虚拟桌面。

> 本项目由 deepseek-V4.1-Flash-1017 生成。

## 为什么写

同类的电脑控制工具多半要装插件、逐个开权限，配起来比用起来还长。我只想要一双能听命令的手，就写了这个：一个 `pc.py`，一条命令做一件事，代码量不大，看得懂也改得动。

工具本身离线可用；用云端模型下命令时，那一环需要联网。想整条链路都在本机，就得换本地视觉模型，更吃硬件、也更慢。

## 安装

```bash
pip install pyautogui pillow
pip install pygetwindow   # 可选：列窗口
pip install pyvda         # 可选：虚拟桌面
```

Windows / macOS / Linux（X11）都能跑，Windows 上中文输入走剪贴板。

## 用法

```bash
python pc.py size                        # 屏幕分辨率
python pc.py shot screen.png             # 截屏
python pc.py click 960 540               # 左键单击
python pc.py dclick 960 540              # 双击
python pc.py rclick 960 540              # 右键
python pc.py move 100 200                # 只移动
python pc.py type "hello world"          # 输入英文
python pc.py type "你好呀"               # 输入中文
python pc.py key enter                   # 按一个键
python pc.py hotkey ctrl s               # 组合键
python pc.py scroll -3                   # 向下滚 3 格
python pc.py find button.png 0.9         # 找图，返回坐标
python pc.py windows                     # 窗口和位置
python pc.py screens                     # 每块桌面开着什么、谁在前台
python pc.py watch 2                     # 实时：现在在干什么
python pc.py desks                       # 虚拟桌面列表
python pc.py desk new                    # 新建桌面
python pc.py desk switch 2               # 切桌面
python pc.py desk close                  # 关闭当前桌面
python pc.py click 960 540 --delay 2     # 动作前等 2 秒
python pc.py run steps.txt               # 跑一串
python pc.py run steps.txt --parallel    # 并行跑打开/查看类步骤
python pc.py run -                       # 从标准输入读
```

`steps.txt` 每行一条，支持引号与 `#` 注释：

```
launch notepad
click 300 200
type "hello"
hotkey ctrl s
```

## 权限等级

用 `--level`（或环境变量 `PC_LEVEL`）选，默认 `safe`：

| 等级 | 允许 |
| --- | --- |
| `browse` | 查看与打开：size / shot / windows / find / launch / move |
| `safe` | 再加点击、滚动、打字、按键；命中「付款、注册注销、删改东西」这类词时拒绝 |
| `full` | 全部 |

```bash
python pc.py launch https://bilibili.com --level browse
python pc.py type "确认付款" --level safe   # 被拦截
python pc.py type "确认付款" --level full   # 放行
```

`safe` 按命令与文字关键词判断，只能挡住一部分常见误操作——变体写法、点到几步之后的确认页、业务页面里才看得出的危险操作，都可能拦不住。

## 行动表

执行前先打印一行「计划：…」：

```
计划：在 (960, 540) 单击鼠标左键
```

`--no-plan` 关掉，`--dry-run` 只看计划不执行。

## 本地看板（可选）

```bash
python serve.py            # http://127.0.0.1:8000
python serve.py 8765       # 换端口
python serve.py --host 0.0.0.0   # 让局域网能访问
```

目前只有命令行和这个看板两种形态：给 agent 用不需要界面，想盯着看时起 `serve.py`，它和命令行共用同一套动作与权限。常驻、托盘这类形态以后可能加。

页面上有每块桌面的窗口、前台窗口、鼠标位置、空闲秒数；一个动作输入框（同一套语法与权限）；权限切换；还有一个**急停**按钮，点下去后所有动作都被拒绝，再点一次解除。

## 和视觉模型一起用

1. `python pc.py shot screen.png`，把图发给模型；
2. 模型说点哪里、输入什么；
3. `python pc.py click X Y` 或 `python pc.py type "..."`；
4. 重复。

截图和 `pyautogui.size()` 都是物理像素，坐标可以直接用。

## 风险

- 它会真的动你的鼠标和键盘：怎么用、用在哪儿由你决定，后果也由你承担（MIT，作者不提供担保）。
- 权限分级、危险词拦截、急停只能挡住部分误操作，不是安全保证。
- 建议在自己有权操作的机器上、看得见屏幕时运行；支付、账号、删改这类操作请人工完成。

## 维护

不定期更新。有 bug、想加功能，或文档哪里写得不清楚，欢迎开 issue 或提 PR。

## License

MIT

---

版本 **v1.0**，更新记录见 [CHANGELOG.md](CHANGELOG.md)。
