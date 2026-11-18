#!/usr/bin/env python3
"""Publisher.run() 端到端离线测试（不联网、不碰店铺、不调用 AI）。

这是最接近真实的一层验证：跑的是真正的 `Publisher.run()`，
只把「外部世界」换成桩：

- 店小秘客户端（dxm）→ 只回状态，不保存
- 文本 AI → 返回固定字符串
- 图片 AI → 返回本地真实图片文件
- 编辑器自动化 → 记录「要被保存的内容」，不打开浏览器

因此能验证「配置 → 处理 → 结果」这条链是否真的接通，
包括单元测试发现不了的「某条分支压根没被调用」的问题
（例如：通用编辑靠 img_general_prompt 触发，rules/jobs 是空的）。
"""
import json
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from PIL import Image  # noqa: E402


def _mk(d: Path, name: str, w=1024, h=1024) -> Path:
    p = d / name
    Image.new("RGB", (w, h), "white").save(p)
    return p


class FakeDxm:
    """只提供 run() 需要的两个方法。"""

    def __init__(self, state="editingRequired"):
        self.state = state
        self.saved = []

    def product_state(self, pid):
        return {"status": self.state, "err": ""}

    def cookies_dict(self):
        return {}

    def save_product(self, *a, **k):
        self.saved.append((a, k))
        return {"ok": True}


class FakeText:
    ok = True

    def make_title(self, *a, **k):
        return types.SimpleNamespace(ok=True, value="70m Hydraulic Water Well Drilling Rig", error="")

    def make_detail(self, *a, **k):
        return types.SimpleNamespace(ok=True, value="<p>Professional drilling rig</p>", error="")

    def make_skus(self, names):
        return types.SimpleNamespace(ok=True, value="\n".join(f"RIG-{i:03d}" for i in range(1, len(names) + 1)),
                                     error="")


class FakeImage:
    def __init__(self, big: Path, small: Path):
        self.big = big
        self.small = small
        self.calls = []

    def edit(self, fp, prompt):
        self.calls.append(("edit", str(fp), prompt))
        return str(self.big)

    def edit_many(self, files, prompt, tag=""):
        self.calls.append(("edit_many", len(list(files)), prompt))
        return str(self.big)

    def white_background(self, fp):
        self.calls.append(("white", str(fp)))
        return str(self.big)

    def remove_watermark(self, fp):
        self.calls.append(("wm", str(fp)))
        return str(self.big)

    def combined(self, fp):
        self.calls.append(("combined", str(fp)))
        return str(self.big)


def _cfg(**kw):
    from aeclone.config import AppConfig
    c = AppConfig()
    defaults = dict(
        api_key="sk-test", api_base="http://127.0.0.1:9", timeout=5,
        auto_save_products=True, ai_image_enable=True, image_edit_mode="fine",
        img_general_prompt="", image_simple_rules="[]", image_ai_jobs="[]",
        image_edit_plans="[]", designated_edit_enable=False,
        img_min_size_enable=True, img_min_size=800, img_min_size_drop_invalid=False,
        image_cache_enable=False, image_concurrency=1,
        img_fine_main_enable=True, img_fine_detail_enable=False, img_fine_sku_enable=False,
        img_fine_detail_replace=False, editor_main_images_max=6,
        # 测试里用假地址，默认关掉回读校验；专门的用例会显式打开
        prefer_dianxiaomi_album=True, album_verify_url=False,
        enable_ai_title=True, enable_ai_detail=False, enable_ai_sku=False,
        white_bg_enable=False, watermark_enable=False, edit_enable=False,
        img_main_edit=False, img_detail_white_bg=False, img_detail_remove_wm=False,
        img_detail_edit=False, img_sku_white_bg=False, img_sku_remove_wm=False,
        img_sku_edit=False,
        translate_main_pic_enable=False, translate_detail_pic_enable=False, translate_sku_pic=False,
        main_image_generate_enable=False, overlay_watermark_enable=False, watermark="",
        four_in_one=False, main_as_detail=False, pad_main_images_from_extra=False,
        write_main_images_to_product=True, write_detail_html_to_product=True,
        write_sku_codes_to_product=True, write_price_to_product=True,
        combo_enable=False, max_sku_count=256, product_type="普货",
        main_image_position="", main_image_generate_position="prepend",
        background_gen_rules="[]", price_result=None,
        publish_action="save_only", publish_after_save=False,
        apply_template_on_save=False, template_snapshot="",
        spec_weight="", spec_length="", spec_width="", spec_height="",
        declared_value="", custom_attributes=[], auto_generate_sku_code=False,
        custom_sku_prefix_enable=False, modify_stock_999=False, stock_quantity="",
        only_sku_prefix_checkbox=False, sku_split_char="-",
        detail_text_info="", detail_text_info_tail="", detail_text_position="head",
        move_detail_text=False, custom_detail_text_enable=False,
        review_enable=False, estimate_weight_enable=False,
        generate_main_video=False, upload_main_video=False,
        keep_main_images_local=False, apply_template_on_save_flag=False,
    )
    defaults.update(kw)
    for k, v in defaults.items():
        c.set(k, v)
    return c


