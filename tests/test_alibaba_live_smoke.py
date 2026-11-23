"""Alibaba 真实刊登冒烟修复的离线回归（mock 覆盖真实失败场景，绝不联网/写店铺）。

覆盖 2026-11-22 真实 op=1 冒烟发现的问题：
1. 评分器把真实 dict 形状的 productSku 当成「没有 SKU」→ sku_price 商品价格维度
   2.5 分、总分 4.25 < 发布线 4.5（真实商品 220114598434958538）。
2. 总分 4.495 显示成「4.50/5」却仍判未通过（门禁与展示精度不一致）。
3. 采集详情里的站外联系方式（WhatsApp/邮箱/电话）直接写进 textDesc → 评分/审核风险。
4. 全局包装/重量/HS 默认值静默覆盖商品真实数据（0.64kg 小件写成 500kg）。
5. 属性自动候选值/全局覆盖没有提示（缺依据的字段静默写入）。

跑法：``.venv/bin/python tests/test_alibaba_live_smoke.py``；失败 exit 1。
"""
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone.config import AppConfig
from aeclone.services.alibaba_payload import (
    build_alibaba_save, validate_alibaba_save, strip_contact_info,
)
from aeclone.services.alibaba_pipeline import run_alibaba_product
from aeclone.services.alibaba_score import PASS_SCORE, contact_hit, score_product

PID = "220114598434958538"


# ---------------- 真实形状的「泳池」样本 ----------------
DETAIL_OK = ("1) Product Overview\n"
             "This prefabricated fiberglass container swimming pool is designed for "
             "villa, hotel and resort outdoor use. The FRP structure is corrosion "
             "resistant and supports above-ground or in-ground installation.\n"
             "2) Specifications\n"
             "Size: 20ft / 40ft\nMaterial: Fiberglass (FRP)\nColor: White\n"
             "Shape: Rectangle\nApplicable People: Adult, Children\n"
             "3) Installation\n"
             "The pool is delivered as a complete module; local crane and base "
             "preparation are required. Installation guidance is provided.\n"
             "4) Application\n"
             "Suitable for backyard, garden, hotel rooftop and resort projects.\n"
             * 8)


CONTACT_DETAIL = (DETAIL_OK[:900]
                  + "\nWhatsApp: +86 18237117765 Email: sales@example.com\n")


def pool_product(**over):
    attrs = [
        {"attribute_id": "1", "attribute_name": "Place of Origin",
         "value_id": "100000458", "value_name": "China"},
        {"attribute_id": "210190015", "attribute_name": "Color",
         "value_id": "4", "value_name": "Other"},
        {"attribute_id": "238736366", "attribute_name": "Material",
         "value_id": "326756224", "value_name": "Glass Fiber"},
        {"attribute_id": "270605350", "attribute_name": "Applicable People",
         "value_id": "3254703", "value_name": "All"},
        {"attribute_id": "191284229", "attribute_name": "Type",
         "value_id": "190089526", "value_name": "Above Ground Pools"},
    ]
    product = {
        "id": PID, "shopId": "9466975", "categoryId": "202107201",
        "fullCid": "18,100005657,100005663,202107201",
        "subject": "20ft Prefab Steel Swimming Pool - Outdoor Container Pool for Backyard",
        "keywords": "machinery, construction equipment, laser screed, drilling rig, "
                    "concrete leveling",
        "attributes": json.dumps(attrs, ensure_ascii=False),
        "mainImage": ",".join(f"https://img.example.com/m{i}.jpg" for i in range(6)),
        "draftImgUrl": "|".join(f"https://img.example.com/m{i}.jpg" for i in range(6)),
        "detailImage": json.dumps([{"displayName": "default", "value": -2,
                                    "url": f"https://img.example.com/d{i}.jpg",
                                    "text": ""} for i in range(4)]),
        "textDesc": DETAIL_OK,
        "logisticsProperty": "general_cargo_0",
        "priceType": "sku_price",
        # 真实 edit.json：productSku 是 JSON 字符串（dict：attributes + special_skus）
        "productSku": json.dumps({
            "attributes": [{"attribute_id": "-1", "attribute_name": "Color",
                            "value_id": "-1", "value_name": "White"}],
            "special_skus": [
                {"exclude": False, "price": 7407.04, "sku_code": "SKU-20FT",
                 "attributes": [{"attribute_name": "Color", "value_name": "White"}]},
                {"exclude": False, "price": 9800.0, "sku_code": "SKU-40FT",
                 "attributes": [{"attribute_name": "Color", "value_name": "Gray"}]},
            ],
        }),
        "bulkDiscountPrices": "[]",
        "wholesaleTrade": json.dumps({
            "min_order_quantity": 1, "package_size": "35X16X8", "weight": "640",
            "unit_type": "Piece", "sale_type": "normal",
            "deliver_periods": [{"quantity": 1, "process_period": 30}],
            "tariffsHsCode": [{"toCountry": "US", "hsCode": "9506999000"}],
            "inventory": [{"warehouseCode": "CN_LOCAL_01", "value": 10}],
        }),
    }
    product.update(over)
    return product


