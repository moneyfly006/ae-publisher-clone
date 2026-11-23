"""TikTok 类目属性补全离线单测（不联网、不写文件）。

覆盖：
1. ``load_category_attributes`` / ``normalize_category_attribute`` 的真实字段形状
   （``values`` 是 JSON 字符串、``valueList`` 已解析、``transName`` 中文稳定、
   ``requirementConditions`` 条件必填、``isMandatory/isMultipleSelected/isCustomized``）；
2. ``suggest_attributes`` 的本地规则（Color/Size/Material/Warranty/Model/Power/Origin…）、
   已有值不覆盖、无法确定给候选 + 中文原因、AI 兜底（patch，不联网）；
3. ``apply_attribute_suggestions`` 写回真实前端形状
   （attributeId/attributeName/values[{valueId,valueName}]）且只补空值；
4. 流水线接入：``prepare_save`` 自动补值、缺失必填进 issues、属性接口失败只提醒；
   ``run_tiktok_product`` 的「类目属性补全」步骤与结果字段；预演不写。

跑法：``.venv/bin/python tests/test_tiktok_attributes.py``
"""
import copy
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.services import tiktok_payload as P
from aeclone.services.tiktok_pipeline import prepare_save, run_tiktok_product

IMAGE_1 = "https://img.example.com/1.jpg"
IMAGE_2 = "https://img.example.com/2.jpg"

# 真实 attributeList 响应形状（2026-11-22 实测片段：GB 601265 / DE 910472）
RAW_MATERIAL = {
    "id": 598869, "site": "GB", "categoryId": "601265", "crossBorder": 1,
    "attrId": "100157", "attrName": "Material", "transName": "材质", "attrType": 3,
    "isMandatory": 0, "isMultipleSelected": 1, "isCustomized": 1,
    "values": json.dumps([
        {"id": "1000039", "name": "Cotton"},
        {"id": "1000013", "name": "Linen"},
        {"id": "1000037", "name": "Polyester"},
    ]),
    "transValues": json.dumps([
        {"id": "1000039", "name": "Cotton", "transName": "棉"},
        {"id": "1000013", "name": "Linen", "transName": "亚麻"},
    ]),
    "valueList": [{"id": "1000039", "name": "Cotton"},
                  {"id": "1000013", "name": "Linen"},
                  {"id": "1000037", "name": "Polyester"}],
    "requirementConditions": None,
}

RAW_BATCH = {
    "attrId": "102255", "attrName": "Batch number", "transName": "生产批次号",
    "isMandatory": 1, "isMultipleSelected": 0, "isCustomized": 1,
    "values": "", "valueList": [], "requirementConditions": None,
}

RAW_CONDITIONAL = {
    "attrId": "102273", "attrName": "Minimale Leistung (W)", "transName": "最小功率 (W)",
    "isMandatory": 1, "isMultipleSelected": 0, "isCustomized": 1,
    "values": "", "valueList": [],
    "requirementConditions": json.dumps([{
        "attribute_id": "102759", "attribute_value_id": "1000058",
        "condition_type": "VALUE_ID_MATCH", "region": None,
    }]),
}

RAW_BOOLEAN = {
    "attrId": "100108", "attrName": "With battery", "transName": "产品带电池",
    "isMandatory": 1, "isMultipleSelected": 0, "isCustomized": 0,
    "values": json.dumps([{"id": "1", "name": "Yes"}, {"id": "2", "name": "No"}]),
    "valueList": [{"id": "1", "name": "Yes"}, {"id": "2", "name": "No"}],
}

RAW_BRAND = {
    "attrId": "-99999", "attrName": "Brand", "transName": "品牌",
    "isMandatory": 0, "isMultipleSelected": 0, "isCustomized": 1, "valueList": [],
}


def attr(attr_id, name, trans="", *, mandatory=0, multi=0, custom=1, values=None):
    """构造真实形状的属性（``values`` 用 JSON 字符串，验证解析）。"""
    return {
        "attrId": attr_id, "attrName": name, "transName": trans,
        "isMandatory": mandatory, "isMultipleSelected": multi, "isCustomized": custom,
        "values": json.dumps(values or []), "valueList": values or [],
        "requirementConditions": None,
    }


