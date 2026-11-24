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
        self.img_fine_main_enable = False
        self.img_fine_detail_enable = False
        self.img_fine_sku_enable = False
        self.image_ai_jobs = "[]"
        self.image_simple_rules = "[]"
        for k, v in kw.items():
            setattr(self, k, v)

    def set(self, key, value):      # 模拟 AppConfig.set
        setattr(self, key, value)


def t1_empty_generate_prompt_does_not_block():
    print("\n[测试1] 生成主图提示词为空 → 自动当作未启用，不提醒不阻断")
    import json
    cfg = Cfg(main_image_generate_enable=True, main_image_generate_prompt="",
              image_ai_jobs=json.dumps([{"group": "main", "index": 1, "prompt": "换成白底"}]))
    plan = build_plan(cfg, {"images": ["a"] * 6, "detail_images": []})
    assert not plan.blockers, f"不应有阻断：{plan.blockers}"
    assert not any("生成主图" in s for s in plan.steps), plan.steps
    assert any("精细化编辑" in s for s in plan.steps), plan.steps
    assert getattr(cfg, "main_image_generate_enable") is False, "应被自动关闭"
    print("  ✓ 自动关闭，只执行精细化编辑：", plan.steps)


def t2_conflict_generate_vs_edit():
    print("\n[测试2] 生成主图 + 主图精细化编辑 → 检出 high 冲突")
    import json
    cfg = Cfg(main_image_generate_enable=True,
              main_image_generate_prompt="生成专业主图",
              img_fine_main_enable=True,
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
    cfg = Cfg(img_fine_main_enable=True,
              image_ai_jobs='[{"group":"main","index":1,"prompt":"白底"}]')
    assert detect_conflicts(cfg) == [], detect_conflicts(cfg)
    print("  ✓ 无冲突")


def t5_empty_prompt_jobs_warned():
    print("\n[测试5] 精细化编辑里提示词为空的条目 → 提示但不停")
    import json
    cfg = Cfg(img_fine_main_enable=True,
              image_ai_jobs=json.dumps([{"group": "main", "index": 1, "prompt": ""}]))
    cf = detect_conflicts(cfg)
    assert any("没有提示词" in c["title"] for c in cf), cf
    plan = build_plan(cfg, {"images": ["a"] * 3, "detail_images": []})
    assert not plan.blockers, plan.blockers
    print("  ✓ 提示 + 不阻断")


def t6_four_in_one_not_ai_job():
    print("\n[测试6] 四合一不再被迁移成 AI 任务（应走本地拼图）")
    from aeclone.services.image_jobs import migrate, parse_jobs
    cfg = Cfg(four_in_one=True, image_simple_rules="[]", image_ai_jobs="[]")
    rj, jj = migrate(cfg, with_global=True)
    jobs = parse_jobs(jj)
    assert not any(j.get("group") == "main" for j in jobs), f"不应生成主图 AI 任务: {jobs}"
    plan = build_plan(cfg, {"images": ["a"] * 6, "detail_images": []})
    assert any("四合一" in s for s in plan.steps), plan.steps
    print("  ✓ 迁移后无 AI 任务，计划里是本地四合一")


def t7_disabled_group_not_conflict():
    print("\n[测试7] 任务存在但分组未勾选 → 不算冲突")
    import json
    cfg = Cfg(main_image_generate_enable=True, main_image_generate_prompt="生成主图",
              img_fine_main_enable=False,
              image_ai_jobs=json.dumps([{"group": "main", "index": 1, "prompt": "改图"}]))
    cf = detect_conflicts(cfg)
    assert not any("生成主图" in c["title"] and "精细化" in c["title"] for c in cf), cf
    print("  ✓ 未勾选分组不产生冲突")


def t8_video_source_modes():
    print("\n[测试8] 视频来源 4 种模式都能识别")
    import aeclone.services.publisher as P
    from pathlib import Path as _P
    import tempfile
    # 采集视频：直接保留，不上传
    cfg = Cfg(video_source="collected")
    class _D:
        def _get(self, *a, **k): return {}
    pub = object.__new__(P.Publisher)
    pub.cfg = cfg
    pub.dxm = _D()
    res = P.PublishResult(product_id="x", ok=True)
    pub._prepare_video({"images": ["a"]}, res, {}, dry_run=False)
    names = [s.name for s in res.steps]
    assert "视频" in names, names
    assert any("采集" in s.detail for s in res.steps), res.steps
    # 不要视频：csp_video_id 置空
    cfg2 = Cfg(video_source="none")
    pub2 = object.__new__(P.Publisher); pub2.cfg = cfg2; pub2.dxm = _D()
    pp = {"images": ["a"], "csp_video_id": "123"}
    res2 = P.PublishResult(product_id="x", ok=True)
    pub2._prepare_video(pp, res2, {}, dry_run=False)
    assert pp.get("csp_video_id") == "", pp
    print("  ✓ collected / none 行为正确")


def t9_image_edit_fallback_on_524():
    print("\n[测试9] 图像编辑 524/超时 → 自动降级到 turbo 模型")
    from aeclone.services import ImageService, FALLBACK_IMAGE_MODEL

    class FakeClient:
        def __init__(self):
            self.calls = []
        def images_edit(self, model, prompt, files):
            self.calls.append(model)
            if model != FALLBACK_IMAGE_MODEL:
                raise RuntimeError("HTTP 524")
            return "https://ok/fb.png"

    svc = object.__new__(ImageService)
    svc.cfg = type("C", (), {"prefer_dianxiaomi_album": False, "image_retry_times": 0})()
    svc.client = FakeClient()
    svc._STABLE_HOSTS = ("moneyfly",)
    out = svc._img_edit("p", [], "qwen-image-3.0")
    assert svc.client.calls == ["qwen-image-3.0", FALLBACK_IMAGE_MODEL], svc.client.calls
    assert out == "https://ok/fb.png"
    print("  ✓ 调用序列:", svc.client.calls)


def t10_publish_retry_no_ai():
    print("\n[测试10] 「只重试发布」不碰 AI，只调 add.json op=2")
    import aeclone.services.publisher as P

    calls = {"count": 0, "op": None}

    class FakeDxm:
        def _get(self, ep, params):
            return {"data": {"product": {"id": params["id"], "categoryId": "100007204",
                                         "detail": "<p>d</p>",
                                         "aeopAeProductSKUs": '[{"skuCode":"S1","originalBox":"1"}]'}}}

        def save_product_via_api(self, payload):
            calls["count"] += 1
            calls["op"] = payload.get("op")
            return {"code": 0, "data": {"msg": "ok", "productId": payload.get("id")}}

    pub = object.__new__(P.Publisher)
    pub.cfg = type("C", (), {"template_product_id": "", "declared_value": "50",
                             "custom_attributes": []})()
    pub.dxm = FakeDxm()
    res = P.PublishResult(product_id="1", ok=True)
    out = pub.publish_only("1", res)
    assert out.ok, out
    assert calls["count"] == 1, calls
    assert calls["op"] == 2, calls
    assert ("发布已确认" in out.message or "只发布成功" in out.message)
    print("  ✓ 只发 1 次请求，op=2，无 AI 调用")

if __name__ == "__main__":
    tests = [t1_empty_generate_prompt_does_not_block, t2_conflict_generate_vs_edit,
             t3_conflict_watermark_contradiction, t4_clean_config_no_conflict,
             t5_empty_prompt_jobs_warned, t6_four_in_one_not_ai_job,
             t7_disabled_group_not_conflict, t8_video_source_modes,
             t9_image_edit_fallback_on_524, t10_publish_retry_no_ai]
    passed = failed = 0
    for t in tests:
        try:
            t(); passed += 1
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ✗ {t.__name__}: {e}")
    print(f"\n{'='*50}\n结果: {passed} 通过, {failed} 失败")
    sys.exit(1 if failed else 0)