def _run(cfg, product, big, small, state="editingRequired"):
    from aeclone.services import publisher as pubmod

    # 屏蔽进度总线与提示词方案（避免任何网络/全局状态）
    pubmod.BUS.begin = lambda *a, **k: None
    pubmod.BUS.price_of = lambda m: 999.0
    pubmod.BUS.set_prices = lambda *a, **k: None
    pubmod.emit = lambda *a, **k: None
    # 编辑器自动化：记录要保存的内容，不打开浏览器
    captured = {}

    class FakeSave:
        ok = True
        message = "已保存（测试桩）"
        title = ""
        published = False

    def fake_save_via_api(self, p, res, action, ship, images):
        captured.update({
            "ws": self.ws_endpoint, "pid": p.get("id"), "title": p.get("title_en"),
            "detail_html": p.get("detail_html"),
            "images": images,
            "skus": p.get("skus") if getattr(self.cfg, "write_sku_codes_to_product", True) else None,
            "weight": ship[0], "length": ship[1], "width": ship[2], "height": ship[3],
            "clean_dup_attrs": bool(getattr(self.cfg, "auto_clean_dup_attributes", True)),
        })
        return FakeSave()

    pubmod.Publisher._save_via_api = fake_save_via_api

    # 图片转存：不真的上传，原样返回
    pubmod.Publisher._publicize_images = lambda self, p, res, dry_run=False: p

    dxm = FakeDxm(state)
    pub = pubmod.Publisher(cfg, dxm=dxm, text_service=FakeText(),
                           image_service=FakeImage(big, small), ws_endpoint="ws://fake")
    pub._fetch = lambda u, workdir=None: (Path(str(u)) if Path(str(u)).exists()
                                          else (small if "small" in str(u) else big))
    res = pub.run(product, dry_run=False, manage_progress=False)
    return res, captured, pub


def _product(n=10, skus=1):
    return {
        "id": "P1001",
        "title": "70米水井钻机",
        "status_type": "editingRequired",
        "images": [f"https://ex.com/m{i}.jpg" for i in range(1, n + 1)],
        "detail_images": [],
        "skus": [{"sku": f"SKU-{i}", "stock": "10",
                  "attributes": {"Depth": f"{i * 50}m"}} for i in range(1, skus + 1)],
        "cost": 100.0,
    }


def t1_general_mode_reaches_engine():
    print("\n[测试1] 通用编辑（只有 img_general_prompt，无 rules/jobs）→ 必须真的改图")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png"), _mk(d, "small.png", 500, 500)
        cfg = _cfg(image_edit_mode="general", img_general_prompt="清理并本地化这张商品图",
                   img_general_main_from="1", img_general_main_to="3")
        res, cap, pub = _run(cfg, _product(10), big, small)
        img_calls = [c for c in pub.image.calls if c[0] == "edit"]
        assert len(img_calls) == 3, f"应调用 3 次编辑，实际 {len(img_calls)}：{pub.image.calls}"
        assert all(c[2] == "清理并本地化这张商品图" for c in img_calls), img_calls
        line = [s for s in res.steps if "通用编辑" in s.name]
        assert line, f"缺少「通用编辑」步骤：{[s.name for s in res.steps]}"
        print("  ✓ 3 次调用，提示词正确：", line[0].detail)