def make_product(**over):
    product = {
        "id": "1729000000000001",
        "shopId": "9408005",
        "site": "GB",
        "siteIds": ["S1"],
        "productName": "Women Cotton Linen Blouse Short Sleeve Casual Summer Shirt",
        "description": ("<p>Made of cotton and linen, breathable for summer.</p>"
                        "<ul><li>Material: Cotton</li><li>2 year warranty</li></ul>"),
        "categoryId": "601265",
        "categoryList": [{"categoryId": "601265", "categoryName": "Blouses & Shirts"}],
        "isLeaf": 1,
        "productType": 1,
        "mainImage": "|".join([IMAGE_1, IMAGE_2]),
        "draftImgUrl": "|".join([IMAGE_1, IMAGE_2]),
        "packageWeight": 0.3,
        "packageLength": 10,
        "packageWidth": 10,
        "packageHeight": 5,
        "packageDimensionUnit": "CENTIMETER",
        "warehouseId": "WH-1",
        "variationList": [{
            "sellerSku": "BLU-2024A",
            "originalPrice": 19.99,
            "availableStock": 5,
            "skuProperties": [],
        }],
        "brandInfo": "Generic",
        "productAttributes": "[]",
        "productCertifications": "[]",
    }
    product.update(over)
    return product


def make_cfg(**over):
    cfg = {
        "api_base": "", "api_key": "", "timeout": 5,
        "tiktok_cookies": "", "manual_cookies": {},
        "tiktok_shop_id": "", "tiktok_shop_name": "", "tiktok_site": "",
        "tiktok_sku": "", "tiktok_price": "", "tiktok_stock": "",
        "tiktok_weight": "", "tiktok_length": "", "tiktok_width": "",
        "tiktok_height": "", "tiktok_dimension_unit": "",
        "tiktok_warehouse_id": "", "tiktok_product_unit": "",
        "tiktok_brand_id": "", "tiktok_manufacturer_id": "",
        "tiktok_responsible_person_id": "", "tiktok_product_attributes": "",
        "tiktok_key_feature_count": 5,
    }
    cfg.update(over)
    return cfg


class FakeAttrClient:
    """离线替身：类目属性返回真实形状；可配置为抛异常/无方法/只接受两个参数。"""

    def __init__(self, product=None, attrs=None, *, error=None, no_method=False,
                 positional_only=False):
        self.product = product if product is not None else make_product()
        self.attrs = list(attrs or [])
        self.error = error
        self.no_method = no_method
        self.positional_only = positional_only
        self.attr_calls = []
        self.saved = []
        if no_method:
            # 模拟旧替身：没有 category_attributes 方法
            for name in ("category_attributes",):
                self.__dict__.pop(name, None)

    # ---- 只读接口 ----
    def product_detail(self, product_id):
        return {"product": copy.deepcopy(self.product),
                "category": {"id": "601265", "isLeaf": 1},
                "shopId": "9408005"}

    def shops(self):
        return [{"id": "9408005", "name": "QingHe TikTok", "platform": "tiktok"}]

    def sites(self, shop_id=""):
        return [{"site": "GB", "site_shop_id": "S1"}]

    def site_limits(self, site=""):
        return {}

    def search_categories(self, keyword, site, shop_id="-1", cross_border=True):
        return []

    def check_banned_words(self, title="", description=""):
        return []

    # ---- 类目属性 ----
    def category_attributes(self, *args, **kwargs):
        if self.positional_only and kwargs:
            raise TypeError("category_attributes() takes 2 positional arguments")
        self.attr_calls.append((args, kwargs))
        if isinstance(self.error, Exception):
            raise self.error
        return copy.deepcopy(self.attrs)

    # ---- 写接口（记录，不联网）----
    def save_product(self, payload, op="1", save_mode=""):
        self.saved.append(copy.deepcopy(payload))
        return {"code": 0, "msg": "ok"}


class NoAttrClient(FakeAttrClient):
    """没有 category_attributes 的客户端（模拟旧替身 / 只读最小客户端）。"""

    category_attributes = None


