"""图片计划 / 开关冲突回归：空提示词不能阻断商品，冲突能被检出。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone.services.image_task_plan import build_plan, detect_conflicts  # noqa: E402


class Cfg:
    """最简配置桩。"""
    def __init__(self, **kw):
        self.ai_image_enable = True
        self.main_image_generate_enable = False
        self.main_image_generate_prompt = ""
        self.main_image_generate_use_refs = True
        self.main_image_generate_count = "1"
        self.white_bg_enable = False
        self.white_background_pic_1 = "1"
        self.white_background_pic_2 = "3"
        self.watermark_enable = False
        self.remove_watermark_pic_1 = "1"
        self.remove_watermark_pic_2 = "5"
        self.translate_main_pic_enable = False
        self.translate_main_pic_indices = ""
        self.designated_edit_enable = False
        self.designated_edit_indices = ""
        self.designated_edit_prompt = ""
        self.img_detail_white_bg = False
        self.img_detail_remove_wm = False
        self.img_detail_edit = False
        self.white_background_detail_pic_indices = ""
        self.translate_detail_pic_enable = False
        self.translate_detail_pic_indices = ""
        self.img_sku_white_bg = False
        self.img_sku_remove_wm = False
        self.img_sku_edit = False
        self.white_background_sku_pic = False
        self.translate_sku_pic = False
        self.background_gen_rules = ""
        self.four_in_one = False
        self.four_in_one_position = "append"
        self.image_edit_plans = ""
        self.overlay_watermark_enable = False
        self.watermark = ""
        self.img_main_edit = False
        self.image_ai_jobs = "[]"
        self.image_simple_rules = "[]"
        for k, v in kw.items():
            setattr(self, k, v)


def t1_empty_generate_prompt_does_not_block():
    print("\n[测试1] 生成主图提示词为空 → 只警告，不阻断")
    import json
    cfg = Cfg(main_image_generate_enable=True, main_image_generate_prompt="",
              image_ai_jobs=json.dumps([{"group": "main", "index": 1, "prompt": "换成白底"}]))
    plan = build_plan(cfg, {"images": ["a"] * 6, "detail_images": []})
    assert not plan.blockers, f"不应有阻断：{plan.blockers}"
    assert plan.warnings, "应有警告"
    assert any("精细化编辑" in s for s in plan.steps), plan.steps
    print("  ✓ 不阻断，警告：", plan.warnings[0])


def t2_conflict_generate_vs_edit():
    print("\n[测试2] 生成主图 + 主图精细化编辑 → 检出 high 冲突")
    import json
    cfg = Cfg(main_image_generate_enable=True,
              main_image_generate_prompt="生成专业主图",
              image_ai_jobs=json.dumps([{"group": "main", "index": 1, "prompt": "换背景"}]))
    cf = detect_conflicts(cfg)
    assert any(c["level"] == "high" for c in cf), cf
    assert any("生成主图" in c["title"] and "精细化" in c["title"] for c in cf), cf
    print("  ✓ 检出：", [c["title"] for c in cf])


def t3_conflict_watermark_contradiction():
    print("\n[测试3] 去水印 + 叠加水印 → 检出 high 冲突")
    cfg = Cfg(watermark_enable=True, overlay_watermark_enable=True, watermark="logo.png")
    cf = detect_conflicts(cfg)
    assert any(c["level"] == "high" and "水印" in c["title"] for c in cf), cf
    print("  ✓ 检出：", [c["title"] for c in cf])


def t4_clean_config_no_conflict():
    print("\n[测试4] 只开一个开关 → 无冲突")
    cfg = Cfg(image_ai_jobs='[{"group":"main","index":1,"prompt":"白底"}]')
    assert detect_conflicts(cfg) == [], detect_conflicts(cfg)
    print("  ✓ 无冲突")


def t5_empty_prompt_jobs_warned():
    print("\n[测试5] 精细化编辑里提示词为空的条目 → 提示但不停")
    import json
    cfg = Cfg(image_ai_jobs=json.dumps([{"group": "main", "index": 1, "prompt": ""}]))
    cf = detect_conflicts(cfg)
    assert any("没有提示词" in c["title"] for c in cf), cf
    plan = build_plan(cfg, {"images": ["a"] * 3, "detail_images": []})
    assert not plan.blockers, plan.blockers
    print("  ✓ 提示 + 不阻断")


if __name__ == "__main__":
    tests = [t1_empty_generate_prompt_does_not_block, t2_conflict_generate_vs_edit,
             t3_conflict_watermark_contradiction, t4_clean_config_no_conflict,
             t5_empty_prompt_jobs_warned]
    passed = failed = 0
    for t in tests:
        try:
            t(); passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ✗ {t.__name__}: {e}")
    print(f"\n{'='*50}\n结果: {passed} 通过, {failed} 失败")
    sys.exit(1 if failed else 0)