def make_cfg(**over):
    data = {
        "alibaba_cookies": "", "alibaba_category_id": "", "alibaba_full_cid": "",
        "alibaba_keywords": "machinery, construction equipment, laser screed, "
                            "drilling rig, concrete leveling",
        "alibaba_attr_presets": "", "alibaba_price_type": "ladder_price",
        "alibaba_min_order_quantity": 1, "alibaba_unit_type": "Piece",
        "alibaba_package_size": "300X200X200", "alibaba_weight": "500",
        "alibaba_stock": 0, "alibaba_hs_code": "84791000",
        "api_key": "",
    }
    data.update(over)
    return AppConfig(data)


REQUIRED_ATTRS = [
    {"attrNameId": "1", "nameEn": "Place of Origin", "required": 1, "inputType": "single_select",
     "values": json.dumps([{"id": "100000458", "name_en": "China"},
                           {"id": "100000459", "name_en": "United States"}])},
    {"attrNameId": "210190015", "nameEn": "Color", "required": 1, "inputType": "multi_select",
     "values": json.dumps([{"id": "4", "name_en": "Other"}, {"id": "5", "name_en": "White"}])},
    {"attrNameId": "238736366", "nameEn": "Material", "required": 1, "inputType": "multi_select",
     "values": json.dumps([{"id": "326756224", "name_en": "Glass Fiber"},
                           {"id": "190091184", "name_en": "FRP"}])},
    {"attrNameId": "270605350", "nameEn": "Applicable People", "required": 1,
     "inputType": "multi_select",
     "values": json.dumps([{"id": "3254703", "name_en": "All"},
                           {"id": "3972330", "name_en": "Adult"}])},
    {"attrNameId": "191284229", "nameEn": "Type", "required": 1, "inputType": "multi_select",
     "values": json.dumps([{"id": "190089526", "name_en": "Above Ground Pools"},
                           {"id": "190089532", "name_en": "Swimming Pools"}])},
]
SCHEMA_FIELDS = [{"fieldId": "textDesc"}, {"fieldId": "detailImage"}]
SAVE_OK = {"code": 0, "data": {"msg": "您的产品编辑成功！", "productId": PID}}


def score_of(product, cfg=None, save=None):
    return score_product(product, cfg, save=save, required_attrs=REQUIRED_ATTRS)


class FakeClient:
    """离线替身：只记录调用，绝不真实联网。"""

    def __init__(self, product=None, save_resp=None):
        self.product = pool_product() if product is None else product
        self.save_resp = SAVE_OK if save_resp is None else save_resp
        self.saved = []
        self.required = list(REQUIRED_ATTRS)

    def product_detail(self, product_id):
        return dict(self.product)

    def required_attributes(self, category_id):
        return list(self.required)

    def category_schema(self, category_id):
        return {"fields": list(SCHEMA_FIELDS)}

    def check_banned_words(self, text):
        return {"code": 0, "data": []}

    def online_category_scope(self, max_pages=10, page_size=100):
        return {"categories": {"202107201": 2}, "scanned": 100, "total": 100, "pages": 2}

    def save_product(self, product, save_dict=None, op="1"):
        self.saved.append((dict(product), dict(save_dict or {}), str(op)))
        return self.save_resp


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("AURORA_NO_SAVE", "AURORA_TEST", "AURORA_SKIP_LICENSE")}
    env.update(extra)
    return patch.dict(os.environ, env, clear=True)


def step_names(res):
    return [s.name for s in res.steps]


