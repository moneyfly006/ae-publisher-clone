#!/usr/bin/env python3
"""速卖通/SMT 类目属性解析与补全（纯离线单测，不联网、不写商品）。

覆盖 services/smt_attributes.py 的解析、映射、补全、合并、缺失检测与报告，
以及 text_pipeline.suggest_attributes 对店小秘 values JSON 的兼容。
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone.services import smt_attributes as SA  # noqa: E402
from aeclone.services.text_pipeline import TextPipeline  # noqa: E402


# ---------------------------------------------------------------- 真实格式样本

#: /api/smtCategory/attributeList.json 的真实返回（categoryId=100007204 截取）
CATEGORY_ATTRS_JSON = json.dumps([
    {
        "id": 26691, "categoryId": "100007204", "spec": 0, "visible": 1,
        "arrtNameId": "2", "nameZh": "品牌", "nameEn": "Brand Name",
        "inputType": "STRING", "attributeShowTypeValue": "list_box",
        "supportEnumInput": 0, "required": 1, "sku": 0,
        "values": json.dumps([{"names": {"en": "NONE", "zh": "NONE"},
                               "valueTags": "{}", "id": "201512802", "hasSubAttr": 0}]),
    },
    {
        "id": 84630, "categoryId": "100007204", "visible": 1,
        "arrtNameId": "219", "nameZh": "产地（国家或地区）", "nameEn": "Origin",
        "inputType": "STRING", "attributeShowTypeValue": "list_box",
        "supportEnumInput": 0, "required": 1, "sku": 0,
        "values": json.dumps([
            {"names": {"en": "Mainland China", "zh": "中国大陆(Origin)"}, "id": "9441741844"},
            {"names": {"en": "De(origin)", "zh": "德国(Origin)"}, "id": "9441845303"},
        ]),
    },
    {
        "id": 86000, "categoryId": "100007204", "visible": 1,
        "arrtNameId": "400074001", "nameZh": "是否带电", "nameEn": "Charge Status",
        "inputType": "STRING", "attributeShowTypeValue": "list_box",
        "supportEnumInput": 0, "required": 1, "sku": 0,
        "values": json.dumps([
            {"names": {"en": "Yes", "zh": "是"}, "id": "24097551176"},
            {"names": {"en": "No", "zh": "否"}, "id": "23399554847"},
        ]),
    },
    {
        "id": 86100, "categoryId": "100007204", "visible": 1,
        "arrtNameId": "400000603", "nameZh": "高关注化学品", "nameEn": "High-concerned chemical",
        "inputType": "STRING", "attributeShowTypeValue": "check_box",
        "supportEnumInput": 0, "required": 1, "sku": 0,
        "values": json.dumps([
            {"names": {"en": "None", "zh": "无"}, "id": "23399591357"},
            {"names": {"en": "Acetone", "zh": "丙酮"}, "id": "1981197668"},
        ]),
    },
    {
        "id": 87000, "categoryId": "100007204", "visible": 1,
        "arrtNameId": "3", "nameZh": "型号", "nameEn": "Model Number",
        "inputType": "STRING", "attributeShowTypeValue": "input",
        "supportEnumInput": 0, "required": 0, "sku": 0, "values": None,
    },
    {
        "id": 88000, "categoryId": "100007204", "visible": 1,
        "arrtNameId": "14", "nameZh": "颜色", "nameEn": "Color",
        "inputType": "STRING", "attributeShowTypeValue": "check_box",
        "supportEnumInput": 0, "required": 0, "sku": 1,
        "values": json.dumps([{"names": {"en": "Brown", "zh": "棕色"}, "id": "350686"}]),
    },
], ensure_ascii=False)

CATEGORY_ATTRS = SA.parse_category_attributes(CATEGORY_ATTRS_JSON)

#: 模板商品（店主在「连接/模板」里选的 onSelling 商品）的系统属性
TEMPLATE_PROPS = SA.parse_product_attributes([
    {"attrName": "Brand Name", "attrNameId": 2, "attrValue": "NONE", "attrValueId": 201512802},
    {"attrName": "Origin", "attrNameId": 219, "attrValue": "Mainland China",
     "attrValueId": 9441741844},
    {"attrName": "Charge Status", "attrNameId": 400074001, "attrValue": "No",
     "attrValueId": 23399554847},
    {"attrName": "High-concerned chemical", "attrNameId": 400000603, "attrValue": "None",
     "attrValueId": 23399591357},
    {"attrName": "Power Type", "attrNameId": -1, "attrValue": "Gasoline", "attrValueId": None},
])


def _draft_product(**overrides):
    """真实草稿形态：只有自定义属性（无 attrNameId/attrValueId），1 个 SKU。"""
    product = {
        "id": "220114600917618092",
        "subject": "Underground trenchless horizontal directional drilling rig",
        "categoryId": "100007204",
        "aeopAeProductPropertys": json.dumps([
            {"attrName": "Warranty", "attrValue": "1 Year"},
            {"attrName": "Place of Origin", "attrValue": "Henan, China"},
            {"attrName": "Product name", "attrValue": "HDD drilling rig"},
            {"attrName": "Weight (kg)", "attrValue": "8800"},
        ], ensure_ascii=False),
        "aeopAeProductSKUs": json.dumps([{
            "skuCode": "107769519454", "skuPrice": "125128.29",
            "aeopSKUProperty": [{"skuPropertyId": "200007763", "propertyValueId": 5580,
                                 "propertyValueDefinitionName": "YG-GS150"}],
        }], ensure_ascii=False),
    }
    product.update(overrides)
    return product


def _good_product():
    return {
        "id": "220114600917618292",
        "categoryId": "100007204",
        "aeopAeProductPropertys": json.dumps([
            {"attrName": "Brand Name", "attrNameId": 2, "attrValue": "NONE",
             "attrValueId": 201512802},
            {"attrName": "Origin", "attrNameId": 219, "attrValue": "Mainland China",
             "attrValueId": 9441741844},
            {"attrName": "Charge Status", "attrNameId": 400074001, "attrValue": "No",
             "attrValueId": 23399554847},
            {"attrName": "High-concerned chemical", "attrNameId": 400000603,
             "attrValue": "None", "attrValueId": 23399591357},
            {"attrName": "Power Type", "attrValue": "Gasoline"},
        ], ensure_ascii=False),
        "aeopAeProductSKUs": json.dumps([{
            "skuCode": "A-1",
            "aeopSKUProperty": [{"skuPropertyId": "14", "propertyValueId": 350686}],
        }], ensure_ascii=False),
    }


class FakeChat:
    """模拟 ApiClient.chat 的签名（离线）。"""

    def __init__(self, reply):
        self.reply = reply
        self.calls = []

    def __call__(self, messages, max_tokens=0, **kwargs):
        self.calls.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class FakeCfg:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class FakeTextClient:
    def __init__(self, reply):
        self.reply = reply
        self.messages = []

    def chat(self, model, messages, **kwargs):
        self.messages.append(messages)
        return self.reply


# ---------------------------------------------------------------- 解析


class ParseTests(unittest.TestCase):
    def test_parse_category_attributes_real_payload(self):
        self.assertEqual(len(CATEGORY_ATTRS), 6)
        brand = CATEGORY_ATTRS[0]
        self.assertEqual(brand.name_id, "2")
        self.assertEqual(brand.name_zh, "品牌")
        self.assertEqual(brand.name_en, "Brand Name")
        self.assertTrue(brand.required)
        self.assertFalse(brand.sku)
        self.assertEqual(len(brand.values), 1)
        self.assertEqual(brand.values[0].id, "201512802")
        self.assertEqual(brand.value_by_name("none").id, "201512802")
        color = [a for a in CATEGORY_ATTRS if a.name_id == "14"][0]
        self.assertTrue(color.sku)
        self.assertEqual(color.value_by_id("350686").name_en, "Brown")

    def test_parse_category_attributes_wrappers_and_bad_values(self):
        self.assertEqual(SA.parse_category_attributes(None), [])
        self.assertEqual(SA.parse_category_attributes("not-json"), [])
        wrapped = {"code": 0, "data": [{"arrtNameId": "9", "nameEn": "Foo",
                                        "required": 1, "values": "{broken"}]}
        parsed = SA.parse_category_attributes(wrapped)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].name_id, "9")
        self.assertEqual(parsed[0].values, ())

    def test_normalize_name_aliases(self):
        self.assertEqual(SA.normalize_name("品牌"), "brand name")
        self.assertEqual(SA.normalize_name("产地（国家或地区）"), "origin")
        self.assertEqual(SA.normalize_name("Place of Origin"), "origin")
        self.assertEqual(SA.normalize_name("Brands"), "brand name")
        self.assertEqual(SA.normalize_name("Product Weight (kg)"), "weight")
        self.assertEqual(SA.normalize_name("高关注化学品"), "high-concerned chemical")
        self.assertEqual(SA.normalize_name("是否带电"), "charge status")

    def test_parse_product_attributes_dirty_ids(self):
        attrs = SA.parse_product_attributes([
            {"attrName": "Brand Name", "attrNameId": -1, "attrValue": "NONE",
             "attrValueId": None},
            {"attrName": "Origin", "attrNameId": 219, "attrValue": "Mainland China",
             "attrValueId": "9441741844"},
            {"attrName": None, "attrNameId": "400000603", "attrValue": "None",
             "attrValueId": "23399591357"},
            {"attrName": "Ignore", "attrValue": "None", "attrValueId": "None"},
        ])
        self.assertEqual(len(attrs), 4)
        self.assertFalse(attrs[0].has_valid_ids)          # -1/None → 无效
        self.assertEqual(attrs[0].name, "Brand Name")     # 名称仍在，可映射
        self.assertTrue(attrs[1].has_valid_ids)
        self.assertTrue(attrs[2].has_valid_ids)
        self.assertEqual(attrs[2].name, "")               # 有 id 无名称
        self.assertEqual(attrs[3].value, "None")          # 原始值保留，供枚举匹配

    def test_parse_skus_and_property_ids(self):
        skus = SA.parse_skus(_draft_product()["aeopAeProductSKUs"])
        self.assertEqual(len(skus), 1)
        self.assertEqual(SA.sku_property_ids(skus), {"200007763"})
        self.assertEqual(SA.parse_skus("[]"), [])

    def test_forecast_category_candidates(self):
        product = {"id": "1", "forecastCategoryId": "202216043;202181206;202216235"}
        self.assertEqual(SA.forecast_category_candidates(product),
                         ["202216043", "202181206", "202216235"])
        self.assertEqual(SA.suggest_category_id(product), "202216043")
        self.assertEqual(SA.forecast_category_candidates({}), [])


# ---------------------------------------------------------------- 填充


class FillTests(unittest.TestCase):
    def test_fill_draft_from_template_and_defaults(self):
        product = _draft_product()
        attrs = SA.parse_product_attributes(product["aeopAeProductPropertys"])
        result = SA.fill_attributes(CATEGORY_ATTRS, attrs,
                                    template_props=TEMPLATE_PROPS,
                                    defaults=SA.universal_defaults(CATEGORY_ATTRS))
        # 4 个必填全补上（模板/默认），没有 missing
        self.assertTrue(result.ok, result.missing)
        self.assertEqual(result.stats["required_total"], 4)
        self.assertEqual(result.stats["required_filled"], 4)
        # targets 是 {英文属性名: 值}
        self.assertEqual(result.targets["Brand Name"], "NONE")
        self.assertEqual(result.targets["Origin"], "Mainland China")
        self.assertEqual(result.targets["Charge Status"], "No")
        self.assertEqual(result.targets["High-concerned chemical"], "None")
        # 系统属性都带真实 id
        ids = {prop["attrNameId"]: prop["attrValueId"] for prop in result.system_props}
        self.assertEqual(ids["2"], "201512802")
        self.assertEqual(ids["219"], "9441741844")
        # 来源可追溯
        self.assertIn("template", result.stats["sources"])
        self.assertEqual(result.missing, [])

    def test_fill_product_text_backfills_enum_ids(self):
        product = _draft_product()
        product["aeopAeProductPropertys"] = json.dumps([
            {"attrName": "Brand Name", "attrValue": "NONE"},
            {"attrName": "Place of Origin", "attrValue": "Mainland China"},
            {"attrName": "Charge Status", "attrValue": "No"},
            {"attrName": "High-concerned chemical", "attrValue": "None"},
        ], ensure_ascii=False)
        attrs = SA.parse_product_attributes(product["aeopAeProductPropertys"])
        result = SA.fill_attributes(CATEGORY_ATTRS, attrs)
        self.assertTrue(result.ok, result.missing)
        self.assertIn("product_text", result.stats["sources"])
        self.assertEqual(result.stats["sources"].get("product_text"), 4)
        self.assertEqual({p["attrNameId"] for p in result.system_props},
                         {"2", "219", "400074001", "400000603"})

    def test_fill_reports_unmappable_required_value(self):
        # Place of Origin=Henan, China 不是国家枚举；没有模板/默认时必须报缺失
        product = _draft_product()
        product["aeopAeProductPropertys"] = json.dumps([
            {"attrName": "Place of Origin", "attrValue": "Henan, China"},
        ], ensure_ascii=False)
        attrs = SA.parse_product_attributes(product["aeopAeProductPropertys"])
        result = SA.fill_attributes(CATEGORY_ATTRS, attrs)
        self.assertFalse(result.ok)
        names = {item["name"] for item in result.missing}
        self.assertIn("Origin", names)
        origin_missing = [item for item in result.missing if item["name"] == "Origin"][0]
        self.assertIn("不在类目可选值中", origin_missing["reason"])
        self.assertEqual(origin_missing["matched_value"], "Henan, China")

    def test_fill_free_text_attribute_keeps_custom_prop(self):
        attrs = SA.parse_product_attributes([
            {"attrName": "Model Number", "attrValue": "YG-GS150"},
        ])
        result = SA.fill_attributes(CATEGORY_ATTRS, attrs, include_optional=True)
        # Model Number 是 input 类型，允许自定义属性；必填 4 项仍缺
        model = [item for item in result.attributes if item["name"] == "Model Number"]
        self.assertEqual(len(model), 1)
        self.assertEqual(model[0]["status"], "filled")
        self.assertEqual(model[0]["source"], "product_free")
        self.assertEqual(result.stats["filled_custom"], 1)

    def test_fill_optional_missing_not_in_missing_list(self):
        attrs = SA.parse_product_attributes([])
        result = SA.fill_attributes(CATEGORY_ATTRS, attrs)
        optional = {item["name"] for item in result.unresolved_optional}
        self.assertIn("Model Number", optional)
        self.assertTrue(all(item["required"] for item in result.missing))

    def test_universal_defaults_only_use_real_enum_values(self):
        defaults = SA.universal_defaults(CATEGORY_ATTRS)
        self.assertEqual(defaults["2"]["value_id"], "201512802")
        self.assertEqual(defaults["219"]["value_id"], "9441741844")
        self.assertEqual(defaults["400074001"]["value_id"], "23399554847")
        self.assertEqual(defaults["400000603"]["value_id"], "23399591357")
        # Color 没有通用默认，不应编造
        self.assertNotIn("14", defaults)

    def test_template_defaults_skip_invalid_ids(self):
        defaults = SA.template_defaults(TEMPLATE_PROPS)
        self.assertEqual({item.name_id for item in defaults},
                         {"2", "219", "400074001", "400000603"})


# ---------------------------------------------------------------- AI 兜底


class AiFallbackTests(unittest.TestCase):
    def _missing_origin(self):
        attrs = SA.parse_product_attributes([])
        fill = SA.fill_attributes(CATEGORY_ATTRS, attrs)
        return fill

    def test_build_ai_prompt_lists_allowed_values(self):
        fill = self._missing_origin()
        prompt = SA.build_ai_prompt(fill.missing, CATEGORY_ATTRS,
                                    title="HDD drilling rig", detail="heavy duty")
        self.assertIn("Brand Name", prompt)
        self.assertIn("NONE", prompt)
        self.assertIn("Mainland China", prompt)
        self.assertIn("HDD drilling rig", prompt)

    def test_parse_ai_suggestions_validates_enum(self):
        raw = json.dumps({"Brand Name": "NONE", "Origin": "Mainland China",
                          "Charge Status": "Maybe", "Bogus": "x"})
        got = SA.parse_ai_suggestions(raw, CATEGORY_ATTRS)
        self.assertEqual(got["2"]["value_id"], "201512802")
        self.assertEqual(got["219"]["value_id"], "9441741844")
        self.assertNotIn("400074001", got)   # Maybe 不在枚举里，必须丢弃
        self.assertEqual(len(got), 2)

    def test_ai_suggest_missing_and_apply(self):
        fill = self._missing_origin()
        chat = FakeChat(json.dumps({"Brand Name": "NONE", "Origin": "Mainland China",
                                    "Charge Status": "No",
                                    "High-concerned chemical": "None"}))
        suggestions = SA.ai_suggest_missing(fill.missing, CATEGORY_ATTRS, chat,
                                            title="t", detail="d")
        self.assertEqual(len(suggestions), 4)
        self.assertEqual(len(chat.calls), 1)               # 只调用一次
        SA.apply_ai_suggestions(fill, CATEGORY_ATTRS, suggestions)
        self.assertTrue(fill.ok, fill.missing)
        self.assertEqual(fill.stats["sources"].get("ai"), 4)

    def test_ai_suggest_never_blocks(self):
        fill = self._missing_origin()
        self.assertEqual(SA.ai_suggest_missing(fill.missing, CATEGORY_ATTRS,
                                               FakeChat(RuntimeError("boom"))), {})
        self.assertEqual(SA.ai_suggest_missing(fill.missing, CATEGORY_ATTRS, None), {})
        self.assertEqual(SA.ai_suggest_missing([], CATEGORY_ATTRS,
                                               FakeChat("{}")), {})

    def test_text_pipeline_smt_values_json(self):
        client = FakeTextClient(json.dumps({"Brand Name": "NONE"}))
        tp = TextPipeline.__new__(TextPipeline)
        tp.cfg = FakeCfg()
        tp.client = client
        result = tp.suggest_attributes(
            [{"nameEn": "Brand Name", "values": CATEGORY_ATTRS[0].raw["values"]},
             {"nameZh": "产地（国家或地区）",
              "values": CATEGORY_ATTRS[1].raw["values"]}],
            title="HDD rig", platform="smt")
        self.assertTrue(result.ok, result.error)
        prompt = "\n".join(str(msg["content"]) for msg in client.messages[0])
        self.assertIn("Mainland China", prompt)       # 嵌套 names.en 被解析
        self.assertIn("AliExpress", prompt)           # smt 口径提示词
        self.assertIn("NONE", prompt)


# ---------------------------------------------------------------- 合并


class MergeTests(unittest.TestCase):
    def _fill(self):
        attrs = SA.parse_product_attributes(_draft_product()["aeopAeProductPropertys"])
        return SA.fill_attributes(CATEGORY_ATTRS, attrs, template_props=TEMPLATE_PROPS,
                                  defaults=SA.universal_defaults(CATEGORY_ATTRS))

    def test_merge_adds_system_props_and_drops_duplicate_custom(self):
        product = _draft_product()
        product["aeopAeProductPropertys"] = json.dumps([
            {"attrName": "Brand Name", "attrValue": "NONE"},   # 同名自定义 → 应删除
            {"attrName": "Warranty", "attrValue": "1 Year"},   # 无关自定义 → 保留
        ], ensure_ascii=False)
        merged = SA.merge_attributes(product["aeopAeProductPropertys"], self._fill())
        names = [item.get("attrName") for item in merged.props]
        self.assertEqual(names.count("Brand Name"), 1)
        self.assertIn("Warranty", names)
        # 补进来的系统属性带 id
        system_ids = {item["attrNameId"] for item in merged.props if item.get("attrNameId")}
        self.assertEqual(system_ids, {"2", "219", "400074001", "400000603"})
        self.assertTrue(any(item["reason"] == "与类目系统属性同名，改由系统属性提交"
                            for item in merged.removed))
        # 输出是合法 JSON，且 to_submit_props 能原样保留系统属性
        parsed = json.loads(merged.json)
        self.assertEqual(len(parsed), len(merged.props))
        from aeclone.services.api_save import to_submit_props
        submitted = to_submit_props({"aeopAeProductPropertys": merged.json})
        submitted_ids = {item["attrNameId"] for item in submitted if "attrNameId" in item}
        self.assertEqual(submitted_ids, {"2", "219", "400074001", "400000603"})
        self.assertEqual(len(to_submit_props({"aeopAeProductPropertys": merged.json})), 5)

    def test_merge_is_idempotent(self):
        product = _draft_product()
        fill = self._fill()
        first = SA.merge_attributes(product["aeopAeProductPropertys"], fill)
        second = SA.merge_attributes(first.json, fill)
        self.assertEqual(first.props, second.props)
        self.assertEqual(second.added, [])

    def test_merge_backfills_missing_attr_name(self):
        # 编辑中的商品：有 id 没 attrName（真实数据常见）
        existing = json.dumps([
            {"attrName": None, "attrNameId": "2", "attrValue": None, "attrValueId": "201512802"},
            {"attrName": None, "attrNameId": "219", "attrValue": None, "attrValueId": "9441741844"},
        ])
        merged = SA.merge_attributes(existing, self._fill())
        by_id = {item["attrNameId"]: item for item in merged.props}
        self.assertEqual(by_id["2"]["attrName"], "Brand Name")
        self.assertEqual(by_id["219"]["attrName"], "Origin")
        self.assertEqual(len(merged.renamed), 2)

    def test_merge_respects_drop_names(self):
        existing = json.dumps([{"attrName": "Warranty", "attrValue": "1 Year"},
                               {"attrName": "Power Type", "attrValue": "Gasoline"}])
        merged = SA.merge_attributes(existing, SA.fill_attributes([], []),
                                     drop_names=["保修", "Warranty"])
        names = [item.get("attrName") for item in merged.props]
        self.assertNotIn("Warranty", names)
        self.assertIn("Power Type", names)

    def test_merge_keeps_original_raw_fields(self):
        existing = json.dumps([{"attrName": "Origin", "attrNameId": "219",
                                "attrValue": "Mainland China",
                                "attrValueId": "9441741844",
                                "attrValueUnit": "country"}])
        merged = SA.merge_attributes(existing, SA.fill_attributes([], []))
        self.assertEqual(merged.props[0]["attrValueUnit"], "country")

    def test_payload_helper(self):
        merged = SA.merge_attributes(_draft_product()["aeopAeProductPropertys"], self._fill())
        payload = merged.payload()
        self.assertIn("aeopAeProductPropertys", payload)
        json.loads(payload["aeopAeProductPropertys"])


# ---------------------------------------------------------------- 缺失检测


class RepairHelperTests(unittest.TestCase):
    def test_repair_product_attributes_one_call(self):
        out = SA.repair_product_attributes(
            _draft_product(), CATEGORY_ATTRS,
            template_props=TEMPLATE_PROPS,
            defaults=SA.universal_defaults(CATEGORY_ATTRS),
            template_skus=[])
        self.assertEqual(out["category_id"], "100007204")
        self.assertEqual(out["missing"], [])                      # 必填全补上
        self.assertEqual(out["before"]["system_attributes"], 0)
        self.assertEqual(out["after"]["system_attributes"], 4)
        self.assertEqual(out["merge"].stats["added"], 4)
        props = json.loads(out["payload"]["aeopAeProductPropertys"])
        self.assertEqual({item["attrNameId"] for item in props if "attrNameId" in item},
                         {"2", "219", "400074001", "400000603"})
        # 检测层仍如实报告草稿的缺失类型（含模板未套用）
        kinds = {item["type"] for item in out["issues"]}
        self.assertIn(SA.MISSING_TEMPLATE_NOT_APPLIED, kinds)

    def test_repair_product_attributes_without_category(self):
        out = SA.repair_product_attributes(
            {"id": "X", "categoryId": None, "forecastCategoryId": "202181206",
             "aeopAeProductPropertys": "[]"}, [])
        self.assertEqual(out["issues"][0]["type"], SA.MISSING_NO_CATEGORY)
        self.assertEqual(out["payload"]["aeopAeProductPropertys"], "[]")


class DetectMissingTests(unittest.TestCase):
    def test_no_category_with_forecast_candidates(self):
        product = {"id": "1", "categoryId": None,
                   "forecastCategoryId": "202181206;202216043",
                   "aeopAeProductPropertys": "[]"}
        issues = SA.detect_missing(product, [])
        self.assertEqual(issues[0]["type"], SA.MISSING_NO_CATEGORY)
        self.assertEqual(issues[0]["forecast_candidates"], ["202181206", "202216043"])
        self.assertIn("没有类目 ID", issues[0]["reason"])

    def test_required_no_value_and_custom_only(self):
        product = _draft_product()
        issues = SA.detect_missing(product, CATEGORY_ATTRS, template_props=TEMPLATE_PROPS)
        kinds = {item["type"] for item in issues}
        self.assertIn(SA.MISSING_REQUIRED_NO_VALUE, kinds)
        self.assertIn(SA.MISSING_VALUE_ID, kinds)
        self.assertIn(SA.MISSING_TEMPLATE_NOT_APPLIED, kinds)
        required = [item for item in issues
                    if item["type"] == SA.MISSING_REQUIRED_NO_VALUE
                    and item.get("name") == "Brand Name"]
        self.assertEqual(len(required), 1)
        self.assertIn("完全没有对应项", required[0]["reason"])

    def test_value_not_in_enum(self):
        product = _draft_product()
        product["aeopAeProductPropertys"] = json.dumps([
            {"attrName": "Origin", "attrValue": "Henan, China"},
        ], ensure_ascii=False)
        issues = SA.detect_missing(product, CATEGORY_ATTRS)
        bad = [item for item in issues if item.get("detail") == "value_not_in_enum"]
        self.assertTrue(bad)
        self.assertIn("不是类目枚举值", bad[0]["reason"])

    def test_good_product_has_no_missing(self):
        product = _good_product()
        issues = SA.detect_missing(product, CATEGORY_ATTRS, template_props=TEMPLATE_PROPS)
        self.assertEqual(issues, [])

    def test_missing_name_detected(self):
        product = {"id": "1", "categoryId": "100007204",
                   "aeopAeProductPropertys": json.dumps([
                       {"attrName": None, "attrNameId": "2", "attrValue": "NONE",
                        "attrValueId": "201512802"}])}
        issues = SA.detect_missing(product, CATEGORY_ATTRS)
        self.assertTrue(any(item.get("detail") == "missing_name" for item in issues))

    def test_sku_mismatch_unknown_id(self):
        product = _draft_product()
        product["aeopAeProductSKUs"] = json.dumps([{
            "skuCode": "A-1",
            "aeopSKUProperty": [{"skuPropertyId": "999999", "propertyValueId": 1}],
        }])
        issues = SA.detect_missing(product, CATEGORY_ATTRS)
        sku = [item for item in issues if item["type"] == SA.MISSING_SKU_MISMATCH]
        self.assertTrue(sku)
        self.assertIn("999999", sku[0]["reason"])

    def test_sku_duplicate_combination(self):
        skus = [{"skuCode": "A", "aeopSKUProperty": [
                    {"skuPropertyId": "14", "propertyValueId": 350686}]},
                {"skuCode": "B", "aeopSKUProperty": [
                    {"skuPropertyId": "14", "propertyValueId": 350686}]}]
        issues = SA.sku_attribute_issues(CATEGORY_ATTRS, skus)
        self.assertTrue(any(item["detail"] == "duplicate_sku_combination" for item in issues))

    def test_sku_plug_parent_is_not_a_dimension(self):
        # 真实模板（onSelling）SKU：plug 库属性 400079803 + plug_parent 400081003；
        # plug_parent 不是 SKU 维度，不能因为不在 attributeList 里就误报。
        template_skus = [{"aeopSKUProperty": [
            {"skuPropertyId": 400079803, "propertyValueId": 1983474624,
             "plug_parent_property_id": 400081003}]}]
        skus = [{"skuCode": "A", "aeopSKUProperty": [
            {"skuPropertyId": "14", "propertyValueId": 350686},
            {"skuPropertyId": 400079803, "propertyValueId": 1983474624,
             "plug_parent_property_id": 400081003}]}]
        self.assertEqual(SA.sku_property_ids(skus), {"14", "400079803"})
        self.assertEqual(
            SA.sku_property_ids(skus, include_plug_parents=True),
            {"14", "400079803", "400081003"})
        self.assertEqual(SA.sku_attribute_issues(CATEGORY_ATTRS, skus,
                                                 template_skus=template_skus), [])
        # 没有模板白名单时，plug 属性确实不在类目属性表里 → 报不匹配（保守）
        self.assertTrue(SA.sku_attribute_issues(CATEGORY_ATTRS, skus))

    def test_allowed_sku_property_ids(self):
        allowed = SA.allowed_sku_property_ids(
            CATEGORY_ATTRS,
            template_skus=[{"aeopSKUProperty": [
                {"skuPropertyId": 400079803, "plug_parent_property_id": 400081003}]}],
            extra_sku_property_ids=["999"])
        self.assertEqual(allowed, {"14", "400079803", "400081003", "999"})

    def test_sku_plug_property_allowed_by_extra_ids(self):
        skus = [{"skuCode": "A", "aeopSKUProperty": [
            {"skuPropertyId": "14", "propertyValueId": 350686},
            {"skuPropertyId": "400079803", "propertyValueId": 1983474624}]}]
        issues = SA.sku_attribute_issues(CATEGORY_ATTRS, skus,
                                        extra_sku_property_ids=["400079803"])
        self.assertEqual(issues, [])


# ---------------------------------------------------------------- 报告


class ReportTests(unittest.TestCase):
    def _products(self):
        draft = _draft_product()
        no_category = {"id": "NO-CAT-1", "categoryId": None,
                       "forecastCategoryId": "202181206;202216043",
                       "aeopAeProductPropertys": json.dumps([
                           {"attrName": "Brand Name", "attrValue": "NONE"},
                       ])}
        good = _good_product()
        return [draft, no_category, good]

    def test_build_missing_report_aggregates(self):
        report = SA.build_missing_report(
            self._products(),
            {"100007204": CATEGORY_ATTRS_JSON},
            template_props=TEMPLATE_PROPS,
            defaults=SA.universal_defaults(CATEGORY_ATTRS),
            suggested_categories={"NO-CAT-1": "202181206"})
        self.assertEqual(report["samples"], 3)
        self.assertEqual(report["with_category"], 2)
        self.assertEqual(report["no_category"], 1)
        # 无类目商品即使给了建议类目，也仍然记录「无类目」缺失
        self.assertEqual(report["missing_types"][SA.MISSING_NO_CATEGORY], 1)
        self.assertGreaterEqual(report["missing_types"][SA.MISSING_REQUIRED_NO_VALUE], 1)
        self.assertGreaterEqual(report["missing_types"][SA.MISSING_TEMPLATE_NOT_APPLIED], 1)
        # 可自动补：草稿 4 项必填全部由模板/默认补上
        required = report["required_attributes"]
        self.assertGreater(required["before_missing"], 0)
        self.assertGreater(required["filled_by_repair"], 0)
        self.assertGreater(required["auto_fill_ratio"], 0)
        self.assertLessEqual(required["missing_after"], required["before_missing"] + 4)
        # 每个商品都有修复前后结构
        for record in report["products"]:
            self.assertIn("missing", record)
            self.assertIn("required", record)
            self.assertIn("merge", record)
            self.assertIn("props_after", record["merge"])
        # 报告可 JSON 序列化
        json.dumps(report, ensure_ascii=False)

    def test_report_uses_effective_category_from_suggestion(self):
        report = SA.build_missing_report(
            [{"id": "X", "categoryId": None, "forecastCategoryId": "202181206"}],
            {"202181206": json.dumps([])},
            suggested_categories={"X": "202181206"})
        record = report["products"][0]
        self.assertEqual(record["suggested_category_id"], "202181206")
        self.assertEqual(record["effective_category_id"], "202181206")

    def test_report_ai_fallback_only_first_n(self):
        products = [_draft_product(id="D1"), _draft_product(id="D2")]
        chat = FakeChat(json.dumps({"Brand Name": "NONE", "Origin": "Mainland China",
                                    "Charge Status": "No",
                                    "High-concerned chemical": "None"}))
        report = SA.build_missing_report(
            products, {"100007204": CATEGORY_ATTRS_JSON},
            ai_chat=chat, ai_products=1)
        self.assertEqual(report["ai_fallback"]["products_called"], 1)
        self.assertEqual(len(chat.calls), 1)
        used = [record for record in report["products"] if record["ai"]["used"]]
        self.assertEqual(len(used), 1)
        self.assertEqual(used[0]["ai"]["missing_before"], 4)
        self.assertEqual(used[0]["ai"]["missing_after"], 0)
        self.assertEqual(len(used[0]["ai"]["suggestions"]), 4)

    def test_empty_report(self):
        report = SA.build_missing_report([], {})
        self.assertEqual(report["samples"], 0)
        self.assertEqual(report["missing_types"][SA.MISSING_NO_CATEGORY], 0)
        self.assertEqual(report["required_attributes"]["auto_fill_ratio"], 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
