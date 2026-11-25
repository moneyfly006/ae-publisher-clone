"""Amazon 缺失必填属性自动补齐 / AI 兜底建议（离线单测，无网络、绝不真实保存）。

覆盖 task-6 验收点：
1. ``build_amazon_payload`` / ``prepare_save`` 对缺失的 4 个发布必填属性自动补齐进
   ``payload.productAttr``（只补缺失、不覆盖已有；``cfg.amazon_autofill_attrs=False`` 可关）；
2. 确定性映射：SKU/标题型号/商品属性 → model_number / model_name / part_number；
   manufacturer_contact_information 使用源商品或实际配置的联系方式；
3. 扩充映射：item_name / product_description / bullet_point / generic_keyword /
   brand / manufacturer / item_package_quantity / country_of_origin 等；
4. ``ai_suggest_attributes`` AI 兜底（mock）：逐字段隔离失败、跳过结构敏感属性、
   不写平台；``run_amazon_product(use_ai=True)`` 才把建议并入保存包；
5. op=1 保存草稿也补齐（不再只在发布时补），且 dry-run 不写平台。

跑法：``AURORA_TEST=1 .venv/bin/python tests/test_amazon_attributes.py``
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.config import AppConfig
from aeclone.services import amazon_pipeline as PL
from aeclone.services.amazon_payload import (
    REQUIRED_COMMON, attribute_present, autofill_enabled, build_amazon_payload,
    build_warnings, missing_common_attributes, parse_product_attr,
    required_attribute_names,
)
from aeclone.services.amazon_pipeline import (
    OP_SAVE, ai_suggest_attributes, autofill_attributes, prepare_save,
    run_amazon_product,
)
from aeclone.services.amazon_prompts import (
    build_attributes_prompt, parse_attribute_suggestion,
)

ENV_KEYS = ("AURORA_NO_SAVE", "AURORA_TEST", "AURORA_SKIP_LICENSE")
SITE = "A2EUQ1WTGCTBG2"   # CA


def make_product(**over):
    """真实采集箱草稿形态：4 个发布必填属性缺失。"""
    product = {
        "id": "220114595363181900",
        "shopId": "9408005",
        "marketPlaceId": SITE,
        "currency": "CAD",
        "title": "4X4 Wheel Loader Backhoe 85KW Diesel Excavator",
        "titleDifferentiation": None,
        "categoryId": "13553005011",
        "categoryBrowsePath": "6299024011/6256963011/6256964011/13553005011",
        "audience": "all",
        "isVariation": 0,
        "brandSettledType": "external_product_id",
        "fullCid": "5405448-",
        "sourceUrl": "https://www.alibaba.com/product-detail/x_1.html",
        "standardProductType": "upc",
        "standardProductId": None,
        "sku": "WY-QHCA-LTM-6",
        "brand": "Generic",
        "manufacturer": "Generic",
        "productAttr": json.dumps({
            "is_assembly_required": [{"value": False, "marketplace_id": SITE}],
            "batteries_required": [{"value": False, "marketplace_id": SITE}],
        }),
        "standardPrice": 15999.0,
        "saleSalePrice": None,
        "standardPriceCurrency": "CAD",
        "saleStartDateStr": None,
        "saleEndDateStr": None,
        "quantity": 3,
        "conditionType": None,
        "conditionValue": "",
        "mainImage": "https://sc04.alicdn.com/kf/main.png",
        "extraImages": ["https://sc04.alicdn.com/kf/a.png"],
        "extraImagesStr": "https://sc04.alicdn.com/kf/a.png",
        "draftImgUrl": "https://sc04.alicdn.com/kf/a.png",
        "variationTheme": "",
        "skuTitle": 0,
        "description": "<p>Heavy duty backhoe loader for road construction.</p>",
        "bulletPoint": "±".join([
            "Diesel engine for heavy duty construction work",
            "4x4 wheel drive for rough terrain",
            "High load moment for road projects",
            "Hydraulic system for precise operation",
            "Low fuel consumption and easy maintenance",
        ]),
        "gpsrManufacturerReference": "",
        "dsaResponsiblePartyAddress": "",
        "gpsrSafetyAttestation": 0,
        "switchFulfillmentTo": "MFN",
        "merchantShippingGroup": "legacy-template-id",
        "searchTerms": "backhoe loader diesel construction machine",
    }
    product.update(over)
    return product


def make_detail(**over):
    return {"site": "CA", "product": make_product(**over), "categoryName": "反铲",
            "productType": "OUTDOOR_LIVING", "categoryTree": "[]"}


class FakeClient:
    """离线替身：读写都不联网；save_product 只记录（不真实保存）。"""

    def __init__(self, detail=None, template=None):
        self.detail = detail if detail is not None else make_detail()
        self.saved = []
        self.template = template if isinstance(template, dict) else {}
        self.template_queries = []

    def product_detail(self, pid):
        return self.detail

    def currency(self, shop_id, marketplace_id):
        return "CAD"

    def check_banned_words(self, **kwargs):
        return []

    def save_product(self, payload, op="1"):
        self.saved.append((dict(payload), str(op)))
        return {"code": 0, "msg": "Successful", "data": {"id": payload.get("id")}}

    def attribute_template(self, category_id, marketplace_id, shop_id="-1"):
        self.template_queries.append((str(category_id), str(marketplace_id)))
        return dict(self.template)


class EnvIsolatedCase(unittest.TestCase):
    def setUp(self):
        self._saved_env = {k: os.environ.pop(k, None) for k in ENV_KEYS}

    def tearDown(self):
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _attr(payload, key):
    return parse_product_attr(payload.get("productAttr")).get(key)


def _attr_value(payload, key):
    items = _attr(payload, key) or []
    if not items or not isinstance(items[0], dict):
        return None
    return items[0].get("value")


class PayloadAutofillTests(unittest.TestCase):
    """保存包阶段自动补齐（默认开）。"""

    def test_default_build_reports_missing_contact(self):
        payload = build_amazon_payload(make_product(), AppConfig(), {}, currency="CAD")
        attr = parse_product_attr(payload["productAttr"])
        self.assertEqual(missing_common_attributes(payload["productAttr"]), ["manufacturer_contact_information"])
        self.assertEqual(attr["model_number"][0]["value"], "WY-QHCA-LTM-6")
        self.assertEqual(attr["part_number"][0]["value"], "WY-QHCA-LTM-6")
        self.assertEqual(attr["model_name"][0]["value"], "WY-QHCA-LTM-6")
        self.assertNotIn("manufacturer_contact_information", attr)
        # 新补的属性带站点语言/站点标识
        self.assertEqual(attr["model_number"][0]["language_tag"], "en_CA")
        self.assertEqual(attr["model_number"][0]["marketplace_id"], SITE)
        # 原有属性保留
        self.assertIs(attr["is_assembly_required"][0]["value"], False)

    def test_existing_attribute_is_not_overwritten(self):
        product = make_product(productAttr=json.dumps({
            "model_number": [{"value": "EXISTING-MODEL", "language_tag": "en_CA",
                              "marketplace_id": SITE}],
            "manufacturer_contact_information": [{"value": "ACME Ltd, CN",
                                                  "marketplace_id": SITE}],
        }))
        payload = build_amazon_payload(product, AppConfig(), {}, currency="CAD")
        self.assertEqual(_attr_value(payload, "model_number"), "EXISTING-MODEL")
        self.assertEqual(_attr_value(payload, "manufacturer_contact_information"),
                         "ACME Ltd, CN")
        # 缺的两个仍然补
        self.assertEqual(_attr_value(payload, "part_number"), "WY-QHCA-LTM-6")
        self.assertEqual(_attr_value(payload, "model_name"), "WY-QHCA-LTM-6")

    def test_override_attributes_win_and_missing_still_filled(self):
        payload = build_amazon_payload(
            make_product(), AppConfig(),
            {"attributes": {"model_number": "OVERRIDE-MODEL",
                            "part_number": "OVERRIDE-PN"}}, currency="CAD")
        self.assertEqual(_attr_value(payload, "model_number"), "OVERRIDE-MODEL")
        self.assertEqual(_attr_value(payload, "part_number"), "OVERRIDE-PN")
        self.assertIsNone(_attr_value(payload, "manufacturer_contact_information"))

    def test_complete_product_attr_is_written_back_verbatim(self):
        raw = json.dumps({key: [{"value": f"X-{key}", "marketplace_id": SITE}]
                          for key in REQUIRED_COMMON}, ensure_ascii=False)
        payload = build_amazon_payload(make_product(productAttr=raw),
                                       AppConfig(), {}, currency="CAD")
        self.assertEqual(payload["productAttr"], raw)

    def test_cfg_switch_off_keeps_missing(self):
        cfg = AppConfig()
        cfg.set("amazon_autofill_attrs", False)
        payload = build_amazon_payload(make_product(), cfg, {}, currency="CAD")
        self.assertEqual(set(missing_common_attributes(payload["productAttr"])),
                         set(REQUIRED_COMMON))
        self.assertTrue(any("发布必填属性缺失" in w
                            for w in build_warnings(payload)))
        # dict cfg / 各种「关」的写法
        for off in (False, 0, "0", "false", "off", "no"):
            self.assertFalse(autofill_enabled({"amazon_autofill_attrs": off}), off)
        self.assertTrue(autofill_enabled({}))
        self.assertTrue(autofill_enabled(None))

    def test_notes_out_param_reports_what_was_filled(self):
        notes = []
        build_amazon_payload(make_product(), AppConfig(), {}, currency="CAD",
                             notes=notes)
        text = "\n".join(notes)
        self.assertIn("自动补齐发布必填属性", text)
        self.assertIn("model_number", text)
        self.assertIn("manufacturer_contact_information", text)

    def test_model_from_title_then_product_field_then_sku(self):
        # 1) 标题里带型号（真实写 #1 的 HW20-28）
        product = make_product(title="5 Ton Diesel Wheeled Backhoe Loader 4x4 "
                                     "HW20-28 Heavy Duty Construction Machine")
        payload = build_amazon_payload(product, AppConfig(), {}, currency="CAD")
        self.assertEqual(_attr_value(payload, "model_number"), "HW20-28")
        # 2) 标题/SKU 都提不出 → 商品自带型号字段
        product2 = make_product(title="Heavy Duty Construction Machine",
                                sku="105430344183", modelNumber="AB-100")
        payload2 = build_amazon_payload(product2, AppConfig(), {}, currency="CAD")
        self.assertEqual(_attr_value(payload2, "model_number"), "AB-100")
        # 3) SKU 含字母型号（YX02-QH-XLZ-3）→ SKU 型号优先
        product3 = make_product(sku="YX02-QH-XLZ-3",
                                title="Lightweight Compact Water Well Drill Rig")
        payload3 = build_amazon_payload(product3, AppConfig(), {}, currency="CAD")
        self.assertEqual(_attr_value(payload3, "model_number"), "YX02-QH-XLZ-3")
        # 4) 纯数字 SKU 也补（不编造，直接用 SKU）
        product4 = make_product(sku="107465057539",
                                title="Efficient Portable Water Well Drill Rig")
        payload4 = build_amazon_payload(product4, AppConfig(), {}, currency="CAD")
        self.assertEqual(_attr_value(payload4, "model_number"), "107465057539")

    def test_manufacturer_contact_requires_source_or_cfg_override(self):
        # 默认「品牌/制造商, CN」
        product = make_product(brand="Hengwang", manufacturer="Hengwang Factory")
        payload = build_amazon_payload(product, AppConfig(), {}, currency="CAD")
        self.assertIsNone(_attr_value(payload, "manufacturer_contact_information"))
        # manufacturer 空 → 用品牌
        product2 = make_product(brand="Hengwang", manufacturer="")
        payload2 = build_amazon_payload(product2, AppConfig(), {}, currency="CAD")
        self.assertIsNone(_attr_value(payload2, "manufacturer_contact_information"))
        # cfg 显式覆盖优先
        cfg = AppConfig()
        cfg.set("amazon_manufacturer_contact", "ACME Ltd, Shenzhen, CN")
        payload3 = build_amazon_payload(make_product(), cfg, {}, currency="CAD")
        self.assertEqual(_attr_value(payload3, "manufacturer_contact_information"),
                         "ACME Ltd, Shenzhen, CN")

    def test_country_of_origin_requires_evidence(self):
        product = make_product(title="Wheeled Backhoe Loader Made in China")
        payload = build_amazon_payload(
            product, AppConfig(), {"required_attributes": ["country_of_origin"]},
            currency="CAD")
        self.assertEqual(_attr_value(payload, "country_of_origin"), "CN")
        filled, notes = PL._autofill_attributes_with_notes(
            make_product(title="Heavy Duty Backhoe Loader",
                         description="<p>Heavy duty machine.</p>"),
            AppConfig(), SITE, required=["country_of_origin"], template={})
        self.assertNotIn("country_of_origin", filled)
        self.assertTrue(any("需用户填写" in n for n in notes))

    def test_payload_mirror_attributes_and_package_quantity(self):
        required = ["item_name", "product_description", "bullet_point",
                    "generic_keyword", "brand", "manufacturer",
                    "item_package_quantity"]
        filled, notes = PL._autofill_attributes_with_notes(
            make_product(), AppConfig(), SITE, required=required, template={})
        self.assertEqual(filled["item_name"], make_product()["title"])
        self.assertIn("Heavy duty backhoe loader", filled["product_description"])
        self.assertIn("Diesel engine", filled["bullet_point"])
        self.assertEqual(filled["generic_keyword"],
                         "backhoe loader diesel construction machine")
        self.assertEqual(filled["brand"], "Generic")
        self.assertEqual(filled["manufacturer"], "Generic")
        self.assertNotIn("item_package_quantity", filled)
        self.assertTrue(any("item_package_quantity" in n for n in notes))

    def test_unfillable_and_no_evidence_attributes_go_to_notes(self):
        filled, notes = PL._autofill_attributes_with_notes(
            make_product(), AppConfig(), SITE,
            required=["some_unknown_attribute", "color",
                      "externally_assigned_product_identifier"], template={})
        self.assertEqual(filled, {})
        text = "\n".join(notes)
        self.assertIn("some_unknown_attribute", text)
        self.assertIn("无法自动补齐", text)
        self.assertIn("color", text)
        self.assertIn("externally_assigned_product_identifier", text)

    def test_required_attribute_names_priority(self):
        cfg = AppConfig()
        cfg.set("amazon_last_category_required", "model_number, part_number")
        self.assertEqual(required_attribute_names(cfg, {}), ["model_number", "part_number"])
        self.assertEqual(required_attribute_names(
            cfg, {"required_attributes": ["country_of_origin"]}), ["country_of_origin"])
        self.assertEqual(required_attribute_names({}, {}), list(REQUIRED_COMMON))


class PrepareSaveAutofillTests(EnvIsolatedCase):
    """prepare_save（op=1 保存前的只读准备路径）也补齐，并返回 warnings。"""

    def test_prepare_save_fills_payload_and_warns(self):
        fake = FakeClient(detail=make_detail())
        detail, payload, issues, warnings = prepare_save(
            "220114595363181900", AppConfig(), client=fake)
        self.assertEqual(issues, [])
        self.assertEqual(len(payload), 39)
        self.assertEqual(missing_common_attributes(payload["productAttr"]), ["manufacturer_contact_information"])
        self.assertEqual(_attr_value(payload, "model_number"), "WY-QHCA-LTM-6")
        self.assertIsNone(_attr_value(payload, "manufacturer_contact_information"))
        self.assertTrue(any("已自动补齐发布必填属性" in w for w in warnings), warnings)
        self.assertEqual(fake.saved, [])   # 只读，不写

    def test_prepare_save_cfg_off_keeps_missing_and_warns(self):
        cfg = AppConfig()
        cfg.set("amazon_autofill_attrs", False)
        fake = FakeClient(detail=make_detail())
        _, payload, issues, warnings = prepare_save(
            "220114595363181900", cfg, client=fake)
        self.assertEqual(issues, [])
        self.assertEqual(set(missing_common_attributes(payload["productAttr"])),
                         set(REQUIRED_COMMON))
        self.assertTrue(any("发布必填属性缺失" in w for w in warnings), warnings)
        self.assertTrue(any("发布必填属性仍缺失" in w for w in warnings), warnings)
        self.assertEqual(fake.saved, [])

    def test_prepare_save_keeps_existing_and_applies_notes(self):
        product = make_product(productAttr=json.dumps({
            "model_number": [{"value": "EXISTING", "marketplace_id": SITE}]}))
        fake = FakeClient(detail={"site": "CA", "product": product})
        _, payload, issues, warnings = prepare_save(
            "220114595363181900", AppConfig(), client=fake)
        self.assertEqual(issues, [])
        self.assertEqual(_attr_value(payload, "model_number"), "EXISTING")
        self.assertIsNotNone(_attr_value(payload, "part_number"))
        self.assertTrue(any("已自动补齐发布必填属性" in w for w in warnings))


class FakeAttributeApi:
    """AI 替身：按提示词里的属性名返回固定文本；可指定某些属性抛错。"""

    def __init__(self, replies=None, fail=()):
        self.calls = []
        self.replies = dict(replies or {})
        self.fail = set(fail)

    def chat(self, model, messages, max_tokens=2048):
        content = messages[0]["content"]
        self.calls.append(content)
        for key in self.replies:
            if f"`{key}`" in content:
                if key in self.fail:
                    raise RuntimeError("模型超时")
                return self.replies[key]
        if "`material`" in content and "material" in self.fail:
            raise RuntimeError("模型超时")
        return "N/A"


class AiSuggestAttributeTests(EnvIsolatedCase):
    """AI 兜底建议：逐字段隔离、跳过结构属性、不自动写平台。"""

    def test_ai_suggest_success_returns_values_and_notes(self):
        api = FakeAttributeApi(replies={"material": "Steel", "color": "Yellow"})
        with patch.object(PL, "_ai_client", return_value=api):
            values, notes = ai_suggest_attributes(
                make_product(), AppConfig(), SITE, ["material", "color"])
        self.assertEqual(values, {"material": "Steel", "color": "Yellow"})
        self.assertEqual(len(api.calls), 2)
        self.assertTrue(any("material" in n and "AI 建议" in n for n in notes))

    def test_ai_suggest_per_field_failure_is_isolated(self):
        api = FakeAttributeApi(replies={"material": "Steel", "color": "Yellow"},
                               fail=("color",))
        with patch.object(PL, "_ai_client", return_value=api):
            values, notes = ai_suggest_attributes(
                make_product(), AppConfig(), SITE, ["material", "color"])
        self.assertEqual(values, {"material": "Steel"})
        self.assertTrue(any("color" in n and "失败" in n for n in notes), notes)

    def test_ai_suggest_skips_structure_sensitive_attributes(self):
        api = FakeAttributeApi(replies={"material": "Steel"})
        with patch.object(PL, "_ai_client", return_value=api):
            values, notes = ai_suggest_attributes(
                make_product(), AppConfig(), SITE,
                ["list_price", "item_dimensions", "material"])
        self.assertEqual(values, {"material": "Steel"})
        self.assertEqual(len(api.calls), 1)   # 价格/尺寸不烧钱、不编造
        self.assertTrue(any("list_price" in n and "需用户填写" in n for n in notes))
        self.assertTrue(any("item_dimensions" in n and "需用户填写" in n for n in notes))

    def test_ai_suggest_empty_missing_and_na_reply(self):
        values, notes = ai_suggest_attributes(make_product(), AppConfig(), SITE, [])
        self.assertEqual(values, {})
        self.assertTrue(notes)
        api = FakeAttributeApi()   # 未配置回复 → 返回 N/A
        with patch.object(PL, "_ai_client", return_value=api):
            values2, notes2 = ai_suggest_attributes(
                make_product(), AppConfig(), SITE, ["material"])
        self.assertEqual(values2, {})
        self.assertTrue(any("AI 无法从原文确定" in n for n in notes2), notes2)

    def test_parse_attribute_suggestion(self):
        self.assertEqual(parse_attribute_suggestion("material", "  Steel  "), "Steel")
        self.assertEqual(parse_attribute_suggestion("material", "N/A"), "")
        self.assertIs(parse_attribute_suggestion("is_assembly_required", "true"), True)
        self.assertIs(parse_attribute_suggestion("batteries_required", "No"), False)
        self.assertEqual(parse_attribute_suggestion("item_package_quantity", "1 件"), 1)
        self.assertEqual(parse_attribute_suggestion("model_number",
                                                    "model_number: HW20-28"),
                         "HW20-28")
        self.assertEqual(parse_attribute_suggestion("color", "```\nYellow\n```"),
                         "Yellow")

    def test_build_attributes_prompt_contains_attribute_and_site(self):
        prompt = build_attributes_prompt(make_product(), AppConfig(), SITE, "material")
        self.assertIn("material", prompt)
        self.assertIn("Amazon CA", prompt)
        self.assertIn("N/A", prompt)
        self.assertIn("backhoe", prompt.lower())


class RunAutofillTests(EnvIsolatedCase):
    """run_amazon_product：op=1 也补齐；AI 仅在 use_ai=True 时并入；不写平台。"""

    def test_op1_fake_save_payload_includes_autofilled_attrs(self):
        os.environ["AURORA_SKIP_LICENSE"] = "1"
        fake = FakeClient(detail=make_detail())
        res = run_amazon_product("220114595363181900", AppConfig(), client=fake,
                                 dry_run=False, confirm_write=True, op=OP_SAVE)
        self.assertTrue(res.ok, res.error)
        self.assertTrue(res.saved)
        self.assertEqual(len(fake.saved), 1)
        payload, op = fake.saved[0]
        self.assertEqual(op, OP_SAVE)
        self.assertEqual(len(payload), 39)
        self.assertEqual(missing_common_attributes(payload["productAttr"]), ["manufacturer_contact_information"])
        self.assertEqual(_attr_value(payload, "model_number"), "WY-QHCA-LTM-6")
        self.assertEqual(fake.template_queries, [])   # op=1 不拉在线模板
        self.assertTrue(any("已自动补齐发布必填属性" in w for w in res.warnings))

    def test_op1_dry_run_never_writes(self):
        fake = FakeClient(detail=make_detail())
        res = run_amazon_product("220114595363181900", AppConfig(), client=fake,
                                 dry_run=True, op=OP_SAVE)
        self.assertTrue(res.ok, res.error)
        self.assertFalse(res.saved)
        self.assertEqual(fake.saved, [])
        step = next(s for s in res.steps if s.name == "必填属性补齐")
        self.assertIn("model_number", step.detail)

    def test_use_ai_merges_extra_attr_suggestion_only_when_enabled(self):
        os.environ["AURORA_SKIP_LICENSE"] = "1"
        api = FakeAttributeApi(replies={"material": "Steel"})
        fake = FakeClient(detail=make_detail())
        with patch.object(PL, "_ai_client", return_value=api):
            res = run_amazon_product(
                "220114595363181900", AppConfig(), client=fake, dry_run=False,
                confirm_write=True, use_ai=True, op=OP_SAVE,
                overrides={"required_attributes": ["model_number", "material"]})
        self.assertTrue(res.ok, res.error)
        self.assertTrue(res.ai_used)
        payload = fake.saved[0][0]
        self.assertEqual(_attr_value(payload, "model_number"), "WY-QHCA-LTM-6")
        self.assertEqual(_attr_value(payload, "material"), "Steel")
        self.assertTrue(any("AI 建议" in w for w in res.warnings))

    def test_use_ai_false_never_calls_ai(self):
        fake = FakeClient(detail=make_detail())
        with patch.object(PL, "_ai_client",
                          side_effect=AssertionError("未开启 use_ai 不应调用 AI")):
            res = run_amazon_product(
                "220114595363181900", AppConfig(), client=fake, dry_run=True,
                use_ai=False, op=OP_SAVE,
                overrides={"required_attributes": ["model_number", "material"]})
        self.assertTrue(res.ok, res.error)
        step = next(s for s in res.steps if s.name == "必填属性补齐")
        self.assertIn("material", step.detail)      # 只提示仍需人工
        self.assertFalse(res.ai_used)

    def test_still_missing_attr_is_warning_not_blocker(self):
        fake = FakeClient(detail=make_detail())
        res = run_amazon_product(
            "220114595363181900", AppConfig(), client=fake, dry_run=True,
            op=OP_SAVE, overrides={"required_attributes": ["mystery_attr"]})
        self.assertTrue(res.ok, res.error)          # 不阻断保存
        self.assertTrue(any("发布必填属性仍缺失" in w for w in res.warnings))

    def test_op2_template_and_autofill_still_work(self):
        fake = FakeClient(detail=make_detail(), template={
            "power_source_type": [{"value": "diesel", "marketplace_id": SITE}]})
        res = run_amazon_product(
            "220114595363181900", AppConfig(), client=fake, dry_run=True,
            op="2", overrides={"required_attributes": ["model_number"]})
        self.assertTrue(res.ok, res.error)
        self.assertEqual(len(fake.template_queries), 1)   # op=2 才拉模板
        step = next(s for s in res.steps if s.name == "必填属性补齐")
        self.assertIn("model_number", step.detail)


if __name__ == "__main__":
    unittest.main(verbosity=2)
