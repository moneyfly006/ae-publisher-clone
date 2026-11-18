#!/usr/bin/env python3
"""打包配置体检（不执行构建，几秒出结果）。

检查 app.spec 是否覆盖了新代码真正依赖的东西：
- aeclone 全部子模块（collect_submodules，新增模块不会漏）
- flet / flet_desktop 桌面端（否则 Windows 下不弹窗）
- flet 桌面端 / 依赖收集
- assets 目录存在
- 入口能 import（main.py 指向的 run）

跑完这个再跑 `pyinstaller app.spec` 才值得等。
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SPEC = ROOT / "app.spec"


def _spec_text() -> str:
    assert SPEC.exists(), f"缺少 {SPEC}"
    return SPEC.read_text(encoding="utf-8")


def t1_entry_exists():
    print("\n[测试1] 入口文件与 run 函数")
    main = ROOT / "main.py"
    assert main.exists(), "缺少 main.py"
    text = main.read_text(encoding="utf-8")
    assert "aeclone.ui.app" in text and "run" in text, "main.py 未指向 aeclone.ui.app.run"
    sys.path.insert(0, str(ROOT / "src"))
    from aeclone.ui.app import run  # noqa: F401
    assert callable(run)
    print("  ✓ main.py → aeclone.ui.app.run 可导入")


def t2_spec_covers_packages():
    print("\n[测试2] spec 覆盖关键第三方包")
    s = _spec_text()
    for pkg in ("flet", "flet_desktop"):
        assert f'"{pkg}"' in s, f"spec 未收集 {pkg}"
    assert "collect_all" in s, "应使用 collect_all 收集包数据/二进制"
    print("  ✓ flet / flet_desktop 均收集")


def t3_hidden_imports():
    print("\n[测试3] 关键 hiddenimports")
    s = _spec_text()
    need = ["browser_cookie3", "Crypto"]
    missing = [x for x in need if x not in s]
    assert not missing, f"缺少 hiddenimports：{missing}"
    print("  ✓", ", ".join(need))


def t4_aeclone_submodules():
    print("\n[测试4] aeclone 全部子模块会被打包")
    s = _spec_text()
    assert 'collect_submodules("aeclone")' in s, "应 collect_submodules('aeclone')"
    # 抽查本轮新增的模块是否真的在包内（能被 import）
    sys.path.insert(0, str(ROOT / "src"))
    import importlib
    for mod in ("aeclone.services.image_validation",
                "aeclone.services.image_jobs",
                "aeclone.services.publisher"):
        importlib.import_module(mod)
    print("  ✓ collect_submodules 覆盖，抽查 4 个新/改动模块可导入")


def t5_assets_present():
    print("\n[测试5] assets 目录")
    s = _spec_text()
    assert '"assets"' in s or "'assets'" in s, "spec 未打包 assets"
    assert (ROOT / "assets").is_dir(), "assets 目录不存在"
    n = len(list((ROOT / "assets").rglob("*")))
    print(f"  ✓ assets 存在（{n} 个文件条目）")


def t6_excludes_sane():
    print("\n[测试6] excludes 不会砍掉运行时依赖")
    s = _spec_text()
    tree = ast.parse(s)
    excl = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Analysis":
            for kw in node.keywords:
                if kw.arg == "excludes":
                    excl = ast.literal_eval(kw.value)
    assert excl is not None, "未找到 excludes"
    must_stay = {"PIL", "requests", "flet"}
    bad = must_stay & set(excl)
    assert not bad, f"excludes 里不该有：{bad}"
    print(f"  ✓ excludes = {excl}")


def t7_spec_parses():
    print("\n[测试7] spec 语法可解析（不是坏文件）")
    ast.parse(_spec_text())
    print("  ✓ 语法正确")


if __name__ == "__main__":
    tests = [t1_entry_exists, t2_spec_covers_packages, t3_hidden_imports,
             t4_aeclone_submodules, t5_assets_present, t6_excludes_sane, t7_spec_parses]
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