class NormalizeTests(unittest.TestCase):
    def test_values_json_string_parsed_with_trans_names(self):
        row = P.normalize_category_attribute(RAW_MATERIAL, site="GB", category_id="601265")
        self.assertEqual(row["attrId"], "100157")
        self.assertEqual(row["attrName"], "Material")
        self.assertEqual(row["transName"], "材质")
        self.assertEqual(row["label"], "材质")  # UI 展示用中文
        self.assertFalse(row["isMandatory"])
        self.assertTrue(row["isMultipleSelected"])
        self.assertTrue(row["isCustomized"])
        self.assertEqual(row["valueShape"], P.ATTR_SHAPE_ENUM_CUSTOM)
        self.assertEqual([v["name"] for v in row["values"]],
                         ["Cotton", "Linen", "Polyester"])
        self.assertEqual(row["values"][0]["transName"], "棉")  # transValues 合并
        self.assertEqual(row["site"], "GB")
        self.assertEqual(row["categoryId"], "601265")

    def test_ui_compat_aliases(self):
        row = P.normalize_category_attribute(RAW_BATCH)
        self.assertTrue(row["required"])
        self.assertEqual(row["name"], "生产批次号")
        self.assertEqual(row["options"], [])

    def test_free_text_shape_and_conditional(self):
        free = P.normalize_category_attribute(RAW_BATCH)
        self.assertEqual(free["valueShape"], P.ATTR_SHAPE_CUSTOM)
        self.assertTrue(free["isMandatory"])
        cond = P.normalize_category_attribute(RAW_CONDITIONAL)
        self.assertTrue(cond["isConditional"])
        self.assertEqual(len(cond["conditions"]), 1)

    def test_value_list_preferred_and_deduped(self):
        raw = dict(RAW_MATERIAL)
        raw["values"] = json.dumps([{"id": "1000039", "name": "Cotton"}])
        row = P.normalize_category_attribute(raw)
        self.assertEqual(len(row["values"]), 3)  # valueList 优先（3 条）
        dup = attr("1", "X", values=[{"id": "1", "name": "A"}, {"id": "1", "name": "A"}])
        self.assertEqual(len(P.normalize_category_attribute(dup)["values"]), 1)

    def test_invalid_values_and_missing_attr_id(self):
        row = P.normalize_category_attribute({"attrId": "1", "values": "{not json"})
        self.assertEqual(row["values"], [])
        self.assertEqual(P.normalize_category_attribute({"attrName": "X"}), {})

    def test_idempotent(self):
        once = P.normalize_category_attribute(RAW_MATERIAL)
        twice = P.normalize_category_attribute(once)
        self.assertEqual(once["attrId"], twice["attrId"])
        self.assertEqual(once["values"], twice["values"])
        self.assertEqual(once["isMandatory"], twice["isMandatory"])


class LoadTests(unittest.TestCase):
    def test_loads_and_normalizes_in_order(self):
        client = FakeAttrClient(attrs=[RAW_MATERIAL, RAW_BATCH])
        rows = P.load_category_attributes("gb", "601265", client)
        self.assertEqual([r["attrId"] for r in rows], ["100157", "102255"])
        args, kwargs = client.attr_calls[0]
        self.assertEqual(args, ("GB", "601265"))
        self.assertEqual(kwargs.get("attr_type"), 3)  # type=3 才返回完整属性
        self.assertEqual(kwargs.get("shop_id"), "-1")

    def test_dedupe_same_attr_id(self):
        rows = P.load_category_attributes("GB", "1", FakeAttrClient(attrs=[RAW_BATCH, RAW_BATCH]))
        self.assertEqual(len(rows), 1)

    def test_empty_inputs_and_missing_client(self):
        errors = []
        self.assertEqual(P.load_category_attributes("", "1", FakeAttrClient(), errors=errors), [])
        self.assertTrue(errors)
        self.assertEqual(P.load_category_attributes("GB", "", FakeAttrClient()), [])
        self.assertEqual(P.load_category_attributes("GB", "1", None), [])
        self.assertEqual(P.load_category_attributes("GB", "1", NoAttrClient()), [])

    def test_interface_error_is_swallowed_with_reason(self):
        errors = []
        client = FakeAttrClient(error=RuntimeError("boom"))
        self.assertEqual(P.load_category_attributes("GB", "1", client, errors=errors), [])
        self.assertTrue(any("失败" in e for e in errors))

    def test_positional_only_client_fallback(self):
        client = FakeAttrClient(attrs=[RAW_BATCH], positional_only=True)
        rows = P.load_category_attributes("GB", "1", client)
        self.assertEqual(len(rows), 1)

    def test_json_string_rows_tolerated(self):
        client = FakeAttrClient(attrs=[RAW_BATCH])
        client.category_attributes = lambda *a, **k: json.dumps([RAW_BATCH])
        self.assertEqual(len(P.load_category_attributes("GB", "1", client)), 1)