def t2_fine_mode_reaches_engine():
    print("\n[测试2] 精细化编辑（jobs 非空）→ 只改指定那张")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png"), _mk(d, "small.png", 500, 500)
        jobs = [{"group": "main", "index": 5, "prompt": "第5张换成工地场景",
                 "mode": "edit", "insert": "replace"}]
        cfg = _cfg(image_edit_mode="fine", image_ai_jobs=json.dumps(jobs, ensure_ascii=False))
        res, cap, pub = _run(cfg, _product(10), big, small)
        many = [c for c in pub.image.calls if c[0] == "edit_many"]
        assert len(many) == 1, f"应只调用 1 次，实际 {len(many)}：{pub.image.calls}"
        assert "工地场景" in many[0][2], many[0]
        print("  ✓ 只调用 1 次：", many[0][2])


def t3_master_switch_off():
    print("\n[测试3] 总开关关闭 → 一次 AI 都不调")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png"), _mk(d, "small.png", 500, 500)
        cfg = _cfg(ai_image_enable=False, image_edit_mode="general",
                   img_general_prompt="清理商品图")
        res, cap, pub = _run(cfg, _product(10), big, small)
        assert not pub.image.calls, f"不应调用 AI：{pub.image.calls}"
        print("  ✓ 0 次调用")


def t4_save_payload_has_images():
    print("\n[测试4] 保存时把处理后的主图传下去")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png"), _mk(d, "small.png")
        cfg = _cfg(image_edit_mode="general", img_general_prompt="清理商品图",
                   img_general_main_from="1", img_general_main_to="2")
        res, cap, pub = _run(cfg, _product(6), big, small)
        assert "images" in cap, f"未传 images：{list(cap)}"
        imgs = cap["images"]
        assert imgs and len(imgs) == 6, f"应传 6 张，实际 {len(imgs) if imgs else 0}"
        assert str(imgs[0]).endswith("big.png"), imgs[0]
        assert str(imgs[2]) == "https://ex.com/m3.jpg", "第3张应保持原图"
        print(f"  ✓ 传了 {len(imgs)} 张，第1张=处理后，第3张=原图")


def t5_ai_title_flows():
    print("\n[测试5] AI 标题真的被写进要保存的标题")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png"), _mk(d, "small.png")
        cfg = _cfg(enable_ai_title=True)
        res, cap, pub = _run(cfg, _product(3), big, small)
        assert cap.get("title") == "70m Hydraulic Water Well Drilling Rig", cap.get("title")
        print("  ✓ 保存标题 =", cap["title"])


def t6_auditing_state_blocks():
    print("\n[测试6] 审核中的商品 → 跳过、不调 AI")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png"), _mk(d, "small.png")
        cfg = _cfg(enable_ai_title=True, image_edit_mode="general", img_general_prompt="清理")
        res, cap, pub = _run(cfg, _product(3), big, small, state="auditing")
        assert not res.ok, "审核中应标记未成功"
        assert not pub.image.calls, "审核中不应调用 AI"
        assert not cap, "审核中不应保存"
        print("  ✓ 跳过：", res.error)


def t7_multi_sku_guard():
    print("\n[测试7] 多 SKU + 写回编码 → 按设计拒绝（未调 AI）")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png"), _mk(d, "small.png")
        cfg = _cfg(write_sku_codes_to_product=True)
        res, cap, pub = _run(cfg, _product(3, skus=3), big, small)
        assert not res.ok and "多 SKU" in res.error, res.error
        assert not pub.image.calls
        print("  ✓ 拒绝：", res.error[:40])


def t8_multi_sku_allowed_when_off():
    print("\n[测试8] 多 SKU 但关闭编码写回 → 可以继续跑")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png"), _mk(d, "small.png")
        cfg = _cfg(write_sku_codes_to_product=False)
        res, cap, pub = _run(cfg, _product(3, skus=3), big, small)
        assert cap, "应当进入保存流程"
        assert cap.get("skus") is None, "关闭写回后不应把 skus 传下去"
        print("  ✓ 已保存，且未写 SKU 行")


def t9_no_double_processing():
    print("\n[测试9] 旧白底开关 + 迁移后的规则同时存在 → 同一张图只处理一次")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png"), _mk(d, "small.png")
        rules = [{"group": "main", "range": "1-3", "ops": ["white"]}]
        cfg = _cfg(white_bg_enable=True, image_simple_rules=json.dumps(rules))
        res, cap, pub = _run(cfg, _product(10), big, small)
        whites = [c for c in pub.image.calls if c[0] == "white"]
        assert len(whites) == 3, f"应只处理 3 张，实际 {len(whites)}：{pub.image.calls}"
        note = [s for s in res.steps if s.name == "图片路径"]
        assert note, f"应提示旧路径已跳过：{[s.name for s in res.steps]}"
        print("  ✓ 只处理 3 张；" + note[0].detail[:40])


