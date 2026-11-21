#!/usr/bin/env python3
"""启动前自检测试。

覆盖「静默失效」这一类问题：不报错、也不做事。
- 选了通用编辑但提示词空
- 精细化有任务但三组都没启用
- 白底范围写反/写 0
- 开了 AI 但没 Key
- 多 SKU 会被 SKU 编码写回挡住
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aeclone.services import preflight as pf  # noqa: E402


class Cfg:
    def __init__(self, **kw):
        base = dict(
            api_key="sk-x", auto_save_products=True, max_sku_count=256,
            ai_image_enable=True, image_edit_mode="fine",
            img_general_prompt="", img_general_main_from="1", img_general_main_to="8",
            img_fine_main_enable=True, img_fine_detail_enable=False, img_fine_sku_enable=False,
            img_fine_detail_replace=False, img_min_size_enable=True, img_min_size=800,
            img_min_size_drop_invalid=False,
            image_simple_rules="[]", image_ai_jobs="[]",
            white_bg_enable=False, white_background_pic_1="", white_background_pic_2="",
            watermark_enable=False, remove_watermark_pic_1="", remove_watermark_pic_2="",
            img_detail_white_bg=False, img_detail_remove_wm=False, white_background_sku_pic=False,
            translate_main_pic_enable=False, translate_detail_pic_enable=False, translate_sku_pic=False,
            enable_ai_title=False, enable_ai_detail=False, enable_ai_sku=False,
            review_enable=False, estimate_weight_enable=False,
            write_sku_codes_to_product=True,
            product_template_id="tpl-1", product_template_name="机械模板",
            template_product_id="", template_product_title="", template_snapshot="",
        )
        base.update(kw)
        for k, v in base.items():
            setattr(self, k, v)


def _texts(cfg):
    return [i.text for i in pf.check(cfg)]


def t1_general_prompt_empty():
    print("\n[测试1] 通用编辑 + 提示词为空 → error")
    cfg = Cfg(image_edit_mode="general", img_general_prompt="")
    t = _texts(cfg)
    assert any("通用编辑" in x and "空" in x for x in t), t
    print("  ✓", [x for x in t if "通用编辑" in x][0])


def t2_general_range_reversed():
    print("\n[测试2] 通用编辑范围写反 → error")
    cfg = Cfg(image_edit_mode="general", img_general_prompt="清理",
              img_general_main_from="8", img_general_main_to="3")
    t = _texts(cfg)
    assert any("写反" in x for x in t), t
    print("  ✓", [x for x in t if "写反" in x][0])


def t3_fine_all_groups_off():
    print("\n[测试3] 精细化有任务但三组都没启用 → error")
    jobs = [{"group": "main", "index": 1, "prompt": "x"},
            {"group": "detail", "index": 1, "prompt": "y"}]
    cfg = Cfg(image_ai_jobs=json.dumps(jobs),
              img_fine_main_enable=False, img_fine_detail_enable=False, img_fine_sku_enable=False)
    t = _texts(cfg)
    assert any("三组都没启用" in x for x in t), t
    print("  ✓", [x for x in t if "三组" in x][0])


def t4_fine_group_off_hint_in_plan():
    print("\n[测试4] 某组未启用 → 计划里写明「全部跳过」")
    jobs = [{"group": "detail", "index": 1, "prompt": "详情图重做"}]
    cfg = Cfg(image_ai_jobs=json.dumps(jobs), img_fine_detail_enable=False)
    lines = pf.image_plan_lines(cfg)
    assert any("未启用" in x and "跳过" in x for x in lines), lines
    print("  ✓", [x for x in lines if "未启用" in x][0])


def t5_white_bg_range_invalid():
    print("\n[测试5] 主图白底范围 0-x → warn")
    cfg = Cfg(white_bg_enable=True, white_background_pic_1="0", white_background_pic_2="3")
    t = _texts(cfg)
    assert any("白底范围不合法" in x for x in t), t
    print("  ✓", [x for x in t if "白底范围" in x][0])


def t6_api_key_missing():
    print("\n[测试6] 开 AI 但没 Key → error")
    cfg = Cfg(api_key="", enable_ai_title=True)
    t = _texts(cfg)
    assert any("API Key" in x for x in t), t
    print("  ✓", [x for x in t if "API Key" in x][0])


def t7_no_ai_no_key_ok():
    print("\n[测试7] 全部 AI 关 + 没 Key → 不报 Key 错")
    cfg = Cfg(api_key="", ai_image_enable=False)
    t = _texts(cfg)
    assert not any("API Key" in x for x in t), t
    print("  ✓ 不提示 Key")


def t8_multi_sku_hint():
    print("\n[测试8] 多 SKU + 编码写回 → 提示按原始变种身份核验")
    cfg = Cfg(max_sku_count=256, write_sku_codes_to_product=True)
    t = _texts(cfg)
    assert any("原始变种身份" in x and "无法匹配时停止" in x for x in t), t
    print("  ✓", [x for x in t if "原始变种身份" in x][0])


def t9_plan_reads_like_chinese():
    print("\n[测试9] 计划文本是给人看的中文")
    jobs = [{"group": "main", "index": 1, "prompt": "四合一"},
            {"group": "main", "index": 5, "prompt": "换场景"}]
    cfg = Cfg(image_ai_jobs=json.dumps(jobs),
              white_bg_enable=True, white_background_pic_1="1", white_background_pic_2="5",
              enable_ai_title=True)
    txt = pf.summary(cfg)
    for kw in ("这次会做什么", "图片简单编辑", "主图白底 1-5", "精细化编辑·主图", "2 条", "AI 标题"):
        assert kw in txt, f"缺少 {kw}\n{txt}"
    print(txt.replace("\n", "\n  "))


def t10_blocking_only_errors():
    print("\n[测试10] blocking() 只返回 error 级")
    cfg = Cfg(image_edit_mode="general", img_general_prompt="",
              white_bg_enable=True, white_background_pic_1="0", white_background_pic_2="2")
    b = pf.blocking(cfg)
    assert b and all("通用编辑" in x for x in b), b
    print("  ✓ blocking =", b)


def t11_size_check_off_visible():
    print("\n[测试11] 关掉尺寸校验时计划里要写明")
    cfg = Cfg(img_min_size_enable=False)
    lines = pf.image_plan_lines(cfg)
    assert any("尺寸校验：已关闭" in x for x in lines), lines
    print("  ✓", [x for x in lines if "尺寸校验" in x][0])


def t12_no_template_warns():
    print("\n[测试12] 没选模板 → warn")
    cfg = Cfg(product_template_id="", product_template_name="")
    t = _texts(cfg)
    assert any("模板" in x for x in t), t
    print("  ✓", [x for x in t if "模板" in x][0])


if __name__ == "__main__":
    tests = [t1_general_prompt_empty, t2_general_range_reversed, t3_fine_all_groups_off,
             t4_fine_group_off_hint_in_plan, t5_white_bg_range_invalid, t6_api_key_missing,
             t7_no_ai_no_key_ok, t8_multi_sku_hint, t9_plan_reads_like_chinese,
             t10_blocking_only_errors, t11_size_check_off_visible, t12_no_template_warns]
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
