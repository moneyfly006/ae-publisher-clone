#!/usr/bin/env python3
"""构建脚本与打包配置的一致性检查。

本机是 macOS，跑不了 Windows 构建，所以对 Windows 脚本做静态检查：
关键步骤都在、路径都对、产物名对、构建后确实跑了自检。

同时校验 macOS 脚本（这个能真跑，见 docs/BUILD.md 里的实测记录）。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
MAC = ROOT / "tools" / "build_macos.sh"
WIN = ROOT / "tools" / "build_windows.ps1"
SPEC = ROOT / "app.spec"


def t1_scripts_exist():
    print("\n[测试1] 两个构建脚本都存在")
    for p in (MAC, WIN):
        assert p.exists(), f"缺少 {p}"
        assert p.stat().st_size > 500, f"{p.name} 内容过少"
    print(f"  ✓ {MAC.name} / {WIN.name}")


def t2_use_same_spec():
    print("\n[测试2] 两个脚本都用同一个 app.spec")
    assert "app.spec" in MAC.read_text()
    assert "app.spec" in WIN.read_text()
    assert SPEC.exists()
    print("  ✓ 都指向 app.spec")


def t3_key_steps_present():
    print("\n[测试3] 关键步骤齐全（依赖检查 → 体检 → 打包 → 验证）")
    need = ["test_packaging_spec.py", "--noconfirm", "requirements.txt"]
    mac, win = MAC.read_text(), WIN.read_text()
    for n in need:
        assert n in mac, f"macOS 脚本缺少 {n}"
        assert n in win, f"Windows 脚本缺少 {n}"
    assert "--self-check" in mac and "--self-check" in win, "构建后必须跑自检"
    print("  ✓ 依赖/体检/打包/自检 都有")


def t4_product_paths():
    print("\n[测试4] 产物路径与 app.spec 一致")
    spec = SPEC.read_text()
    m = re.search(r'name="([^"]+)"', spec)
    assert m, "spec 里没有 name="
    name = m.group(1)
    assert name in MAC.read_text(), f"macOS 脚本没引用 {name}"
    assert name in WIN.read_text(), f"Windows 脚本没引用 {name}"
    assert f"{name}.exe" in WIN.read_text(), "Windows 脚本应检查 .exe 产物"
    print(f"  ✓ 产物名 {name}（Windows 为 {name}.exe）")


def t5_windows_console_note():
    print("\n[测试5] Windows 脚本提醒了「窗口程序看不到控制台」")
    win = WIN.read_text()
    assert "last-selfcheck.txt" in win, "应提示报告文件位置"
    assert "APPDATA" in win, "应给出 Windows 配置目录"
    print("  ✓ 提示了报告落盘位置（因为 console=False）")


def t6_spec_matches_scripts():
    print("\n[测试6] spec 是窗口程序 + 目录模式（与脚本假设一致）")
    spec = SPEC.read_text()
    assert "console=False" in spec, "应是窗口程序"
    assert "COLLECT(" in spec, "应是目录模式（onedir），脚本按目录找产物"
    print("  ✓ console=False + COLLECT（onedir）")


def t7_build_doc_exists():
    print("\n[测试7] 有构建文档")
    doc = ROOT / "docs" / "BUILD.md"
    assert doc.exists(), "缺少 docs/BUILD.md"
    text = doc.read_text(encoding="utf-8")
    for kw in ("build_macos.sh", "build_windows.ps1", "app.spec", "--self-check"):
        assert kw in text, f"BUILD.md 缺少 {kw}"
    print("  ✓ BUILD.md 提到两个脚本、spec、自检")


if __name__ == "__main__":
    tests = [t1_scripts_exist, t2_use_same_spec, t3_key_steps_present,
             t4_product_paths, t5_windows_console_note, t6_spec_matches_scripts,
             t7_build_doc_exists]
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