class SuggestTests(unittest.TestCase):
    ATTRS = [
        attr("100157", "Material", "材质", multi=1,
             values=[{"id": "1000039", "name": "Cotton"},
                     {"id": "1000013", "name": "Linen"},
                     {"id": "1000037", "name": "Polyester"}]),
        attr("100397", "Season", "季节", multi=1,
             values=[{"id": "1001162", "name": "Summer"},
                     {"id": "1001163", "name": "Winter"}]),
        attr("100107", "Warranty type", "保修类型",
             values=[{"id": "w1", "name": "1 Year"}, {"id": "w2", "name": "2 Years"},
                     {"id": "w3", "name": "No Warranty"}]),
        attr("101476", "Warranty duration", "保修期限",
             values=[{"id": "d1", "name": "1 year"}, {"id": "d2", "name": "2 years"}]),
        attr("900001", "Colour", "颜色", multi=1,
             values=[{"id": "c1", "name": "Black"}, {"id": "c2", "name": "White"}]),
        attr("900002", "Size", "尺码",
             values=[{"id": "s1", "name": "S"}, {"id": "s2", "name": "M"},
                     {"id": "s3", "name": "L"}]),
        attr("900003", "Power", "功率",
             values=[{"id": "p1", "name": "20W"}, {"id": "p2", "name": "30W"}]),
        attr("900004", "Country of origin", "原产国/原产地",
             values=[{"id": "o1", "name": "China"}, {"id": "o2", "name": "Vietnam"}]),
        attr("100177", "Model", "型号", mandatory=1),
    ]

    def suggest(self, product, attrs=None, **kwargs):
        return P.suggest_attributes(product, attrs or self.ATTRS, "GB", **kwargs)

    @staticmethod
    def by_label(result, label):
        return [s for s in result["suggestions"] if s["label"] == label]

    def test_material_from_title_matches_enum_ids(self):
        result = self.suggest(make_product())
        found = self.by_label(result, "材质")
        self.assertTrue(found)
        values = {v["valueName"] for v in found[0]["values"]}
        self.assertEqual(values, {"Cotton", "Linen"})  # 多选：两个都命中
        self.assertEqual(found[0]["confidence"], "high")
        self.assertTrue(all(v["valueId"] for v in found[0]["values"]))
        self.assertIn("标题", found[0]["reason"])

    def test_season_and_warranty(self):
        result = self.suggest(make_product())
        season = self.by_label(result, "季节")
        self.assertEqual([v["valueName"] for v in season[0]["values"]], ["Summer"])
        warranty = self.by_label(result, "保修类型")
        self.assertTrue(warranty)  # "2 year warranty" → 2 Years（包含匹配）
        self.assertEqual(warranty[0]["values"][0]["valueName"], "2 Years")
        duration = self.by_label(result, "保修期限")
        self.assertEqual(duration[0]["values"][0]["valueName"], "2 years")

    def test_color_size_power_origin_and_model(self):
        product = make_product(
            productName="500ml Stainless Steel Bottle Black Size: M Power 20W Made in Vietnam",
            description="<p>Model: BTL-500X</p>", sellerSku="SKU-1")
        result = self.suggest(product)
        self.assertEqual(self.by_label(result, "颜色")[0]["values"][0]["valueName"], "Black")
        self.assertEqual(self.by_label(result, "尺码")[0]["values"][0]["valueName"], "M")
        self.assertEqual(self.by_label(result, "功率")[0]["values"][0]["valueName"], "20W")
        self.assertEqual(self.by_label(result, "原产国/原产地")[0]["values"][0]["valueName"],
                         "Vietnam")
        model = self.by_label(result, "型号")
        self.assertTrue(model)
        # 500ml 是规格，不得当型号
        self.assertNotIn("500ml", [v["valueName"] for v in model[0]["values"]])
        self.assertIn("BTL-500X", model[0]["values"][0]["valueName"])

    def test_existing_values_are_not_overwritten(self):
        existing = json.dumps([{
            "attributeId": "100157", "attributeName": "Material",
            "values": [{"valueId": "1000037", "valueName": "Polyester"}],
        }])
        result = self.suggest(make_product(productAttributes=existing))
        material = self.by_label(result, "材质")
        self.assertEqual([v["valueName"] for v in material[0]["values"]], ["Polyester"])
        self.assertEqual(material[0]["source"], "existing")
        self.assertIn("不覆盖", material[0]["reason"])

    def test_unresolved_has_candidates_and_chinese_reason(self):
        attrs = [attr("900010", "Neckline", "领型", mandatory=1,
                      values=[{"id": "n1", "name": "V-Neck"},
                              {"id": "n2", "name": "Round Neck"}])]
        result = self.suggest(make_product(), attrs)
        self.assertEqual(len(result["unresolved"]), 1)
        entry = result["unresolved"][0]
        self.assertTrue(entry["isMandatory"])
        self.assertEqual(entry["reason"], "本地无该属性的推导规则，请从候选人工确认")
        self.assertEqual([c["name"] for c in entry["candidates"]], ["V-Neck", "Round Neck"])
        self.assertEqual(result["missing_mandatory"], ["领型"])
        self.assertEqual(result["coverage"]["mandatory_ratio"], 0.0)

    def test_boolean_attribute_is_not_guessed(self):
        result = self.suggest(make_product(), [RAW_BOOLEAN])
        self.assertEqual(result["suggestions"], [])
        self.assertIn("布尔型", result["unresolved"][0]["reason"])
        self.assertEqual(result["missing_mandatory"], ["产品带电池"])

    def test_free_text_attribute_derived_from_text(self):
        attrs = [attr("103637", "TIS license no.", "TIS 许可证号", mandatory=1)]
        product = make_product(description="<p>TIS license no: 1234/2566</p>")
        result = self.suggest(product, attrs)
        self.assertTrue(result["suggestions"])
        suggestion = result["suggestions"][0]
        self.assertTrue(suggestion["isFreeText"])
        self.assertEqual(suggestion["values"][0]["valueId"], "")
        self.assertTrue(suggestion["values"][0]["valueName"])

    def test_brand_special_attribute_skipped(self):
        result = self.suggest(make_product(), [RAW_BRAND, RAW_BATCH])
        self.assertEqual([s["attrId"] for s in result["suggestions"]], [])
        self.assertEqual([u["attrId"] for u in result["unresolved"]], ["102255"])

    def test_coverage_counts_mandatory(self):
        attrs = [
            attr("100157", "Material", "材质", mandatory=1,
                 values=[{"id": "1000039", "name": "Cotton"}]),
            RAW_BATCH,
        ]
        result = self.suggest(make_product(), attrs)
        self.assertEqual(result["coverage"]["mandatory_total"], 2)
        self.assertEqual(result["coverage"]["mandatory_filled"], 1)
        self.assertEqual(result["coverage"]["mandatory_ratio"], 0.5)
        self.assertEqual(result["coverage"]["attr_total"], 2)

    def test_single_broken_attribute_does_not_break_others(self):
        """逐属性 try/except：一个属性数据异常不影响其它属性补全。"""
        attrs = [
            attr("100157", "Material", "材质", values=[{"id": "1000039", "name": "Cotton"}]),
            attr("900030", "Broken", "坏属性", values=[{"id": "x", "name": "X"}]),
        ]
        real = P._rule_key

        def flaky(row):
            if row.get("attrId") == "900030":
                raise ValueError("bad raw row")
            return real(row)

        with mock.patch.object(P, "_rule_key", side_effect=flaky):
            result = self.suggest(make_product(), attrs)
        self.assertEqual([s["label"] for s in result["suggestions"]], ["材质"])
        self.assertTrue(any("解析失败" in n for n in result["notes"]))
        self.assertIn("900030", [u["attrId"] for u in result["unresolved"]])

    def test_ai_fill_accepts_enum_only(self):
        attrs = [attr("900020", "Pattern", "图案花纹", mandatory=1,
                      values=[{"id": "pt1", "name": "Floral"},
                              {"id": "pt2", "name": "Solid"}])]
        fake = {"900020": "Floral", "999999": "bogus"}
        with mock.patch.object(P, "_ai_fill", return_value=(fake, "")):
            result = self.suggest(make_product(), attrs, use_ai=True, cfg=make_cfg())
        self.assertTrue(result["ai_used"])
        self.assertEqual(result["suggestions"][0]["values"][0]["valueName"], "Floral")
        self.assertEqual(result["suggestions"][0]["values"][0]["valueId"], "pt1")
        self.assertEqual(result["suggestions"][0]["source"], "ai")
        self.assertEqual(result["suggestions"][0]["confidence"], "low")
        self.assertEqual(result["missing_mandatory"], [])
        # AI 编造的属性 ID 不进入建议
        self.assertNotIn("999999", [u["attrId"] for u in result["unresolved"]])

    def test_ai_value_outside_enum_is_dropped(self):
        attrs = [attr("900020", "Pattern", "图案花纹", mandatory=1,
                      values=[{"id": "pt1", "name": "Floral"}])]
        with mock.patch.object(P, "_ai_fill", return_value=({"900020": "Stripey"}, "")):
            result = self.suggest(make_product(), attrs, use_ai=True, cfg=make_cfg())
        self.assertFalse(result["ai_used"])
        self.assertEqual(result["missing_mandatory"], ["图案花纹"])
        self.assertTrue(any("不在类目枚举" in n for n in result["notes"]))

    def test_ai_failure_does_not_break_pipeline(self):
        """_ai_fill 抛异常时：_complete_attributes 兜底，预演继续（只进警告）。"""
        def boom(*_a, **_k):
            raise RuntimeError("network down")

        client = FakeAttrClient(attrs=[RAW_BATCH])
        with mock.patch.object(P, "_ai_fill", boom):
            res = run_tiktok_product("p1", make_cfg(), client=client, dry_run=True,
                                     use_ai_attributes=True)
        self.assertTrue(any("属性建议失败" in w for w in res.warnings))
        self.assertFalse(res.saved)

    def test_ai_fill_helper_swallows_errors(self):
        with mock.patch("aeclone.api.ApiClient", side_effect=RuntimeError("no api")):
            data, note = P._ai_fill(make_product(), [P.normalize_category_attribute(RAW_BATCH)],
                                    "GB", make_cfg(api_key="x"))
        self.assertEqual(data, {})
        self.assertIn("AI 属性补全失败", note)

    def test_ai_fill_helper_wires_client_and_parses_reply(self):
        """真实接线：ApiClient 用 cfg 的 base/key 构造，回复按 JSON 解析（不联网）。"""
        calls = {}

        class FakeApi:
            def __init__(self, base, key, timeout):
                calls["args"] = (base, key, timeout)

            def chat(self, model, messages, max_tokens=0):
                calls["prompt"] = messages[0]["content"]
                return '```json\n{"102255": "BATCH-2026"}\n```'

        attrs = [P.normalize_category_attribute(RAW_BATCH)]
        with mock.patch("aeclone.api.ApiClient", FakeApi), \
                mock.patch("aeclone.services.model_of", return_value="fake-model"):
            data, note = P._ai_fill(make_product(), attrs, "GB",
                                    make_cfg(api_base="https://x", api_key="k", timeout=7))
        self.assertEqual(data, {"102255": "BATCH-2026"})
        self.assertEqual(note, "")
        self.assertEqual(calls["args"], ("https://x", "k", 7))
        self.assertIn("102255", calls["prompt"])

    def test_no_ai_by_default(self):
        with mock.patch.object(P, "_ai_fill") as fake:
            self.suggest(make_product(), [RAW_BATCH])
        fake.assert_not_called()


