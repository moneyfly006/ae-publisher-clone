#!/usr/bin/env python3
"""文档 / 代码 / 测试 一致性检查。

这几轮加了很多文档和测试，容易漂移。用机器检查三件事：

1. `DOCUMENT-INDEX.md` 里的每个链接都真实存在
2. 索引里点名的测试文件都真实存在
3. 文档里引用的「测试项数」和实际测试函数数量一致（抽查几个）
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
INDEX = ROOT / "DOCUMENT-INDEX.md"


def t1_links_resolve():
    print("\n[测试1] 索引里的链接全部可达")
    text = INDEX.read_text(encoding="utf-8")
    broken = []
    for m in re.finditer(r"\]\(([^)]+)\)", text):
        target = m.group(1).split("#")[0].strip()
        if not target or target.startswith(("http://", "https://")):
            continue
        if not (ROOT / target).exists():
            broken.append(target)
    assert not broken, f"失效链接：{broken}"
    print(f"  ✓ {len(re.findall(r'\]\(([^)]+)', text))} 个链接全部有效")


def t2_test_files_exist():
    print("\n[测试2] 索引点名的测试文件都存在")
    text = INDEX.read_text(encoding="utf-8")
    named = set(re.findall(r"tests/(test_[a-z0-9_]+\.py)", text))
    missing = [n for n in sorted(named) if not (ROOT / "tests" / n).exists()]
    assert not missing, f"索引提到但文件不存在：{missing}"
    print(f"  ✓ {len(named)} 个测试文件都在")


def t3_all_tests_indexed():
    print("\n[测试3] 每个测试文件都被索引收录")
    text = INDEX.read_text(encoding="utf-8")
    files = {p.name for p in (ROOT / "tests").glob("test_*.py")}
    missing = sorted(f for f in files if f not in text)
    assert not missing, f"这些测试没写进索引：{missing}"
    print(f"  ✓ {len(files)} 个测试全部收录")


def t4_doc_counts_match():
    print("\n[测试4] 文档里写的「N 项」和实际用例数一致（抽查）")
    import ast
    checks = {
        "tests/test_preflight_config.py": 12,
        "tests/test_config_robustness.py": 8,
        "tests/test_switch_effectiveness.py": 4,
        "tests/test_api_save.py": 20,
    }
    for rel, expected in checks.items():
        src = (ROOT / rel).read_text(encoding="utf-8")
        tree = ast.parse(src)
        n = sum(1 for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name.startswith("t"))
        assert n == expected, f"{rel}：文档说 {expected} 项，实际定义 {n} 个"
    print(f"  ✓ 抽查 {len(checks)} 个文件一致")


def t5_no_empty_docs():
    print("\n[测试5] 索引里的文档不是空壳")
    text = INDEX.read_text(encoding="utf-8")
    docs = set(re.findall(r"\((docs/[A-Za-z0-9_\-]+\.md)\)", text))
    thin = []
    for d in sorted(docs):
        p = ROOT / d
        if p.exists() and len(p.read_text(encoding="utf-8").splitlines()) < 5:
            thin.append(d)
    assert not thin, f"这些文档几乎没有内容：{thin}"
    print(f"  ✓ {len(docs)} 个文档都有实质内容")


if __name__ == "__main__":
    tests = [t1_links_resolve, t2_test_files_exist, t3_all_tests_indexed,
             t4_doc_counts_match, t5_no_empty_docs]
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