def t10_legacy_only_still_works():
    print("\n[测试10] 只有旧白底开关、没有规则 → 旧路径照常工作")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png"), _mk(d, "small.png")
        cfg = _cfg(white_bg_enable=True, image_simple_rules="[]")
        res, cap, pub = _run(cfg, _product(4), big, small)
        # 白底/去水印同时开启时会合并成 combined 一次调用，两种都算处理过
        whites = [c for c in pub.image.calls if c[0] in ("white", "combined", "wm")]
        assert whites, f"旧路径应处理白底：{pub.image.calls}"
        print(f"  ✓ 旧路径处理 {len(whites)} 张（{whites[0][0]}）")


def t11_collected_weight_used():
    # 用户明确要求：设置里填了尺寸/重量 → 一律以设置为准，不被采集值覆盖
    print("\n[测试11] 设置里填了尺寸重量 → 以设置为准（采集值不覆盖）")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png"), _mk(d, "small.png")
        cfg = _cfg(prefer_collected_weight=True, spec_weight="1", spec_length="1",
                   spec_width="1", spec_height="1")
        prod = _product(3)
        prod["fields"] = {"weight": "12.5", "size": "60x40x30"}
        res, cap, pub = _run(cfg, prod, big, small)
        assert cap.get("weight") == "1", cap.get("weight")
        assert (cap.get("length"), cap.get("width"), cap.get("height")) == ("1", "1", "1"), cap
        print("  ✓ 写入 weight=1 尺寸=1x1x1（设置优先）")


def t11b_spec_empty_uses_collected():
    print("\n[测试11b] 设置为空 + 开关开 → 用采集值")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png"), _mk(d, "small.png")
        cfg = _cfg(prefer_collected_weight=True, spec_weight="", spec_length="",
                   spec_width="", spec_height="")
        prod = _product(3)
        prod["fields"] = {"weight": "12.5", "size": "60x40x30"}
        res, cap, pub = _run(cfg, prod, big, small)
        assert cap.get("weight") == "12.5", cap.get("weight")
        assert (cap.get("length"), cap.get("width"), cap.get("height")) == ("60", "40", "30"), cap
        print("  ✓ 写入 weight=12.5 尺寸=60x40x30（采集值）")


def t12_spec_weight_when_off():
    print("\n[测试12] 开关关闭 → 用设置里的统一规格")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png"), _mk(d, "small.png")
        cfg = _cfg(prefer_collected_weight=False, spec_weight="9", spec_length="80",
                   spec_width="50", spec_height="40")
        prod = _product(3)
        prod["fields"] = {"weight": "12.5", "size": "60x40x30"}
        res, cap, pub = _run(cfg, prod, big, small)
        assert cap.get("weight") == "9", cap.get("weight")
        assert (cap.get("length"), cap.get("width"), cap.get("height")) == ("80", "50", "40"), cap
        print("  ✓ 仍用设置值 9 / 80x50x40")


def t13_keep_local_writes_files():
    print("\n[测试13] 保留到本地 = 开 → 落盘到 kept_images/<产品>")
    import aeclone.config as C
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png"), _mk(d, "small.png")
        cfg = _cfg(keep_main_images_local=True)
        orig = C.config_dir
        C.config_dir = lambda: d
        try:
            res, cap, pub = _run(cfg, _product(4), big, small)
        finally:
            C.config_dir = orig
        kept = d / "kept_images" / "P1001"
        files = list(kept.glob("*")) if kept.exists() else []
        assert files, f"应生成留档文件，实际目录：{kept}"
        print(f"  ✓ 生成 {len(files)} 个文件：{[f.name for f in files[:3]]}")


def t14_keep_local_off_no_files():
    print("\n[测试14] 保留到本地 = 关 → 不产生文件")
    import aeclone.config as C
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png"), _mk(d, "small.png")
        cfg = _cfg(keep_main_images_local=False)
        orig = C.config_dir
        C.config_dir = lambda: d
        try:
            _run(cfg, _product(4), big, small)
        finally:
            C.config_dir = orig
        kept = d / "kept_images"
        assert not kept.exists(), f"不应创建目录：{list(kept.glob('*')) if kept.exists() else ''}"
        print("  ✓ 没有生成任何留档")