class ApplyTests(unittest.TestCase):
    def suggestion(self, attr_id="100157", values=None):
        return {"suggestions": [{
            "attrId": attr_id, "attrName": "Material", "label": "材质",
            "values": values or [{"valueId": "1000039", "valueName": "Cotton"}],
        }]}

    def test_writes_real_frontend_shape(self):
        payload = {"productAttributes": "", "categoryId": "1"}
        out = P.apply_attribute_suggestions(payload, self.suggestion())
        items = json.loads(out["productAttributes"])
        self.assertEqual(items, [{
            "attributeId": "100157",
            "attributeName": "Material",
            "values": [{"valueId": "1000039", "valueName": "Cotton"}],
        }])
        self.assertEqual(payload["productAttributes"], "")  # 入参不被修改

    def test_does_not_overwrite_user_values(self):
        existing = json.dumps([{
            "attributeId": "100157", "attributeName": "Material",
            "values": [{"valueId": "1000037", "valueName": "Polyester"}],
        }])
        out = P.apply_attribute_suggestions({"productAttributes": existing},
                                            self.suggestion())
        items = json.loads(out["productAttributes"])
        self.assertEqual([v["valueName"] for v in items[0]["values"]], ["Polyester"])

    def test_overwrite_flag(self):
        existing = json.dumps([{
            "attributeId": "100157", "attributeName": "Material",
            "values": [{"valueId": "1000037", "valueName": "Polyester"}],
        }])
        out = P.apply_attribute_suggestions({"productAttributes": existing},
                                            self.suggestion(), overwrite=True)
        items = json.loads(out["productAttributes"])
        self.assertEqual([v["valueName"] for v in items[0]["values"]], ["Cotton"])

    def test_preserves_brand_and_unknown_keys(self):
        existing = json.dumps([
            {"attributeId": "-99999", "attributeName": "品牌",
             "values": [{"valueId": "b1", "valueName": "Acme"}]},
            {"attributeId": "100157", "attributeName": "Material", "custom": True,
             "values": []},
        ])
        out = P.apply_attribute_suggestions({"productAttributes": existing},
                                            self.suggestion())
        items = json.loads(out["productAttributes"])
        self.assertEqual(items[0]["attributeId"], "-99999")
        self.assertEqual(items[0]["values"][0]["valueName"], "Acme")
        self.assertTrue(items[1].get("custom"))  # 既有键保留
        self.assertEqual(items[1]["values"][0]["valueName"], "Cotton")

    def test_empty_suggestions_keep_payload_untouched(self):
        payload = {"productAttributes": "[]", "categoryId": "1"}
        out = P.apply_attribute_suggestions(payload, {"suggestions": []})
        self.assertEqual(out["productAttributes"], "[]")
        out2 = P.apply_attribute_suggestions(payload, {})
        self.assertEqual(out2["productAttributes"], "[]")

    def test_malformed_existing_json_replaced_cleanly(self):
        out = P.apply_attribute_suggestions({"productAttributes": "not-json"},
                                            self.suggestion())
        self.assertEqual(len(json.loads(out["productAttributes"])), 1)

    def test_missing_mandatory_attributes(self):
        attrs = [P.normalize_category_attribute(RAW_BATCH),
                 P.normalize_category_attribute(RAW_MATERIAL),
                 P.normalize_category_attribute(RAW_CONDITIONAL)]
        payload = {"productAttributes": json.dumps([
            {"attributeId": "100157", "attributeName": "Material",
             "values": [{"valueId": "1000039", "valueName": "Cotton"}]},
        ])}
        missing = P.missing_mandatory_attributes(payload, attrs)
        self.assertEqual([m["attrId"] for m in missing], ["102255", "102273"])
        self.assertTrue(missing[0]["isFreeText"])
        self.assertTrue(missing[1]["isConditional"])