# ---------------- 1. 评分器：真实 SKU 形状 ----------------
class ScoreSkuRegressionTests(unittest.TestCase):
    def test_dict_productsku_is_not_missing(self):
        """回归：220114598434958538 的 dict productSku 曾被打成「缺少 SKU」→ 4.25。"""
        product = pool_product()
        save = build_alibaba_save(product, make_cfg(), {}, attr_defs=REQUIRED_ATTRS)
        self.assertEqual(save["priceType"], "sku_price")
        r = score_of(product, make_cfg(), save)
        trade = r.item("trade")
        self.assertIsNotNone(trade)
        self.assertEqual(trade.score, 5.0, trade.problems)
        self.assertFalse(any("缺少 SKU" in p for p in trade.problems), trade.problems)

    def test_real_shape_reaches_publish_line(self):
        product = pool_product()
        save = build_alibaba_save(product, make_cfg(), {}, attr_defs=REQUIRED_ATTRS)
        r = score_of(product, make_cfg(), save)
        # 加权和 4.495 → 按两位小数 4.5；修复前是 4.245/4.25
        self.assertGreaterEqual(r.total, PASS_SCORE, r.log())
        self.assertTrue(r.passed, r.log())
        self.assertEqual(r.total, 4.5)

    def test_missing_sku_still_penalized(self):
        product = pool_product(productSku="")
        save = build_alibaba_save(product, make_cfg(), {}, attr_defs=REQUIRED_ATTRS)
        r = score_of(product, make_cfg(), save)
        trade = r.item("trade")
        self.assertLessEqual(trade.score, 2.5, trade.problems)
        self.assertTrue(any("缺少 SKU" in p for p in trade.problems), trade.problems)

    def test_all_excluded_sku_penalized(self):
        product = pool_product(productSku=json.dumps({
            "attributes": [{"attribute_name": "Color", "value_name": "White"}],
            "special_skus": [{"exclude": True, "price": 10.0}],
        }))
        save = build_alibaba_save(product, make_cfg(), {}, attr_defs=REQUIRED_ATTRS)
        r = score_of(product, make_cfg(), save)
        trade = r.item("trade")
        self.assertLess(trade.score, 5.0)
        self.assertTrue(any("无有效 SKU" in p for p in trade.problems), trade.problems)


