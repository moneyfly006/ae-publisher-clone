#!/usr/bin/env python3
"""界面开关到底生不生效 —— 自动审计。

用户反复抱怨「这个开关有什么用」。与其逐个解释，不如让代码自己证明：
DEFAULTS 里定义的每个键，要么被运行时读过，要么在界面上明确标注「暂未实现」。

本测试：
1. 扫出 DEFAULTS 里所有键
2. 判断它在 src/aeclone 里是否被引用（引号键名 or 属性访问）
3. 断言「界面上出现但运行时零引用」的键，必须落在已知白名单里
   —— 白名单里的是明确标注过「暂未实现」的，新增的没标注就报错
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
SRC = ROOT / "src" / "aeclone"

# 界面上有、但运行时确实不读的键。这些已经在 UI 里标注「暂未实现」，
# 或者是纯界面状态（不参与刊登逻辑）。
KNOWN_UI_ONLY = {
    # 已标注「暂未实现」
    "original_box", "upload_main_video", 
    # 纯界面/账号状态
    "license_server", "license_enforce", "license_identity", "license_cache_ok",
    "license_cache_expiry", "license_cache_ts", "license_public_key", "api_purchase_url",
    "log_auto_follow", "template_image_url",
    "window_size", "ui_theme",
    "store_name", "store_id", "manual_cookies", "endpoint_overrides", "start_page",
    "edit_sum", "template_source", "template_library", "price_template_library",
    "adjust_price_template_id", "adjust_price_template_name", "attribute_template_id",
    
    # 由 UI 直接传给下游客户端（不在 publisher 里读）
    "concurrency", "timeout", "retry_max",
}

# 完全没人读的键：只要不被界面渲染出来就不影响用户
KNOWN_DEAD = {
    "auto_clean_dup_attributes", "modify_stock_999", "ai_image_enable",
}


def _defaults_keys() -> list[str]:
    lines = (SRC / "config.py").read_text(encoding="utf-8").splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith("DEFAULTS: dict = {"))
    keys = []
    for l in lines[start + 1:]:
        if l.startswith("}"):
            break
        m = re.match(r'\s+"([^"]+)"\s*:', l)
        if m:
            keys.append(m.group(1))
    return keys


def _refs(key: str) -> dict[str, int]:
    pat = re.compile(rf'(?:["\']{re.escape(key)}["\']|\.{re.escape(key)}\b)')
    out = {}
    for p in SRC.rglob("*.py"):
        if p.name == "config.py":
            continue
        t = p.read_text(encoding="utf-8", errors="ignore")
        n = len(pat.findall(t))
        if n:
            out[str(p.relative_to(SRC))] = n
    return out


def t1_defaults_scanned():
    print("\n[测试1] 能扫到全部 DEFAULTS 键")
    keys = _defaults_keys()
    assert len(keys) > 150, len(keys)
    print(f"  ✓ {len(keys)} 个键")


def t2_no_unlabelled_dead_switches():
    print("\n[测试2] 没有「界面上有、运行时零引用、又没标注」的开关")
    keys = _defaults_keys()
    offenders = []
    for k in keys:
        r = _refs(k)
        if not r:
            if k in KNOWN_DEAD:
                continue
            offenders.append((k, "完全没引用且不在白名单"))
            continue
        ui = sum(n for p, n in r.items() if p.startswith("ui/"))
        rt = sum(n for n, p in ((n, p) for p, n in r.items()) if not p.startswith("ui/"))
        if rt == 0 and ui > 0 and k not in KNOWN_UI_ONLY:
            offenders.append((k, f"只在 UI 出现 {ui} 次，运行时没读"))
    assert not offenders, "未标注的无效开关：" + ", ".join(f"{k}({why})" for k, why in offenders)
    print("  ✓ 全部有归属（有效 / 已标注暂未实现 / 纯界面状态）")


def t3_没有残留的暂未实现开关():
    """之前界面标了 4 个「暂未实现」的开关，已全部清理：
    delete_detail_text / split_product_price / variant_mappings / keep_custom_attributes。
    现在界面上不应再出现「暂未实现」标注 —— 出现的开关都必须真的生效。
    """
    print("\n[测试3] 界面上没有残留的「暂未实现」开关")
    ui = (SRC / "ui" / "app.py").read_text(encoding="utf-8")
    assert "以下旧功能暂未实现" not in ui, "还残留「暂未实现」提示块"
    # delete_detail_text 已真正实现（api_save.build_ai_overrides 会读），不再算死开关
    for k in ("split_product_price", "variant_mappings", "keep_custom_attributes"):
        assert f'"{k}"' not in ui, f"{k} 仍在界面出现"
    print("  ✓ 死开关已从界面移除")


def t4_implemented_switches_really_read():
    print("\n[测试4] 声称生效的开关，运行时确实读了")
    must_read = ["prefer_collected_weight", "keep_main_images_local", "four_in_one",
                 "main_as_detail", "custom_detail_text_enable",
                 "delete_detail_text"]
    bad = []
    for k in must_read:
        r = _refs(k)
        rt = [p for p in r if not p.startswith("ui/")]
        if not rt:
            bad.append(k)
    assert not bad, f"这些开关运行时没读：{bad}"
    print("  ✓", ", ".join(must_read))


if __name__ == "__main__":
    tests = [t1_defaults_scanned, t2_no_unlabelled_dead_switches,
             t3_没有残留的暂未实现开关, t4_implemented_switches_really_read]
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