class PipelineAttributeTests(unittest.TestCase):
    """流水线接入：自动补值 / 不覆盖 / 缺失进 issues / 失败只提醒。"""

    ATTRS = [
        attr("100157", "Material", "材质", mandatory=1, multi=1,
             values=[{"id": "1000039", "name": "Cotton"},
                     {"id": "1000013", "name": "Linen"}]),
        attr("100397", "Season", "季节", multi=1,
             values=[{"id": "1001162", "name": "Summer"}]),
    ]

    def test_prepare_save_fills_missing_attributes(self):
        client = FakeAttrClient(attrs=self.ATTRS)
        _, payload, issues, warnings = prepare_save("p1", make_cfg(), client=client)
        items = json.loads(payload["productAttributes"])
        self.assertEqual({i["attributeId"] for i in items}, {"100157", "100397"})
        self.assertIn("attributeId", items[0])
        self.assertEqual(issues, [])
        self.assertTrue(any("属性" in w for w in warnings))

    def test_prepare_save_does_not_overwrite_user_values(self):
        existing = json.dumps([{
            "attributeId": "100157", "attributeName": "Material",
            "values": [{"valueId": "1000013", "valueName": "Linen"}],
        }])
        client = FakeAttrClient(product=make_product(productAttributes=existing),
                                attrs=self.ATTRS)
        _, payload, _, _ = prepare_save("p1", make_cfg(), client=client)
        items = json.loads(payload["productAttributes"])
        material = [i for i in items if i["attributeId"] == "100157"][0]
        self.assertEqual([v["valueName"] for v in material["values"]], ["Linen"])

    def test_missing_mandatory_goes_to_issues(self):
        client = FakeAttrClient(attrs=[RAW_BATCH])
        _, _, issues, _ = prepare_save("p1", make_cfg(), client=client)
        self.assertTrue(any("必填类目属性" in i for i in issues))
        self.assertTrue(any("生产批次号" in i for i in issues))

    def test_conditional_mandatory_only_warns(self):
        client = FakeAttrClient(attrs=[RAW_CONDITIONAL])
        _, _, issues, warnings = prepare_save("p1", make_cfg(), client=client)
        self.assertFalse(any("必填类目属性" in i for i in issues))
        self.assertTrue(any("条件必填" in w for w in warnings))

    def test_attribute_api_failure_only_warns(self):
        client = FakeAttrClient(error=RuntimeError("attribute 500"))
        _, payload, issues, warnings = prepare_save("p1", make_cfg(), client=client)
        self.assertEqual(issues, [])
        self.assertTrue(any("类目属性接口调用失败" in w for w in warnings))
        self.assertEqual(payload["productAttributes"], "[]")  # 原样透传

    def test_client_without_attribute_method_keeps_passthrough(self):
        existing = json.dumps([{"attributeId": "100157", "attributeName": "Material",
                                "values": [{"valueId": "1000039", "valueName": "Cotton"}]}])
        client = NoAttrClient(product=make_product(productAttributes=existing))
        _, payload, issues, _ = prepare_save("p1", make_cfg(), client=client)
        self.assertEqual(payload["productAttributes"], existing)
        self.assertEqual(issues, [])

    def test_run_adds_attribute_step_and_stats(self):
        client = FakeAttrClient(attrs=self.ATTRS)
        res = run_tiktok_product("p1", make_cfg(), client=client, dry_run=True)
        names = [s.name for s in res.steps]
        self.assertIn("类目属性补全", names)
        step = [s for s in res.steps if s.name == "类目属性补全"][0]
        self.assertTrue(step.ok)
        self.assertIn("类目属性", step.detail)
        self.assertEqual(res.attribute_stats.get("attr_total"), 2)
        self.assertEqual(res.attribute_stats.get("remaining"), 0)
        self.assertTrue(res.attribute_suggestions)
        self.assertTrue(res.ok)
        self.assertTrue(res.preview)

    def test_run_reports_missing_mandatory_attribute(self):
        client = FakeAttrClient(attrs=[RAW_BATCH])
        res = run_tiktok_product("p1", make_cfg(), client=client, dry_run=True)
        self.assertFalse(res.ok)
        self.assertEqual(res.missing_attributes, ["生产批次号"])
        step = [s for s in res.steps if s.name == "类目属性补全"][0]
        self.assertFalse(step.ok)
        self.assertTrue(any("必填类目属性" in i for i in res.issues))

    def test_dry_run_never_writes_with_attributes(self):
        client = FakeAttrClient(attrs=self.ATTRS)
        run_tiktok_product("p1", make_cfg(), client=client, dry_run=True)
        self.assertEqual(client.saved, [])

    def test_attribute_failure_does_not_break_preview(self):
        client = FakeAttrClient(error=RuntimeError("boom"))
        res = run_tiktok_product("p1", make_cfg(), client=client, dry_run=True)
        self.assertTrue(res.ok)
        self.assertTrue(any("类目属性接口调用失败" in w for w in res.warnings))

    def test_ai_attribute_failure_keeps_preview_alive(self):
        client = FakeAttrClient(attrs=[RAW_BATCH])
        with mock.patch.object(P, "_ai_fill",
                               return_value=({}, "AI 属性补全失败（不阻断）：mock")):
            res = run_tiktok_product("p1", make_cfg(), client=client, dry_run=True,
                                     use_ai_attributes=True)
        step = [s for s in res.steps if s.name == "类目属性补全"][0]
        self.assertFalse(step.ok)  # 仍缺必填
        self.assertTrue(any("AI 属性补全失败" in w for w in res.warnings))
        self.assertFalse(res.saved)