# ---------------- 2. 门禁精度 ----------------
class ScoreDetailThresholdTests(unittest.TestCase):
    """详情门槛必须与提示词（800~1500）/文档（800~1800）一致，不能把 1747 扣成 4.0。"""

    def _score_with_detail(self, n_chars):
        text = ("This pool detail paragraph describes structure and application. "
                * (n_chars // 20 + 2))[:n_chars]
        product = pool_product(textDesc=text)
        save = build_alibaba_save(product, make_cfg(), {}, attr_defs=REQUIRED_ATTRS)
        return score_of(product, make_cfg(), save)

    def test_800_plus_chars_full_detail_score(self):
        r = self._score_with_detail(1747)
        self.assertEqual(r.item("detail").score, 5.0, r.item("detail").problems)

    def test_short_detail_still_penalized(self):
        r = self._score_with_detail(500)
        self.assertLess(r.item("detail").score, 4.0, r.item("detail").problems)


class ScorePrecisionTests(unittest.TestCase):
    def test_weighted_total_rounds_before_gate(self):
        product = pool_product()
        save = build_alibaba_save(product, make_cfg(), {}, attr_defs=REQUIRED_ATTRS)
        r = score_of(product, make_cfg(), save)
        raw = sum(i.score * i.weight for i in r.items) / 100
        self.assertAlmostEqual(raw, 4.495, places=6)
        self.assertEqual(r.total, 4.5)
        self.assertTrue(r.passed)

    def test_pipeline_gate_message_not_self_contradictory(self):
        """op=2 低分：提示里的分数必须真的低于发布线（不能「4.50/5 未达 4.5」）。"""
        low = pool_product(textDesc="short detail", mainImage="https://img.example.com/a.jpg")
        with clean_env(AURORA_SKIP_LICENSE="1"):
            r = run_alibaba_product(PID, make_cfg(), dry_run=True, op="2",
                                    client=FakeClient(low))
        self.assertFalse(r.ok)
        m = re.search(r"刊登评分 ([\d.]+)/5 未达发布线", r.error or "")
        self.assertIsNotNone(m, r.error)
        self.assertLess(float(m.group(1)), PASS_SCORE)


# ---------------- 3. 联系方式清理 ----------------
class ContactStripTests(unittest.TestCase):
    def test_strip_removes_channels_and_keeps_specs(self):
        raw = ("Size: 1200 800 900 mm\nYear: 2020-2021\nWeight: 1200 kg\n"
               "Contact: james WhatsApp/WeChat +86 18237117765 "
               "Email: sales@example.com https://example.com/pool\n"
               "⭐\n")
        clean, removed = strip_contact_info(raw)
        self.assertFalse(contact_hit(clean), clean)
        self.assertIn("1200 800 900", clean)
        self.assertIn("2020-2021", clean)
        self.assertIn("1200 kg", clean)
        self.assertTrue(any("@" in x for x in removed), removed)
        self.assertTrue(any("18237117765" in x for x in removed), removed)
        self.assertNotIn("⭐", clean)

    def test_build_save_cleans_text_desc_and_reports(self):
        product = pool_product(textDesc=CONTACT_DETAIL)
        save = build_alibaba_save(product, make_cfg(), {}, attr_defs=REQUIRED_ATTRS)
        self.assertFalse(contact_hit(save["textDesc"]), save["textDesc"])
        self.assertTrue(save.get("_contact_removed"), save.get("_contact_removed"))

    def test_pipeline_warns_contact_cleanup(self):
        product = pool_product(textDesc=CONTACT_DETAIL)
        with clean_env():
            r = run_alibaba_product(PID, make_cfg(), dry_run=True, op="1",
                                    client=FakeClient(product))
        joined = " ".join(r.warnings)
        self.assertIn("站外联系方式", joined, joined)
        self.assertIn("联系方式清理", step_names(r))

    def test_clean_detail_has_no_contact_warning(self):
        with clean_env():
            r = run_alibaba_product(PID, make_cfg(), dry_run=True, op="1",
                                    client=FakeClient())
        self.assertNotIn("站外联系方式", " ".join(r.warnings))


# ---------------- 4. 全局默认值不能静默覆盖商品真实数据 ----------------
class GlobalDefaultsTests(unittest.TestCase):
    def test_product_package_and_weight_win(self):
        product = pool_product()          # 真实：35X16X8 / 640kg
        save = build_alibaba_save(product, make_cfg(), {}, attr_defs=REQUIRED_ATTRS)
        trade = json.loads(save["wholesaleTrade"])
        self.assertEqual(trade["package_size"], "35X16X8")
        self.assertEqual(str(trade["weight"]), "640")
        notes = " ".join(save.get("_overwrites") or [])
        self.assertIn("保留商品原值", notes, notes)
        self.assertIn("300X200X200", notes)

    def test_missing_package_falls_back_to_cfg(self):
        trade = {"min_order_quantity": 1, "weight": 0, "unit_type": "Piece",
                 "sale_type": "normal"}
        product = pool_product(wholesaleTrade=json.dumps(trade))
        save = build_alibaba_save(product, make_cfg(), {}, attr_defs=REQUIRED_ATTRS)
        new_trade = json.loads(save["wholesaleTrade"])
        self.assertEqual(new_trade["package_size"], "300X200X200")
        self.assertEqual(str(new_trade["weight"]), "500")

    def test_pipeline_surfaces_global_data_mismatch(self):
        with clean_env():
            r = run_alibaba_product(PID, make_cfg(), dry_run=True, op="1",
                                    client=FakeClient())
        self.assertTrue(any("全局配置与商品数据不一致" in w for w in r.warnings), r.warnings)
        self.assertIn("全局数据核对", step_names(r))

    def test_hs_code_not_overwritten(self):
        product = pool_product()
        save = build_alibaba_save(product, make_cfg(alibaba_hs_code="84791000"), {},
                                  attr_defs=REQUIRED_ATTRS)
        rows = json.loads(save["wholesaleTrade"]).get("tariffsHsCode") or []
        self.assertEqual(rows[0]["hsCode"], "9506999000")
        self.assertIn("保留商品原值 9506999000", " ".join(save.get("_overwrites") or []))


# ---------------- 5. 属性 / 类目脏值不静默 ----------------
class AttributeDirtyValueTests(unittest.TestCase):
    def test_ambiguous_required_value_becomes_review_warning(self):
        """Type 原值「Pool」在候选中有歧义 → 自动选 Other 时必须提示复核。"""
        attrs = json.loads(pool_product()["attributes"])
        for a in attrs:
            if a["attribute_name"] == "Type":
                a["value_name"] = "Pool"
                a["value_id"] = "-1"
        defs = list(REQUIRED_ATTRS)
        product = pool_product(attributes=json.dumps(attrs))
        with clean_env():
            r = run_alibaba_product(PID, make_cfg(), dry_run=True, op="1",
                                    client=FakeClient(product))
        joined = " ".join(r.warnings)
        self.assertIn("需复核", joined, joined)
        self.assertIn("属性自动补齐", step_names(r))

    def test_required_value_id_missing_blocks(self):
        save = {
            "categoryId": "202107201", "fullCid": "18,100005657,100005663,202107201",
            "subject": "Pool", "keywords": "pool, spa, outdoor",
            "mainImage": "https://img.example.com/a.jpg",
            "priceType": "ladder_price",
            "bulkDiscountPrices": '[{"start_quantity":1,"price":10}]',
            "wholesaleTrade": '{"min_order_quantity":1,"package_size":"1X1X1","weight":"2"}',
            "textDesc": "detail", "detailImage": "[]",
            "attributes": json.dumps([
                {"attribute_id": "1", "attribute_name": "Place of Origin",
                 "value_id": "-1", "value_name": "China"}]),
        }
        no_id = [{"attrNameId": "1", "nameEn": "Place of Origin", "required": 1,
                  "values": json.dumps([{"name_en": "China"}])}]   # 平台候选缺 id
        issues = validate_alibaba_save({}, save, None, no_id, [])
        self.assertTrue(any("缺少 value_id" in i for i in issues), issues)

    def test_dirty_category_none_blocks(self):
        save = {"categoryId": "None", "subject": "Pool", "keywords": "a, b, c",
                "mainImage": "https://x/a.jpg", "priceType": "ladder_price",
                "bulkDiscountPrices": '[{"start_quantity":1,"price":1}]',
                "wholesaleTrade": '{"min_order_quantity":1,"package_size":"1X1X1","weight":"2"}',
                "attributes": "[]", "textDesc": "d", "detailImage": "[]"}
        issues = validate_alibaba_save({}, save, None, [], [])
        self.assertTrue(any("类目" in i for i in issues), issues)


# ---------------- 6. 关键词相关性提示 ----------------
class KeywordMismatchTests(unittest.TestCase):
    def test_unrelated_global_keywords_warn(self):
        with clean_env():
            r = run_alibaba_product(PID, make_cfg(), dry_run=True, op="1",
                                    client=FakeClient())
        self.assertTrue(any("无任何重叠" in w for w in r.warnings), r.warnings)
        self.assertIn("关键词相关性", step_names(r))

    def test_relevant_keywords_no_warning(self):
        cfg = make_cfg(alibaba_keywords="container pool, fiberglass pool, outdoor pool")
        with clean_env():
            r = run_alibaba_product(PID, cfg, dry_run=True, op="1", client=FakeClient())
        self.assertFalse(any("无任何重叠" in w for w in r.warnings), r.warnings)


# ---------------- 7. 真实保存路径（mock）--------------
class MockRealSaveTests(unittest.TestCase):
    def test_op1_saves_once_with_clean_payload(self):
        product = pool_product(textDesc=CONTACT_DETAIL)
        client = FakeClient(product)
        with clean_env(AURORA_SKIP_LICENSE="1"):
            r = run_alibaba_product(PID, make_cfg(), dry_run=False, op="1",
                                    use_ai=False, client=client)
        self.assertTrue(r.ok, r.error)
        self.assertTrue(r.saved)
        self.assertFalse(r.preview)
        self.assertEqual(len(client.saved), 1)
        _prod, save, op = client.saved[0]
        self.assertEqual(op, "1")
        self.assertFalse(contact_hit(save["textDesc"]), save["textDesc"])
        self.assertEqual(json.loads(save["wholesaleTrade"])["package_size"], "35X16X8")
        self.assertEqual(save["op"], "1")

    def test_forced_preview_never_saves(self):
        client = FakeClient()
        with clean_env(AURORA_NO_SAVE="1", AURORA_SKIP_LICENSE="1"):
            r = run_alibaba_product(PID, make_cfg(), dry_run=False, op="1",
                                    use_ai=False, client=client)
        self.assertTrue(r.preview)
        self.assertEqual(client.saved, [])


def main():
    print("=" * 64)
    print("Alibaba 真实刊登冒烟修复回归")
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print("=" * 64)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