def t15_clean_dup_flag_passed():
    print("\n[测试15] 属性清理开关被传到编辑器层")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png"), _mk(d, "small.png")
        cfg = _cfg(auto_clean_dup_attributes=False)
        res, cap, pub = _run(cfg, _product(3), big, small)
        assert cap.get("clean_dup_attrs") is False, cap.get("clean_dup_attrs")
        print("  ✓ 关掉时 clean_dup_attrs=False（完全不动属性表）")

        cfg2 = _cfg(auto_clean_dup_attributes=True)
        res2, cap2, pub2 = _run(cfg2, _product(3), big, small)
        assert cap2.get("clean_dup_attrs") is True, cap2.get("clean_dup_attrs")
        print("  ✓ 打开时 clean_dup_attrs=True（保留当前行为）")


def t16_four_in_one_is_local():
    print("\n[测试16] 四合一走本地拼图：不调 AI、原图保留、多 1 张")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png"), _mk(d, "small.png")
        jobs = [{"group": "main", "index": 1, "refs": "1,2,3,4", "prompt": "四合一",
                 "mode": "edit", "insert": "first", "preset": "四合一主图"}]
        cfg = _cfg(image_edit_mode="fine", image_ai_jobs=json.dumps(jobs, ensure_ascii=False))
        res, cap, pub = _run(cfg, _product(6), big, small)
        ai_calls = [c for c in pub.image.calls if c[0] == "edit_many"]
        assert not ai_calls, f"四合一不该调 AI：{ai_calls}"
        step = [s for s in res.steps if "四合一" in s.name]
        assert step and step[0].ok, res.steps
        assert "本地拼图" in step[0].detail, step[0].detail
        assert len(cap.get("images") or []) == 7, len(cap.get("images") or [])
        print("  ✓", step[0].detail, "| 主图 6 → 7")


def t17_multi_input_capped_at_three():
    print("\n[测试17] 多图输入超过 3 张 → 只送前 3 张并告警")
    from aeclone.services import ImageService
    assert ImageService.MAX_INPUT_IMAGES == 3
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png"), _mk(d, "small.png")
        jobs = [{"group": "main", "index": 1, "refs": "2,3,4,5", "prompt": "合并这几张",
                 "mode": "edit", "insert": "replace"}]
        cfg = _cfg(image_edit_mode="fine", image_ai_jobs=json.dumps(jobs, ensure_ascii=False))
        res, cap, pub = _run(cfg, _product(8), big, small)
        many = [c for c in pub.image.calls if c[0] == "edit_many"]
        assert many, "应调用一次多图编辑"
        assert many[0][1] <= 3, f"送进去的素材应 ≤3 张，实际 {many[0][1]}"
        warn = [s for s in res.steps if "素材" in s.name]
        print(f"  ✓ 实际只送 {many[0][1]} 张；" + (warn[0].detail if warn else "（无告警）"))


def t18_empty_sku_image_skipped():
    print("\n[测试18] SKU 没有图片时，不去处理空地址")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png"), _mk(d, "small.png")
        rules = [{"group": "sku", "range": "1-5", "ops": ["white"]}]
        cfg = _cfg(image_simple_rules=json.dumps(rules))
        prod = _product(3)
        prod["skus"] = [{"sku": "A", "stock": "1"}]        # 没有 image 字段
        res, cap, pub = _run(cfg, prod, big, small)
        calls = [c for c in pub.image.calls if c[0] in ("white", "combined", "wm")]
        assert not calls, f"不该对空地址调用 AI：{calls}"
        step = [s for s in res.steps if s.name.startswith("简单处理")]
        assert step, f"没有简单处理步骤：{[s.name for s in res.steps]}"
        assert step[0].ok, step[0]
        assert "空" in step[0].detail or "没有可用" in step[0].detail, step[0].detail
        print("  ✓", step[0].name, "→", step[0].detail)


