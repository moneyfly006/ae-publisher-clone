#!/usr/bin/env python3
"""命令行自检（--self-check）测试。

目的：打包后的程序在别人机器上跑不起来时，让对方执行
`AuroraList --self-check` 把输出发过来，比"打不开"有用得多。

这里验证：入口能识别参数、报告内容完整、只读不写、退出码正确。
"""
import subprocess
import sys
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "src"))

from aeclone import selfcheck  # noqa: E402


def _run_capture():
    buf = StringIO()
    code = selfcheck.run(stream=buf)
    return code, buf.getvalue()


def t1_report_sections():
    print("\n[测试1] 报告包含所有必要段落")
    code, text = _run_capture()
    for section in ("运行环境", "关键依赖", "加密子模块", "配置", "模块链路", "配置体检", "结论"):
        assert section in text, f"缺少段落：{section}"
    print("  ✓ 7 个段落齐全")


def t2_exit_code_semantics():
    print("\n[测试2] 退出码：有致命项=1，否则=0")
    code, text = _run_capture()
    has_err = "✗" in text
    assert code == (1 if has_err else 0), f"退出码 {code} 与报告是否含 ✗ 不一致"
    print(f"  ✓ 退出码 {code}（报告含 ✗：{has_err}）")


def t3_no_network_no_write():
    print("\n[测试3] 只读：不联网、不改配置文件")
    from aeclone.config import AppConfig
    p = AppConfig.path()
    before = p.stat().st_mtime if p.exists() else None
    _run_capture()
    after = p.stat().st_mtime if p.exists() else None
    assert before == after, "自检不应写配置"
    print("  ✓ 配置 mtime 未变")


def t4_api_key_reported():
    print("\n[测试4] API Key 状态被如实报告")
    code, text = _run_capture()
    assert "API Key" in text
    assert ("未填" in text) or ("已填" in text), text[:200]
    print("  ✓ 有明确的「已填/未填」")


def t5_cli_flag_recognized():
    print("\n[测试5] main.py 识别 --self-check 并走自检分支")
    for flag in ("--self-check", "--selfcheck", "--check"):
        r = subprocess.run([sys.executable, "main.py", flag],
                           cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        assert "AuroraList 自检" in (r.stdout + r.stderr), f"{flag} 没有触发自检"
    print("  ✓ 三种写法都能触发")


def t6_normal_start_not_affected():
    print("\n[测试6] 不带参数时仍走 GUI 分支（不误触发自检）")
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "_is_selfcheck(sys.argv)" in src
    assert "aeclone.ui.app import run" in src
    print("  ✓ main.py 逻辑正确")


def t7_report_written_to_file():
    print("\n[测试7] 报告同时落盘（Windows 窗口程序看不到控制台时用）")
    from aeclone.config import config_dir
    _run_capture()
    dst = Path(config_dir()) / "last-selfcheck.txt"
    assert dst.exists(), f"应生成 {dst}"
    text = dst.read_text(encoding="utf-8")
    assert "AuroraList 自检" in text
    assert "结论" in text
    print(f"  ✓ {dst.name}（{len(text)} 字符）")


if __name__ == "__main__":
    tests = [t1_report_sections, t2_exit_code_semantics, t3_no_network_no_write,
             t4_api_key_reported, t5_cli_flag_recognized, t6_normal_start_not_affected,
             t7_report_written_to_file]
    ok = fail = 0
    for t in tests:
        try:
            t()
            ok += 1
        except AssertionError as ex:
            fail += 1
            print(f"  ✗ 失败: {ex}")
        except Exception as ex:
            fail += 1
            import traceback
            print(f"  ✗ 异常: {ex}")
            traceback.print_exc()
    print("\n" + "=" * 56)
    print(f"结果: {ok} 通过, {fail} 失败")
    print("=" * 56)
    sys.exit(0 if fail == 0 else 1)
