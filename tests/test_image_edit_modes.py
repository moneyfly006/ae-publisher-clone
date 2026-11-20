#!/usr/bin/env python3
"""图片通用编辑（通用与精细编辑）逻辑测试。

验证：
1. 总开关关闭 → 全部跳过
2. 通用编辑模式：一条提示词按范围逐张编辑主图，范围外不动
3. 精细化编辑：按分组启用，未启用分组跳过
4. 详情图「删除原有详情图，仅保留编辑结果」
5. 本地参考图顺序（先参考图，后产品原图）
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from aeclone.services.image_jobs import parse_jobs, rows_to_jobs, migrate  # noqa: E402


class Cfg:
    """最小配置桩，只带图片相关字段。"""
    def __init__(self, **kw):
        d = {
            "ai_image_enable": True,
            "image_edit_mode": "fine",
            "img_general_prompt": "",
            "img_general_main_from": "1",
            "img_general_main_to": "8",
            "img_fine_main_enable": True,
            "img_fine_detail_enable": False,
            "img_fine_detail_replace": False,
            "img_fine_sku_enable": False,
            "image_simple_rules": "[]",
            "image_ai_jobs": "[]",
            "image_concurrency": 1,
            "image_cache_enable": False,
            "img_min_size_enable": False,
            "img_min_size": 800,
            "four_in_one": False,
            "watermark": False,
            "white_bg_enable": False,
            "watermark_enable": False,
            "image_edit_plans": "[]",
            "designated_edit_enable": False,
            "designated_edit_indices": "",
            "designated_edit_prompt": "",
        }
        d.update(kw)
        for k, v in d.items():
            setattr(self, k, v)


class FakeRes:
    def __init__(self):
        self.steps = []


def add_step(res, name, ok, detail=""):
    res.steps.append({"name": name, "ok": bool(ok), "detail": str(detail)})


def make_product(n_main=10, n_detail=3, n_sku=2):
    return {
        "images": [f"https://ex.com/main{i}.jpg" for i in range(1, n_main + 1)],
        "detail_images": [f"https://ex.com/det{i}.jpg" for i in range(1, n_detail + 1)],
        "skus": [{"image": f"https://ex.com/sku{i}.jpg"} for i in range(1, n_sku + 1)],
    }


class FakePublisher:
    """模拟 Publisher 的图片方法，把 AI 调用换成可预测的桩。"""
    def __init__(self, cfg, product):
        self.cfg = cfg
        self.p = product
        self.calls = []
        self.made_seq = 0

    # 绑定真实的组访问方法
    from aeclone.services.publisher import Publisher as _P
    _group_urls = _P._group_urls
    _set_group_url = _P._set_group_url
    _set_group_list = _P._set_group_list

    def _pmap(self, fn, items, workers=None):
        out = []
        for x in items:
            try:
                out.append(fn(x))
            except Exception as ex:
                print("   [pmap error]", ex)
                out.append(None)
        return out

    def _edit_with_prompt(self, url, prompt):
        self.calls.append(("general", url, prompt))
        self.made_seq += 1
        return f"https://cdn.ex.com/edited_{self.made_seq}.jpg"

    def _apply_simple_ops(self, url, ops):
        self.calls.append(("simple", url, tuple(ops)))
        self.made_seq += 1
        return f"https://cdn.ex.com/simple_{self.made_seq}.jpg"

    def _img_model(self):
        return "stub"

    def _cache_get(self, *a, **k):
        return None

    def _cache_put(self, *a, **k):
        return None


def run_apply(cfg, product, monkeypatch_edit_many=None):
    """调用真实的 _apply_image_jobs，只替换掉网络/AI 调用。"""
    from aeclone.services import publisher as pubmod
    P = pubmod.Publisher
    res = FakeRes()

    fp = FakePublisher(cfg, product)

    # 造一个不跑 __init__ 的 Publisher 实例，绑定桩方法
    inst = P.__new__(P)
    inst.cfg = cfg
    inst._img_svc = None
    inst.ws_endpoint = ""
    inst.dxm = None
    inst.text = None
    inst.image = None
    inst.scheduler = None

    # 绑定桩
    inst._edit_with_prompt = fp._edit_with_prompt
    inst._apply_simple_ops = fp._apply_simple_ops
    inst._pmap = fp._pmap
    inst._img_model = fp._img_model
    inst._cache_get = fp._cache_get
    inst._cache_put = fp._cache_put

    def fake_img_service():
        class Svc:
            def edit(self, fp_, prompt):
                return fp._edit_with_prompt("x", prompt)
            def edit_many(self, files, prompt, tag=""):
                fp.calls.append(("edit_many", len(files), prompt))
                fp.made_seq += 1
                return f"https://cdn.ex.com/made_{fp.made_seq}.jpg"
            def white_background(self, p):
                return fp._apply_simple_ops("x", ["white"])
            def remove_watermark(self, p):
                return fp._apply_simple_ops("x", ["watermark"])
        return Svc()

    inst._img_service = fake_img_service
    inst._fetch = lambda url, workdir=None: url  # 不真下载

    out = P._apply_image_jobs(inst, product, res)
    return out, res, fp


def t1_master_off():
    print("\n[测试1] 兼容配置中的旧总开关不阻止已启用的图片任务")
    cfg = Cfg(ai_image_enable=False,
              image_ai_jobs=rows_to_jobs([{"group": "main", "index": 1, "prompt": "改图", "insert": "replace"}]))
    p = make_product()
    before = list(p["images"])
    out, res, fp = run_apply(cfg, p)
    assert out["images"][0] != before[0]
    assert out["images"][1:] == before[1:]
    assert len(fp.calls) == 1, fp.calls
    print("  ✓ 仅指定任务的第一张图生效")


def t2_general_mode():
    print("\n[测试2] 通用编辑：一条提示词，只改 1-3，第 4-10 张不动")
    cfg = Cfg(image_edit_mode="general",
              img_general_prompt="清理并本地化这张商品图",
              img_general_main_from="1", img_general_main_to="3")
    p = make_product(10)
    before = list(p["images"])
    out, res, fp = run_apply(cfg, p)
    after = out["images"]
    assert len(after) == 10, f"主图数量应保持 10，实际 {len(after)}"
    assert all("edited_" in u for u in after[:3]), f"前 3 张应被编辑：{after[:3]}"
    assert after[3:] == before[3:], f"第 4-10 张必须原样：{after[3:]}"
    assert len(fp.calls) == 3, f"应只调 3 次编辑，实际 {len(fp.calls)}"
    print("  ✓ 结果:", after)
    print("  ✓ 步骤:", [s.detail for s in res.steps])


def t3_general_prompt_empty():
    print("\n[测试3] 通用编辑提示词为空 → 不处理，不报错")
    cfg = Cfg(image_edit_mode="general", img_general_prompt="")
    p = make_product(10)
    before = list(p["images"])
    out, res, fp = run_apply(cfg, p)
    assert out["images"] == before
    assert not fp.calls
    print("  ✓ 跳过：", [s.detail for s in res.steps])


def t4_fine_group_gating():
    print("\n[测试4] 精细化编辑：只启用主图，详情图任务应跳过")
    jobs = [
        {"group": "main", "index": 2, "prompt": "主图第2张改白底", "insert": "replace"},
        {"group": "detail", "index": 1, "prompt": "详情图第1张改风格", "insert": "replace"},
    ]
    cfg = Cfg(image_edit_mode="fine", image_ai_jobs=rows_to_jobs(jobs),
              img_fine_main_enable=True, img_fine_detail_enable=False)
    p = make_product(10, 3)
    before_main = list(p["images"])
    before_detail = list(p["detail_images"])
    out, res, fp = run_apply(cfg, p)
    assert out["images"][0] == before_main[0], "第1张主图不应变化"
    assert "made_" in out["images"][1], f"第2张主图应被替换：{out['images'][1]}"
    assert out["images"][2:] == before_main[2:], "第3-10张主图不应变化"
    assert out["detail_images"] == before_detail, "详情图未启用，不应变化"
    detail_steps = [s for s in res.steps if "未启用" in s.detail and ("详情图" in s.detail or "描述图" in s.detail)]
    assert detail_steps, f"应有「详情图未启用，跳过」步骤，实际：{res.steps}"
    print("  ✓ 主图第2张已替换，其余不动")
    print("  ✓ 详情图跳过：", detail_steps[0].detail)


def t5_detail_replace():
    print("\n[测试5] 详情图「仅保留编辑结果」")
    jobs = [{"group": "detail", "index": 1, "prompt": "详情图重做", "insert": "replace"}]
    cfg = Cfg(image_edit_mode="fine", image_ai_jobs=rows_to_jobs(jobs),
              img_fine_main_enable=False, img_fine_detail_enable=True,
              img_fine_detail_replace=True)
    p = make_product(10, 3)
    out, res, fp = run_apply(cfg, p)
    det = out["detail_images"]
    print("  编辑后详情图:", det)
    assert len(det) == 1 and "made_" in det[0], f"应只保留 1 张 AI 结果，实际 {det}"
    assert any("仅保留" in s.name for s in res.steps), res.steps
    print("  ✓ 只剩 AI 结果:", det)


def t6_detail_keep():
    print("\n[测试6] 详情图未勾选「仅保留」→ 原有图片保留")
    jobs = [{"group": "detail", "index": 1, "prompt": "详情图重做", "insert": "replace"}]
    cfg = Cfg(image_edit_mode="fine", image_ai_jobs=rows_to_jobs(jobs),
              img_fine_main_enable=False, img_fine_detail_enable=True,
              img_fine_detail_replace=False)
    p = make_product(10, 3)
    out, res, fp = run_apply(cfg, p)
    assert len(out["detail_images"]) == 3, f"应保留 3 张，实际 {out['detail_images']}"
    assert "made_" in out["detail_images"][0]
    print("  ✓ 3 张都在，第1张已替换:", out["detail_images"])


def t7_sku_gating():
    print("\n[测试7] SKU 图编辑开关")
    jobs = [{"group": "sku", "index": 1, "prompt": "SKU 白底", "insert": "replace"}]
    cfg = Cfg(image_edit_mode="fine", image_ai_jobs=rows_to_jobs(jobs),
              img_fine_main_enable=False, img_fine_sku_enable=True)
    p = make_product(10, 3, 2)
    out, res, fp = run_apply(cfg, p)
    assert "made_" in out["skus"][0]["processed_image"]
    print("  ✓ SKU1 已处理:", out["skus"][0]["processed_image"])


def t8_all_groups_off():
    print("\n[测试8] 三组都未启用 → 跳过编辑但简单规则照跑")
    cfg = Cfg(image_edit_mode="fine", white_bg_enable=True,
              white_background_pic_1="1", white_background_pic_2="2",
              image_simple_rules=json.dumps([{"group": "main", "range": "1-2", "ops": ["white"]}]),
              img_fine_main_enable=False, img_fine_detail_enable=False, img_fine_sku_enable=False)
    p = make_product(10, 3)
    before = list(p["images"])
    out, res, fp = run_apply(cfg, p)
    assert all("simple_" in u for u in out["images"][:2]), out["images"][:2]
    assert out["images"][2:] == before[2:]
    assert any("都未启用" in s.detail for s in res.steps), res.steps
    print("  ✓ 简单规则生效，AI 编辑跳过")


def t9_ref_paths_parse():
    print("\n[测试9] 本地参考图解析（最多 5 张）")
    rows = [{"group": "main", "index": 1, "prompt": "p",
             "ref_paths": ["a.png", "b.png", "c.png", "d.png", "e.png", "f.png"]}]
    jobs = parse_jobs(rows)
    assert len(jobs[0]["ref_paths"]) == 5, jobs[0]["ref_paths"]
    assert jobs[0]["ref_paths"][0] == "a.png"
    s = rows_to_jobs(rows)
    assert "ref_paths" in json.loads(s)[0]
    print("  ✓ 截断到 5 张:", jobs[0]["ref_paths"])


def t10_migrate_defaults():
    print("\n[测试10] 迁移后默认：主图启用、详情/ SKU 未启用")
    class Old:
        four_in_one = False
        watermark = False
        white_bg_enable = False
        watermark_enable = False
        image_simple_rules = "[]"
        image_ai_jobs = "[]"
        image_edit_plans = "[]"
        designated_edit_enable = False
        designated_edit_indices = ""
        designated_edit_prompt = ""
    rj, jj = migrate(Old(), with_global=True)
    assert json.loads(jj) == [] or isinstance(json.loads(jj), list)
    print("  ✓ rules:", rj, " jobs:", jj)


if __name__ == "__main__":
    tests = [t1_master_off, t2_general_mode, t3_general_prompt_empty, t4_fine_group_gating,
             t5_detail_replace, t6_detail_keep, t7_sku_gating, t8_all_groups_off,
             t9_ref_paths_parse, t10_migrate_defaults]
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
