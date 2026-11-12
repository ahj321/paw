"""不碰鼠标键盘的那部分——纯函数和 CLI 参数解析。

这些测试在无头环境（CI）里也能跑：不调用任何系统 API。
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

import paw  # noqa: E402


def run_cli(*args):
    """跑一次真正的 CLI，验证参数解析和帮助文本没坏。"""
    return subprocess.run(
        [sys.executable, os.path.join(HERE, "paw.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def test_help_lists_core_commands():
    r = run_cli("--help")
    assert r.returncode == 0
    for cmd in ("size", "shot", "click", "type", "run", "scale"):
        assert cmd in r.stdout


def test_help_mentions_json_flag():
    r = run_cli("--help")
    assert "--json" in r.stdout


def test_plan_text_click_has_coords():
    plan = paw._plan_text("click", ["960", "540"])
    assert "960" in plan
    assert "540" in plan


def test_plan_text_move_has_coords():
    plan = paw._plan_text("move", ["100", "200"])
    assert "100" in plan and "200" in plan


def test_split_step_with_note():
    cmd, args, note = paw._split_step("click 10 20  # 点保存按钮")
    assert cmd == "click"
    assert args == ["10", "20"]
    assert note == "点保存按钮"


def test_split_step_plain():
    cmd, args, note = paw._split_step('type "hello world"')
    assert cmd == "type"
    assert args == ["hello world"]
    assert note == ""


def test_split_step_blank():
    cmd, args, note = paw._split_step("   ")
    assert cmd == ""
    assert args == []


def test_level_matrix():
    # browse 只能看
    assert paw._level_allows("browse", "size")
    assert paw._level_allows("browse", "move")
    assert not paw._level_allows("browse", "click")
    # safe 能点、能拖
    assert paw._level_allows("safe", "click")
    assert paw._level_allows("safe", "drag")
    # full 包含 safe 的所有权限
    assert paw._level_allows("full", "click")


def test_danger_hits_flags_format():
    assert paw._danger_hits("format disk")
    assert paw._danger_hits("click 100 200") == []


def test_read_steps_skips_comments_and_blanks(tmp_path):
    p = tmp_path / "steps.txt"
    p.write_text("# 这是注释\nclick 1 2  # 备注\n\ntype \"hi\"\n", encoding="utf-8")
    steps = paw._read_steps(str(p))
    assert len(steps) == 2
    assert steps[0][1] == "click"
    assert steps[0][3] == "备注"
    assert steps[1][1] == "type"


def test_all_commands_have_level():
    """每个命令都必须有权限等级，否则权限检查会 KeyError。"""
    for cmd in paw.ALL_CMDS:
        assert cmd in paw.CMD_LEVEL
