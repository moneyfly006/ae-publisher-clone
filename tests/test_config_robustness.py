#!/usr/bin/env python3
"""配置健壮性测试：损坏的 config.json 不能把 API Key 悄悄弄丢。

背景：用户报告过「填了 Key，第二天变成 Invalid token」。
`AppConfig.load()` 原来的行为是：

    解析失败 → 试 config.bak → 还失败就返回默认配置

返回默认配置本身没问题，问题在于**随后任何一次 save() 都会用默认值覆盖掉
那个损坏但可能还能抢救的文件**（里面的 API Key 就永久没了）。

现在：解析失败先把原文件另存为 config.corrupt-<时间>.json，再回退。
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def _fresh_config_module(tmp: Path):
    """在临时目录里重新导入 config 模块，避免碰真实配置。"""
    os.environ["XDG_CONFIG_HOME"] = str(tmp)
    os.environ["HOME"] = str(tmp)
    for m in list(sys.modules):
        if m == "aeclone.config" or m.startswith("aeclone.config."):
            del sys.modules[m]
    import importlib
    import aeclone.config as C
    importlib.reload(C)
    return C


def t1_roundtrip_preserves_key():
    print("\n[测试1] 正常读写往返：API Key 不丢")
    with tempfile.TemporaryDirectory() as td:
        C = _fresh_config_module(Path(td))
        cfg = C.AppConfig()
        cfg.set("api_key", "sk-roundtrip-123")
        cfg.set("store_id", "shop-1")
        cfg.save()
        again = C.AppConfig.load()
        assert again.api_key == "sk-roundtrip-123", again.api_key
        assert again.store_id == "shop-1"
        print("  ✓ key / store 都保留")


def t2_corrupt_keeps_copy():
    print("\n[测试2] config.json 损坏 → 先另存 corrupt 副本")
    with tempfile.TemporaryDirectory() as td:
        C = _fresh_config_module(Path(td))
        p = C.AppConfig.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"api_key": "sk-important", BROKEN', encoding="utf-8")
        cfg = C.AppConfig.load()
        assert C.AppConfig.loaded_from in ("defaults", "bak"), C.AppConfig.loaded_from
        corrupts = list(p.parent.glob("config.corrupt-*.json"))
        assert corrupts, "应该生成 config.corrupt-*.json"
        assert "sk-important" in corrupts[0].read_text(encoding="utf-8"), "副本里要保留原文"
        print(f"  ✓ 副本 {corrupts[0].name} 保留了原文（含 key）")
        # 关键：现在保存默认配置也不会毁掉副本
        cfg.save()
        assert "sk-important" in corrupts[0].read_text(encoding="utf-8")
        print("  ✓ 保存默认配置后副本仍在")


def t3_falls_back_to_bak():
    print("\n[测试3] 主配置坏、备份好 → 用备份恢复")
    with tempfile.TemporaryDirectory() as td:
        C = _fresh_config_module(Path(td))
        p = C.AppConfig.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        good = C.AppConfig()
        good.set("api_key", "sk-from-bak")
        p.with_suffix(".bak").write_text(json.dumps(good.as_dict(), ensure_ascii=False), encoding="utf-8")
        p.write_text("not json at all", encoding="utf-8")
        cfg = C.AppConfig.load()
        assert cfg.api_key == "sk-from-bak", cfg.api_key
        assert C.AppConfig.loaded_from == "bak", C.AppConfig.loaded_from
        print("  ✓ 从 .bak 恢复，key 保留")


def t4_no_file_is_new():
    print("\n[测试4] 首次运行没有配置文件 → 标记 new，不报错")
    with tempfile.TemporaryDirectory() as td:
        C = _fresh_config_module(Path(td))
        cfg = C.AppConfig.load()
        assert C.AppConfig.loaded_from == "new"
        assert cfg is not None
        print("  ✓ loaded_from=", C.AppConfig.loaded_from)


def t5_both_broken_defaults():
    print("\n[测试5] 主配置和备份都坏 → 用默认值，但仍留副本")
    with tempfile.TemporaryDirectory() as td:
        C = _fresh_config_module(Path(td))
        p = C.AppConfig.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{bad", encoding="utf-8")
        p.with_suffix(".bak").write_text("{also bad", encoding="utf-8")
        cfg = C.AppConfig.load()
        assert C.AppConfig.loaded_from == "defaults"
        assert list(p.parent.glob("config.corrupt-*.json"))
        assert cfg.default_country == C.DEFAULTS["default_country"] if hasattr(cfg, "default_country") else True
        print("  ✓ 回退默认值 + 保留损坏副本")


def t6_atomic_write():
    print("\n[测试6] 保存是原子写（先 tmp 再替换）")
    with tempfile.TemporaryDirectory() as td:
        C = _fresh_config_module(Path(td))
        cfg = C.AppConfig()
        cfg.set("api_key", "sk-atomic")
        cfg.save()
        p = C.AppConfig.path()
        assert p.exists()
        assert not p.with_suffix(".tmp").exists(), "临时文件应已 rename"
        assert json.loads(p.read_text(encoding="utf-8"))["api_key"] == "sk-atomic"
        print("  ✓ 无残留 .tmp，内容正确")


def t7_preflight_warns_on_recovery():
    print("\n[测试7] 自检会提示「配置从备份恢复 / 已回退默认」")
    from aeclone.services import preflight as pf

    class C:
        api_key = "sk-x"
        _loaded_from = "bak"

    t = [i.text for i in pf.check(C())]
    assert any("config.bak" in x for x in t), t
    print("  ✓", [x for x in t if "bak" in x][0])

    class C2:
        api_key = ""
        _loaded_from = "defaults"

    t2 = [i.text for i in pf.check(C2())]
    assert any("损坏" in x and "默认值" in x for x in t2), t2
    print("  ✓", [x for x in t2 if "损坏" in x][0])


def t8_no_warning_on_normal_load():
    print("\n[测试8] 正常加载时不打扰")
    from aeclone.services import preflight as pf

    class C:
        api_key = "sk-x"
        _loaded_from = "file"

    t = [i.text for i in pf.check(C())]
    assert not any("config.bak" in x or "损坏" in x for x in t), t
    print("  ✓ 无多余提示")


if __name__ == "__main__":
    tests = [t1_roundtrip_preserves_key, t2_corrupt_keeps_copy, t3_falls_back_to_bak,
             t4_no_file_is_new, t5_both_broken_defaults, t6_atomic_write,
             t7_preflight_warns_on_recovery, t8_no_warning_on_normal_load]
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