def t19_album_upload_switch():
    print("\n[测试19] 开启「出图直传相册」→ 用店小秘返回的地址替换")
    import aeclone.integrations.cos_upload as cosmod
    from types import SimpleNamespace

    calls = []

    def fake_upload(client, path, full_cid="", bucket="wxalbum", timeout=120):
        calls.append(str(path))
        return SimpleNamespace(file_id="f1", url="https://dxm-cdn.example.com/album/a.png",
                               size=1, name="a.png")

    orig = cosmod.upload_image
    cosmod.upload_image = fake_upload
    try:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            big, small = _mk(d, "big.png"), _mk(d, "small.png")
            cfg = _cfg(prefer_dianxiaomi_album=True, image_edit_mode="general",
                       img_general_prompt="清理", img_general_main_from="1", img_general_main_to="2")
            res, cap, pub = _run(cfg, _product(4), big, small)
            imgs = cap.get("images") or []
            assert calls, "应调用相册上传"
            assert imgs and imgs[0].startswith("https://dxm-cdn.example.com/"), imgs[:2]
            step = [s for s in res.steps if "相册" in s.name]
            assert step and step[0].ok, step
            print("  ✓", step[0].detail)
            print("  ✓ 第1张已换成店小秘地址：", imgs[0][:60])
    finally:
        cosmod.upload_image = orig


def t20_album_upload_off_by_default():
    print("\n[测试20] 关闭时完全不碰店小秘相册")
    import aeclone.integrations.cos_upload as cosmod
    calls = []
    orig = cosmod.upload_image
    cosmod.upload_image = lambda *a, **k: calls.append(1)
    try:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            big, small = _mk(d, "big.png"), _mk(d, "small.png")
            cfg = _cfg(prefer_dianxiaomi_album=False)
            res, cap, pub = _run(cfg, _product(3), big, small)
            assert not calls, f"不该上传相册：{len(calls)} 次"
            print("  ✓ 0 次相册上传")
    finally:
        cosmod.upload_image = orig


def t21_album_urls_not_reuploaded():
    print("\n[测试21] 相册地址不会被再传一次到图床（否则等于白传）")
    import aeclone.integrations.cos_upload as cosmod
    from types import SimpleNamespace

    orig = cosmod.upload_image
    cosmod.upload_image = lambda client, path, full_cid="", **k: SimpleNamespace(
        file_id="f", url="https://cos.example.com/album/x.png", size=1, name="x.png")
    try:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            big, small = _mk(d, "big.png"), _mk(d, "small.png")
            cfg = _cfg(prefer_dianxiaomi_album=True)
            res, cap, pub = _run(cfg, _product(4), big, small)
            imgs = cap.get("images") or []
            assert imgs and all("cos.example.com" in u for u in imgs[:4]), imgs
            # 步骤里不应再出现「已转公网/已转存」这种重传记录
            steps = {s.name: s.detail for s in res.steps}
            assert "图片转存" not in steps or "已全部公网" in steps.get("图片转存", ""), steps
            print("  ✓ 4 张都是相册地址，未被重传")
            print("  ✓", steps.get("图片转存", "（无转存步骤）"))
    finally:
        cosmod.upload_image = orig


def t22_callback_method_exists():
    print("\n[测试22] DianxiaomiClient 有 upload_callback（以前只有端点没方法）")
    from aeclone.integrations.dianxiaomi import DianxiaomiClient
    assert hasattr(DianxiaomiClient, "upload_callback"), "缺少 upload_callback"
    import inspect
    params = inspect.signature(DianxiaomiClient.upload_callback).parameters
    for p in ("file_id", "full_cid", "bucket"):
        assert p in params, f"缺少参数 {p}"
    print("  ✓ 方法存在，参数齐全")


def t23_album_url_verify_fallback():
    print("\n[测试23] 相册返回的地址打不开 → 保留原地址，不交坏图")
    import aeclone.integrations.cos_upload as cosmod
    from types import SimpleNamespace

    orig_up = cosmod.upload_image
    orig_check = pubmod_readable = None
    cosmod.upload_image = lambda client, path, full_cid="", **k: SimpleNamespace(
        file_id="f", url="https://private-cos.example.com/x.png", size=1, name="x.png")

    from aeclone.services.publisher import Publisher
    orig = Publisher._url_readable
    Publisher._url_readable = staticmethod(lambda url, timeout=12: False)
    try:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            big, small = _mk(d, "big.png"), _mk(d, "small.png")
            cfg = _cfg(prefer_dianxiaomi_album=True, album_verify_url=True)
            res, cap, pub = _run(cfg, _product(3), big, small)
            imgs = cap.get("images") or []
            assert all("private-cos" not in u for u in imgs), imgs
            step = [s for s in res.steps if "相册" in s.name]
            assert step and any("打不开" in x.detail or "失败" in x.detail for x in step), step
            print("  ✓ 没有采用打不开的地址；", step[-1].detail)
    finally:
        cosmod.upload_image = orig_up
        Publisher._url_readable = orig


