"""Alibaba 刊登评分（发布门禁）离线测试。

规则：发布 op=2 必须评分 ≥4.5，否则阻止并列出缺什么（属性/标题/关键词/详情/图片/视频/贸易/合规）。
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from aeclone.services.alibaba_score import (  # noqa: E402
    PASS_SCORE, probe_image, score_product,
)

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ✓ {name}")
    else:
        FAILURES.append(name)
        print(f"  ✗ {name} {detail}")


def good_product(**over):
    attrs = [
        {"attribute_name": "Place of Origin", "value_name": "China"},
        {"attribute_name": "Color", "value_name": "Blue"},
        {"attribute_name": "Material", "value_name": "Fiberglass"},
        {"attribute_name": "Applicable People", "value_name": "All"},
        {"attribute_name": "Type", "value_name": "Container swimming pool"},
        {"attribute_name": "Model Number", "value_name": "JZX-001"},
    ]
    detail_imgs = [{"url": f"https://img.example.com/d{i}.jpg"} for i in range(4)]
    product = {
        "id": "p1", "categoryId": "202107201",
        "subject": "Customized Fiberglass Container Pool - Ready-Made Prefabricated Outdoor Swimming Pool",
        "keywords": "fiberglass container pool, prefabricated outdoor pool, villa pool, "
                    "custom fiberglass pool, outdoor swimming pool",
        "attributes": json.dumps(attrs, ensure_ascii=False),
        "mainImage": ",".join(f"https://img.example.com/m{i}.jpg" for i in range(6)),
        "detailImage": json.dumps(detail_imgs),
        "textDesc": ("1) Overview\n"
                     + "High quality fiberglass container pool for villa and resort. " * 20
                     + "\n2) Specifications\nSize: 20ft\nMaterial: Fiberglass\n"
                       "Lifespan: 30 years\n3) Installation\nEasy installation with local support."),
        "logisticsProperty": "general_cargo_0",
        "priceType": "ladder_price",
        "bulkDiscountPrices": json.dumps([{"start_quantity": 1, "price": 4300.0}]),
        "wholesaleTrade": json.dumps({
            "min_order_quantity": 1, "package_size": "300X200X150", "weight": "300",
            "deliver_periods": [{"quantity": 1, "process_period": 30}],
            "tariffsHsCode": [{"toCountry": "US", "hsCode": "9506999000"}],
            "inventory": [{"warehouseCode": "CN_LOCAL_01", "value": 10}],
            "unit_type": "Piece", "sale_type": "normal"}),
        "mainVideoId": None,
    }
    product.update(over)
    return product


def required_attrs():
    return [{"nameEn": n} for n in
            ("Place of Origin", "Color", "Material", "Applicable People", "Type")]


def test_good_product_passes():
    r = score_product(good_product(), required_attrs=required_attrs())
    check("好商品评分 ≥ 4.5", r.total >= PASS_SCORE, f"total={r.total:.2f}")
    check("好商品 passed=True", r.passed)
    check("无阻断项", not r.blockers, r.blockers)
    # 视频缺失只扣视频维度，不应把总分压到 4.5 以下
    v = r.item("video")
    check("视频缺失被单独指出", v and v.score < 3 and any("视频" in p for p in v.problems))
    return r


def test_empty_product_blocked():
    r = score_product({}, required_attrs=required_attrs())
    check("空商品评分 < 4.5", r.total < PASS_SCORE, f"total={r.total:.2f}")
    check("空商品 passed=False", not r.passed)
    fixes = " ".join(r.fixes)
    check("修复建议包含类目", "类目" in fixes, fixes[:80])
    check("修复建议包含主图", "主图" in fixes or "图片" in fixes, fixes[:80])
    check("修复建议包含标题", "标题" in fixes, fixes[:80])


def test_placeholder_attributes_penalized():
    attrs = json.loads(good_product()["attributes"])
    for a in attrs:
        if a["attribute_name"] in ("Color", "Material", "Type"):
            a["value_name"] = "Other"
    r = score_product(good_product(attributes=json.dumps(attrs)), required_attrs=required_attrs())
    item = r.item("attributes")
    check("占位属性被识别", r.placeholder_attrs, r.placeholder_attrs)
    check("占位属性扣分", item and item.score < 4.5, item.score if item else None)
    check("占位属性修复建议", any("占位" in f for f in r.fixes))


def test_missing_images_and_title_low_score():
    r = score_product(good_product(mainImage="", subject="",
                                   textDesc="<p>short</p>"), required_attrs=required_attrs())
    check("缺图/缺标题时不通过", not r.passed and r.total < PASS_SCORE, f"total={r.total:.2f}")
    check("阻断包含主图", any("主图" in b for b in r.blockers), r.blockers)
    check("阻断包含标题", any("标题" in b for b in r.blockers), r.blockers)


def test_image_checker_affects_images_dimension():
    good = score_product(good_product(), required_attrs=required_attrs())
    bad = score_product(good_product(), required_attrs=required_attrs(),
                        image_checker=lambda url: (False, 400, 400, "400x400 <800"))
    gi, bi = good.item("images"), bad.item("images")
    check("正常图 images 维度 ≥4.5", gi and gi.score >= 4.5, gi.score if gi else None)
    check("小图扣分", bi and bi.score < gi.score, f"{bi.score if bi else None}")
    check("小图修复建议", any("尺寸" in f or "1000" in f for f in bad.fixes))


def test_probe_image_rejects_bad_url():
    ok, w, h, err = probe_image("https://127.0.0.1:9/not-exist.jpg", timeout=1)
    check("坏 URL probe 返回失败", ok is False and bool(err), err)


def test_pipeline_publish_gate_blocks_low_score():
    """pipeline：op=2 低分阻止；op=1 仍可预演（只警告）。"""
    import test_alibaba_pipeline as BT
    from aeclone.services.alibaba_pipeline import run_alibaba_product

    with BT.clean_env(AURORA_SKIP_LICENSE="1"):
        r2 = run_alibaba_product("pid-1", BT.make_cfg(), dry_run=True, op="2",
                                 client=BT.FakeClient(), text_pipeline=BT.FakeTextPipeline())
    check("op=2 低分被阻止", (not r2.ok) and "评分" in (r2.error or ""), r2.error[:90])
    check("op=2 结果带评分", r2.score > 0, r2.score)
    check("op=2 阻止时有修复建议", bool(r2.score_fixes), r2.score_fixes[:3])

    with BT.clean_env(AURORA_SKIP_LICENSE="1"):
        r1 = run_alibaba_product("pid-1", BT.make_cfg(), dry_run=True, op="1",
                                 client=BT.FakeClient(), text_pipeline=BT.FakeTextPipeline())
    check("op=1 低分仍可预演（保存草稿不受发布线限制）", r1.ok, r1.error[:90])


def main():
    print("=" * 60)
    print("Alibaba 刊登评分 / 发布门禁测试")
    test_good_product_passes()
    test_empty_product_blocked()
    test_placeholder_attributes_penalized()
    test_missing_images_and_title_low_score()
    test_image_checker_affects_images_dimension()
    test_probe_image_rejects_bad_url()
    test_pipeline_publish_gate_blocks_low_score()
    print("=" * 60)
    if FAILURES:
        print(f"FAILED: {len(FAILURES)} 项：{FAILURES}")
        return 1
    print("PASS: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