class AttributeRealSaveTests(unittest.TestCase):
    """真实写路径：补全的属性必须进入实际提交的保存包（不被 prepare_save 重组丢掉）。"""

    ATTRS = [
        attr("100157", "Material", "材质", mandatory=1, multi=1,
             values=[{"id": "1000039", "name": "Cotton"}]),
    ]

    def setUp(self):
        import os
        self._env = {k: os.environ.pop(k, None)
                     for k in ("AURORA_NO_SAVE", "AURORA_TEST", "AURORA_SKIP_LICENSE")}
        os.environ["AURORA_SKIP_LICENSE"] = "1"  # 授权放行（core.guard 识别）

    def tearDown(self):
        import os
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_rule_filled_attributes_reach_save_payload(self):
        client = FakeAttrClient(attrs=self.ATTRS)
        res = run_tiktok_product("p1", make_cfg(), client=client, dry_run=False,
                                 confirm_write=True)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(len(client.saved), 1)
        saved = client.saved[0]
        items = json.loads(saved["productAttributes"])
        self.assertEqual(items[0]["attributeId"], "100157")
        self.assertEqual(items[0]["values"][0]["valueName"], "Cotton")

    def test_ai_filled_attributes_reach_save_payload(self):
        with mock.patch.object(P, "_ai_fill",
                               return_value=({"100157": "Cotton"}, "")):
            client = FakeAttrClient(
                product=make_product(productName="Stylish Blouse for Daily Wear",
                                     description="<p>Soft fabric, easy care.</p>"),
                attrs=[attr("100157", "Material", "材质", mandatory=1,
                            values=[{"id": "1000039", "name": "Cotton"}])])
            res = run_tiktok_product("p1", make_cfg(), client=client, dry_run=False,
                                     confirm_write=True, use_ai_attributes=True)
        self.assertTrue(res.ok, res.error)
        saved = client.saved[0]
        values = json.loads(saved["productAttributes"])[0]["values"]
        self.assertEqual(values[0]["valueName"], "Cotton")
        self.assertEqual(res.attribute_suggestions[0]["source"], "ai")


if __name__ == "__main__":
    unittest.main(verbosity=2)