def t24_album_url_verify_pass():
    print("\n[测试24] 地址可读 → 正常采用")
    import aeclone.integrations.cos_upload as cosmod
    from types import SimpleNamespace
    orig_up = cosmod.upload_image
    cosmod.upload_image = lambda client, path, full_cid="", **k: SimpleNamespace(
        file_id="f", url="https://public-cdn.example.com/x.png", size=1, name="x.png")
    from aeclone.services.publisher import Publisher
    orig = Publisher._url_readable
    Publisher._url_readable = staticmethod(lambda url, timeout=12: True)
    try:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            big, small = _mk(d, "big.png"), _mk(d, "small.png")
            cfg = _cfg(prefer_dianxiaomi_album=True, album_verify_url=True)
            res, cap, pub = _run(cfg, _product(3), big, small)
            imgs = cap.get("images") or []
            assert all("public-cdn.example.com" in u for u in imgs), imgs
            print("  ✓ 全部采用相册地址")
    finally:
        cosmod.upload_image = orig_up
        Publisher._url_readable = orig


def t25_dry_run_is_informative():
    print("\n[测试25] 预演不再只回 3 行：要算价格、出图片计划、估算 AI 次数")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png"), _mk(d, "small.png")
        cfg = _cfg(image_edit_mode="general", img_general_prompt="清理并本地化",
                   img_general_main_from="1", img_general_main_to="3")
        from aeclone.services.publisher import Publisher
        pub = Publisher(cfg, dxm=None, ws_endpoint="ws://stub")
        res = pub.run(_product(6), dry_run=True, manage_progress=False)
        names = [s.name for s in res.steps]
        assert "预演" in names and "标准化" in names, names
        assert "价格计算" in names, names
        plan = [s for s in res.steps if s.name == "图片方案"]
        assert plan, names
        assert "预计调用 AI 3 次" in plan[0].detail, plan[0].detail
        price = [s for s in res.steps if s.name == "价格计算"][0]
        assert "¥" in price.detail and "None" not in price.detail, price.detail
        print("  ✓", " | ".join(f"{s.name}:{s.detail[:28]}" for s in res.steps))


def t26_dry_run_no_ai_no_network():
    print("\n[测试26] 预演绝不调用 AI / 不下载图片")
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        big, small = _mk(d, "big.png"), _mk(d, "small.png")
        cfg = _cfg(image_edit_mode="general", img_general_prompt="清理",
                   img_edit_mode_placeholder=None) if False else _cfg(
            image_edit_mode="general", img_general_prompt="清理",
            img_general_main_from="1", img_general_main_to="3")
        from aeclone.services.publisher import Publisher
        calls = {"n": 0}
        pub = Publisher(cfg, dxm=None, ws_endpoint="ws://stub")
        pub._edit_with_prompt = lambda *a, **k: calls.__setitem__("n", calls["n"] + 1)
        pub._fetch = lambda *a, **k: calls.__setitem__("n", calls["n"] + 1)
        res = pub.run(_product(4), dry_run=True, manage_progress=False)
        assert calls["n"] == 0, f"预演不该有任何调用，实际 {calls['n']}"
        print("  ✓ 0 次 AI / 0 次下载")


if __name__ == "__main__":
    tests = [t1_general_mode_reaches_engine, t2_fine_mode_reaches_engine,
             t3_master_switch_off, t4_save_payload_has_images, t5_ai_title_flows,
             t6_auditing_state_blocks, t7_multi_sku_guard, t8_multi_sku_allowed_when_off,
             t9_no_double_processing, t10_legacy_only_still_works,
             t11_collected_weight_used, t12_spec_weight_when_off,
             t13_keep_local_writes_files, t14_keep_local_off_no_files,
             t15_clean_dup_flag_passed, t16_four_in_one_is_local,
             t17_multi_input_capped_at_three, t18_empty_sku_image_skipped,
             t19_album_upload_switch, t20_album_upload_off_by_default,
             t21_album_urls_not_reuploaded, t22_callback_method_exists,
             t23_album_url_verify_fallback, t24_album_url_verify_pass,
             t25_dry_run_is_informative, t26_dry_run_no_ai_no_network]
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
