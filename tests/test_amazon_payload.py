"""Amazon 保存包（amazonSave.txt 39 键）离线单测，不联网。

跑法：``.venv/bin/python tests/test_amazon_payload.py``
"""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.services.amazon_payload import (
    BULLET_MAX_CHARS, BULLET_MAX_ITEMS, DESCRIPTION_MAX_BYTES, REQUIRED_COMMON,
    SAVE_KEYS, SEARCH_TERMS_MAX_CHARS, TITLE_MAX, apply_site, attribute_present,
    build_amazon_payload, build_warnings, format_attribute_value, join_bullets,
    language_tag_for, merge_attributes, missing_common_attributes,
    parse_product_attr, split_bullets, validate_amazon_payload,
)
from aeclone.services.amazon_prompts import site_meta


def make_product(**over):
    """合成一个「能过校验」的 CA 商品（结构对齐真实 editSp.product）。"""
    product = {
        "id": "220114595363182310",
        "shopId": "9408005",
        "marketPlaceId": "A2EUQ1WTGCTBG2",
        "currency": "CAD",
        "title": "5 Ton Diesel Wheeled Backhoe Loader Heavy Duty Construction Machine",
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
        "sku": "WY-QHCA-LTM-1",
        "brand": "Generic",
        "manufacturer": "Generic",
        "productAttr": json.dumps({
            "is_assembly_required": [{"value": False, "marketplace_id": "A2EUQ1WTGCTBG2"}],
            "batteries_required": [{"value": False, "marketplace_id": "A2EUQ1WTGCTBG2"}],
        }),
        "standardPrice": 15999.0,
        "saleSalePrice": 14999.0,
        "standardPriceCurrency": "CAD",
        "saleStartDateStr": None,
        "saleEndDateStr": None,
        "quantity": 3,
        "conditionType": None,
        "conditionValue": "",
        "mainImage": "https://sc04.alicdn.com/kf/main.png",
        "extraImages": ["https://sc04.alicdn.com/kf/a.png", "https://sc04.alicdn.com/kf/b.png"],
        "extraImagesStr": "https://sc04.alicdn.com/kf/a.png;https://sc04.alicdn.com/kf/b.png",
        "draftImgUrl": "https://sc04.alicdn.com/kf/a.png|https://sc04.alicdn.com/kf/b.png",
        "variationTheme": "",
        "skuTitle": 0,
        "description": ("<p>Heavy duty backhoe loader for road construction.</p>"
                        "<ul><li>Engine: Diesel</li><li>Drive: 4x4</li></ul>"),
        "bulletPoint": join_bullets([
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


def make_payload(**over):
    return build_amazon_payload(make_product(**over), None, {}, currency="CAD")


class RequiredAttributeTests(unittest.TestCase):
    """task-8：必填属性常量/格式/合并/提醒。"""

    def test_required_common_constant(self):
        self.assertEqual(set(REQUIRED_COMMON),
                         {"model_number", "part_number", "model_name",
                          "manufacturer_contact_information"})

    def test_format_attribute_value(self):
        mp = "A2EUQ1WTGCTBG2"
        self.assertEqual(language_tag_for(mp), "en_CA")
        self.assertEqual(language_tag_for("ATVPDKIKX0DER"), "en_US")
        self.assertEqual(language_tag_for("A1AM78C64UM0Y8"), "es_MX")

        str_item = format_attribute_value("HW20-28", mp)
        self.assertEqual(str_item, [{"value": "HW20-28", "language_tag": "en_CA",
                                     "marketplace_id": mp}])
        bool_item = format_attribute_value(False, mp)
        self.assertEqual(bool_item, [{"value": False, "marketplace_id": mp}])
        self.assertNotIn("language_tag", bool_item[0])
        dict_item = format_attribute_value({"currency": "CAD", "amount": 8900.0}, mp)
        self.assertEqual(dict_item, [{"value": {"currency": "CAD", "amount": 8900.0},
                                      "marketplace_id": mp}])
        self.assertEqual(format_attribute_value("", mp), [])
        self.assertEqual(format_attribute_value(None, mp), [])

    def test_format_raw_item_list_and_present(self):
        mp = "A2EUQ1WTGCTBG2"
        items = [{"length": {"value": "196", "unit": "inches"},
                  "width": {"value": "63", "unit": "inches"},
                  "height": {"value": "98", "unit": "inches"},
                  "marketplace_id": mp}]
        self.assertEqual(format_attribute_value(items, mp), items)
        # item_dimensions 这类原始 item 列表（无 value 键）也算「已填写」
        self.assertTrue(attribute_present({"item_dimensions": items}, "item_dimensions"))
        self.assertEqual(format_attribute_value([{"currency": "CAD",
                                                  "value": "8900.00",
                                                  "marketplace_id": mp}], mp),
                         [{"currency": "CAD", "value": "8900.00",
                           "marketplace_id": mp}])

    def test_merge_attributes_preserves_existing_and_override_wins(self):
        base = {"is_assembly_required": [{"value": False, "marketplace_id": "A2EUQ1WTGCTBG2"}],
                "model_number": [{"value": "OLD", "language_tag": "en_CA",
                                  "marketplace_id": "A2EUQ1WTGCTBG2"}]}
        merged = merge_attributes(base, {"model_number": "NEW",
                                         "power_source_type": "diesel",
                                         "empty": ""},
                                  "A2EUQ1WTGCTBG2")
        self.assertEqual(merged["model_number"][0]["value"], "NEW")
        self.assertEqual(merged["power_source_type"][0]["value"], "diesel")
        self.assertNotIn("empty", merged)
        self.assertEqual(merged["is_assembly_required"][0]["value"], False)

    def test_build_payload_merges_attributes_override(self):
        payload = build_amazon_payload(
            make_product(), None,
            {"attributes": {"model_number": "HW20-28",
                            "power_source_type": "diesel",
                            "is_assembly_required": True}})
        attr = json.loads(payload["productAttr"])
        self.assertEqual(attr["model_number"][0]["value"], "HW20-28")
        self.assertEqual(attr["model_number"][0]["language_tag"], "en_CA")
        self.assertEqual(attr["model_number"][0]["marketplace_id"], "A2EUQ1WTGCTBG2")
        self.assertEqual(attr["power_source_type"][0]["value"], "diesel")
        self.assertEqual(attr["is_assembly_required"][0]["value"], True)
        self.assertNotIn("language_tag", attr["is_assembly_required"][0])

    def test_build_payload_site_switch_attributes(self):
        payload = build_amazon_payload(
            make_product(), None,
            {"marketplace_id": "ATVPDKIKX0DER",
             "attributes": {"model_number": "HW20-28"}})
        attr = json.loads(payload["productAttr"])
        self.assertEqual(attr["model_number"][0]["marketplace_id"], "ATVPDKIKX0DER")
        self.assertEqual(attr["model_number"][0]["language_tag"], "en_US")
        self.assertEqual(attr["is_assembly_required"][0]["marketplace_id"],
                         "ATVPDKIKX0DER")

    def test_missing_common_attributes_and_present(self):
        self.assertEqual(set(missing_common_attributes("{}")), set(REQUIRED_COMMON))
        self.assertEqual(missing_common_attributes(None), list(REQUIRED_COMMON))
        product = make_product()
        ok_payload = build_amazon_payload(product, None, {"attributes": {
            "model_number": "HW20-28", "part_number": "WY-QHCA-LTM-1",
            "model_name": "HW20-28",
            "manufacturer_contact_information": "Generic, CN"}})
        self.assertEqual(missing_common_attributes(ok_payload["productAttr"]), [])
        self.assertFalse(attribute_present({"x": []}, "x"))
        self.assertTrue(attribute_present({"x": [{"value": False}]}, "x"))

    def test_build_warnings_reports_missing_required(self):
        # 默认开启保存包级必填属性自动补齐：payload 里应已补齐，不再有「缺失」提醒
        payload = make_payload()
        self.assertEqual(missing_common_attributes(payload["productAttr"]), ["manufacturer_contact_information"])
        self.assertTrue(any("manufacturer_contact_information" in w for w in build_warnings(payload)))
        # 关闭自动补齐时才提示缺失（cfg.amazon_autofill_attrs=False）
        no_fill_cfg = SimpleNamespace(amazon_autofill_attrs=False)
        payload2 = build_amazon_payload(make_product(), no_fill_cfg, {}, currency="CAD")
        warns = build_warnings(payload2)
        self.assertTrue(any("发布必填属性缺失" in w for w in warns), warns)


class KeysAndBulletsTests(unittest.TestCase):
    def test_save_keys_39_and_order(self):
        self.assertEqual(len(SAVE_KEYS), 39)
        self.assertEqual(SAVE_KEYS[0], "id")
        self.assertEqual(SAVE_KEYS[-1], "searchTerms")
        self.assertEqual(len(set(SAVE_KEYS)), 39)
        for key in ("bulletPoint", "description", "productAttr", "gpsrSafetyAttestation",
                    "switchFulfillmentTo", "merchantShippingGroup"):
            self.assertIn(key, SAVE_KEYS)

    def test_split_join_bullets(self):
        self.assertEqual(split_bullets("a±b± c ±"), ["a", "b", "c"])
        self.assertEqual(split_bullets(""), [])
        self.assertEqual(join_bullets(["a", " ", "b"]), "a±b")
        self.assertEqual(join_bullets([]), "")
        self.assertEqual(join_bullets(["a±b"]), "a±b")


class BuildPayloadTests(unittest.TestCase):
    def test_clear_promotion_removes_old_price_dates_and_schema_offer(self):
        product = make_product(
            saleSalePrice=50, saleStartDateStr="2026-11-20", saleEndDateStr="2026-12-01",
            productAttr=json.dumps({"purchasable_offer": [{
                "our_price": [{"schedule": [{"value_with_tax": 90}]}],
                "discounted_price": [{"schedule": [{"value_with_tax": 50}]}],
            }]}))
        payload = build_amazon_payload(product, {"amazon_sale_price": "40"}, {"sale_price": "0"})
        self.assertIsNone(payload["saleSalePrice"])
        self.assertIsNone(payload["saleStartDateStr"])
        self.assertIsNone(payload["saleEndDateStr"])
        offer, = json.loads(payload["productAttr"])["purchasable_offer"]
        self.assertNotIn("discounted_price", offer)
        self.assertEqual(offer["our_price"][0]["schedule"][0]["value_with_tax"], 90)
    def test_mfn_updates_price_inventory_and_handling_in_schema_attributes(self):
        product = make_product(productAttr=json.dumps({
            "fulfillment_availability": [{"fulfillment_channel_code": "DEFAULT", "quantity": 8}],
            "purchasable_offer": [{"our_price": [{"schedule": [{"value_with_tax": 90}]}], "currency": "CAD"}]}))
        payload = build_amazon_payload(product, None, {"standard_price": 105, "quantity": 0, "handling_time": 4})
        attrs = json.loads(payload["productAttr"])
        self.assertEqual(payload["quantity"], 0)
        self.assertEqual(attrs["fulfillment_availability"], [{"fulfillment_channel_code": "DEFAULT", "quantity": 0,
                                                               "lead_time_to_ship_max_days": 4}])
        self.assertEqual(attrs["purchasable_offer"][0]["our_price"][0]["schedule"][0]["value_with_tax"], 105)

    def test_draft_permits_missing_price_but_publish_requires_price(self):
        payload = build_amazon_payload(make_product(standardPrice=None), None)
        self.assertFalse(any("标准价" in issue for issue in validate_amazon_payload(payload, op="1")))
        self.assertTrue(any("标准价" in issue for issue in validate_amazon_payload(payload, op="2")))
    def test_only_save_keys_in_order(self):
        payload = make_payload()
        self.assertEqual(list(payload.keys()), SAVE_KEYS)
        self.assertNotIn("extraImages", payload)

    def test_numbers_and_null_prices(self):
        p = make_product(quantity="7", isVariation="0", skuTitle="1",
                         gpsrSafetyAttestation="0", standardPrice=0.0, saleSalePrice=0.0)
        payload = build_amazon_payload(p, None, {})
        self.assertEqual(payload["quantity"], 7)
        self.assertIsInstance(payload["isVariation"], int)
        self.assertIsInstance(payload["skuTitle"], int)
        self.assertIsInstance(payload["gpsrSafetyAttestation"], int)
        self.assertEqual(payload["gpsrSafetyAttestation"], 0)
        self.assertIsNone(payload["standardPrice"])
        self.assertIsNone(payload["saleSalePrice"])

    def test_price_rounding(self):
        payload = make_payload(standardPrice="12.5", saleSalePrice="10.25")
        self.assertEqual(payload["standardPrice"], 12.5)
        self.assertEqual(payload["saleSalePrice"], 10.25)

    def test_overrides_map_snake_case(self):
        ov = {
            "title": "  New Title For Backhoe Loader  ",
            "bullets": ["B1", "B2", "B3"],
            "description": "plain line",
            "search_terms": "abc def",
            "brand": "Hengwang",
            "manufacturer": "Hengwang Factory",
            "sku": "NEW-SKU",
            "standard_price": "9999.5",
            "sale_price": "8999.5",
            "quantity": "12",
            "category_id": " 13553005011 ",
            "category_path": " 6299024011/6256963011/6256964011/13553005011 ",
            "fulfillment": "FBA",
            "shipping_template": "tpl-1",
            "mainImage": "https://ignored.example/x.png",  # 非约定键：忽略
        }
        payload = build_amazon_payload(make_product(), None, ov, currency="CAD")
        self.assertEqual(payload["title"], "New Title For Backhoe Loader")
        self.assertEqual(payload["bulletPoint"], "B1±B2±B3")
        self.assertEqual(payload["description"], "<p>plain line</p>")
        self.assertEqual(payload["searchTerms"], "abc def")
        self.assertEqual(payload["brand"], "Hengwang")
        self.assertEqual(payload["manufacturer"], "Hengwang Factory")
        self.assertEqual(payload["sku"], "NEW-SKU")
        self.assertEqual(payload["standardPrice"], 9999.5)
        self.assertEqual(payload["saleSalePrice"], 8999.5)
        self.assertIsNone(payload["quantity"])
        self.assertEqual(payload["categoryId"], "13553005011")
        self.assertEqual(payload["categoryBrowsePath"],
                         "6299024011/6256963011/6256964011/13553005011")
        self.assertEqual(payload["switchFulfillmentTo"], "AFN")
        self.assertIsNone(payload["merchantShippingGroup"])
        self.assertEqual(payload["mainImage"], "https://sc04.alicdn.com/kf/main.png")

    def test_empty_override_does_not_replace(self):
        base = make_payload()
        payload = build_amazon_payload(make_product(), None,
                                       {"title": "", "brand": "  ", "quantity": "",
                                        "standard_price": ""}, currency="CAD")
        self.assertEqual(payload["title"], base["title"])
        self.assertEqual(payload["brand"], "Generic")
        self.assertEqual(payload["quantity"], 3)
        self.assertEqual(payload["standardPrice"], 15999.0)

    def test_cfg_fallbacks_only_when_product_empty(self):
        cfg = {"amazon_price": "88.8", "amazon_quantity": 9,
               "amazon_fulfillment": "FBA", "amazon_shipping_template": "cfg-tpl",
               "amazon_brand": "CfgBrand", "amazon_manufacturer": "CfgMaker",
               "amazon_sku": "CFG-SKU"}
        payload = build_amazon_payload(
            make_product(brand="", manufacturer="", sku="", quantity=None,
                         standardPrice=0.0, switchFulfillmentTo="",
                         merchantShippingGroup=""), cfg, {})
        self.assertEqual(payload["brand"], "CfgBrand")
        self.assertEqual(payload["manufacturer"], "CfgMaker")
        self.assertEqual(payload["sku"], "CFG-SKU")
        self.assertIsNone(payload["quantity"])
        self.assertEqual(payload["standardPrice"], 88.8)
        self.assertEqual(payload["switchFulfillmentTo"], "AFN")
        self.assertIsNone(payload["merchantShippingGroup"])

    def test_cfg_price_does_not_override_existing_product_price(self):
        cfg = {"amazon_price": "88.8", "amazon_sale_price": "77.7"}
        payload = build_amazon_payload(make_product(), cfg, {})
        self.assertEqual(payload["standardPrice"], 15999.0)
        self.assertEqual(payload["saleSalePrice"], 14999.0)

    def test_plain_text_description_is_wrapped(self):
        payload = make_payload(description="line1\nline2")
        self.assertEqual(payload["description"], "<p>line1</p><p>line2</p>")
        payload2 = make_payload(description="")
        self.assertEqual(payload2["description"], "")

    def test_extra_images_derivation(self):
        product = make_product(extraImagesStr="", draftImgUrl="",
                               extraImages=["https://x/a.png", "https://x/b.png"])
        payload = build_amazon_payload(product, None, {})
        self.assertEqual(payload["extraImagesStr"], "https://x/a.png;https://x/b.png")
        self.assertEqual(payload["draftImgUrl"], "https://x/a.png|https://x/b.png")

    def test_site_switch_rewrites_currency_and_attr(self):
        payload = build_amazon_payload(make_product(), None,
                                       {"marketplace_id": "ATVPDKIKX0DER"})
        self.assertEqual(payload["marketPlaceId"], "ATVPDKIKX0DER")
        self.assertEqual(payload["currency"], "USD")
        attr = json.loads(payload["productAttr"])
        self.assertEqual(attr["is_assembly_required"][0]["marketplace_id"],
                         "ATVPDKIKX0DER")
        self.assertEqual(attr["batteries_required"][0]["marketplace_id"],
                         "ATVPDKIKX0DER")

    def test_apply_site(self):
        payload = make_payload()
        switched = apply_site(payload, "A1AM78C64UM0Y8", "",
                              site_meta("A1AM78C64UM0Y8"))
        self.assertEqual(switched["marketPlaceId"], "A1AM78C64UM0Y8")
        self.assertEqual(switched["currency"], "MXN")
        attr = json.loads(switched["productAttr"])
        self.assertEqual(attr["is_assembly_required"][0]["marketplace_id"],
                         "A1AM78C64UM0Y8")
        # 原 payload 不被原地修改
        self.assertEqual(payload["marketPlaceId"], "A2EUQ1WTGCTBG2")


class ValidateTests(unittest.TestCase):
    def test_valid_payload_has_no_issues(self):
        payload = make_payload()
        site = site_meta(payload["marketPlaceId"])
        self.assertEqual(validate_amazon_payload(payload, site_meta=site), [])

    def test_title_rules(self):
        payload = make_payload(title="")
        issues = validate_amazon_payload(payload, site_meta=site_meta("A2EUQ1WTGCTBG2"))
        self.assertTrue(any("标题为空" in i for i in issues))
        payload = make_payload(title="A" * (TITLE_MAX + 1))
        issues = validate_amazon_payload(payload, site_meta=site_meta("A2EUQ1WTGCTBG2"))
        self.assertTrue(any("标题超过" in i for i in issues))

    def test_chinese_blocked_on_non_chinese_site(self):
        payload = make_payload(title="反铲装载机 Backhoe Loader Heavy Duty Machine")
        issues = validate_amazon_payload(payload, site_meta=site_meta("A2EUQ1WTGCTBG2"))
        self.assertTrue(any("含中文字符" in i for i in issues))

    def test_chinese_allowed_on_cn_site(self):
        product = make_product(marketPlaceId="AAHKV2X7AFYLW", currency="CNY",
                               title="反铲装载机 重型工程机械 四轮驱动")
        payload = build_amazon_payload(product, None, {}, currency="CNY")
        issues = validate_amazon_payload(payload, site_meta=site_meta("AAHKV2X7AFYLW"))
        self.assertEqual([i for i in issues if "中文" in i], [])

    def test_bullets_limits(self):
        payload = make_payload(bulletPoint=join_bullets(
            [f"bullet number {i}" for i in range(BULLET_MAX_ITEMS + 1)]))
        issues = validate_amazon_payload(payload, site_meta=site_meta("A2EUQ1WTGCTBG2"))
        self.assertTrue(any("卖点数量超过" in i for i in issues))

        payload = make_payload(bulletPoint="x" * (BULLET_MAX_CHARS + 1))
        issues = validate_amazon_payload(payload, site_meta=site_meta("A2EUQ1WTGCTBG2"))
        self.assertTrue(any("超过 700 字符" in i for i in issues))

        payload = make_payload(bulletPoint="<b>bold</b> bullet")
        issues = validate_amazon_payload(payload, site_meta=site_meta("A2EUQ1WTGCTBG2"))
        self.assertTrue(any("非法字符" in i for i in issues))

        payload = make_payload(bulletPoint="● bullet")
        issues = validate_amazon_payload(payload, site_meta=site_meta("A2EUQ1WTGCTBG2"))
        self.assertTrue(any("非法字符" in i for i in issues))

    def test_bullets_fewer_than_three(self):
        payload = make_payload(bulletPoint="only one±only two")
        issues = validate_amazon_payload(payload, site_meta=site_meta("A2EUQ1WTGCTBG2"))
        self.assertTrue(any("卖点不足 3 条" in i for i in issues))

    def test_ten_bullets_allowed(self):
        payload = make_payload(bulletPoint=join_bullets(
            [f"feature number {i}" for i in range(1, BULLET_MAX_ITEMS + 1)]))
        issues = validate_amazon_payload(payload, site_meta=site_meta("A2EUQ1WTGCTBG2"))
        self.assertEqual([i for i in issues if "卖点" in i], [])

    def test_description_rules(self):
        payload = make_payload(description="")
        issues = validate_amazon_payload(payload, site_meta=site_meta("A2EUQ1WTGCTBG2"))
        self.assertTrue(any("产品描述为空" in i for i in issues))

        payload = make_payload(description="x" * (DESCRIPTION_MAX_BYTES + 100))
        issues = validate_amazon_payload(payload, site_meta=site_meta("A2EUQ1WTGCTBG2"))
        self.assertTrue(any("字节" in i for i in issues))

        payload = make_payload(description="<p>ok</p><script>alert(1)</script>")
        issues = validate_amazon_payload(payload, site_meta=site_meta("A2EUQ1WTGCTBG2"))
        self.assertTrue(any("危险标签" in i for i in issues))

    def test_search_terms_limits(self):
        payload = make_payload(searchTerms="word " * 120)
        issues = validate_amazon_payload(payload, site_meta=site_meta("A2EUQ1WTGCTBG2"))
        self.assertTrue(any("Search Terms" in i for i in issues))

    def test_site_category_sku_brand_image_price(self):
        cases = [
            ({"marketPlaceId": ""}, "marketPlaceId 为空"),
            ({"marketPlaceId": "ZZZZZZZZZZZZZ"}, "不在支持的 Amazon 站点"),
            ({"categoryId": ""}, "类目无效"),
            ({"categoryId": "abc"}, "类目无效"),
            ({"categoryBrowsePath": "1/2/9999999999"}, "末段"),
            ({"sku": ""}, "SKU 为空"),
            ({"brand": ""}, "品牌为空"),
            ({"manufacturer": ""}, "制造商为空"),
            ({"mainImage": "ftp://x.png"}, "主图"),
            ({"standardPrice": 0}, "标准价"),
            ({"standardPrice": 100, "saleSalePrice": 100}, "促销价必须小于标准价"),
        ]
        for over, hint in cases:
            payload = make_payload(**over)
            payload.update(over)  # 直接把「原始值」放进保存包，测试 validate 本身
            issues = validate_amazon_payload(payload, site_meta=site_meta(
                payload.get("marketPlaceId") or "A2EUQ1WTGCTBG2"))
            self.assertTrue(any(hint in i for i in issues), f"{over} → {issues}")

        # 店小秘有时返回人类可读路径；末段不是数字节点 ID 时不应误报不一致。
        payload = make_payload()
        payload["categoryBrowsePath"] = "Patio, Lawn & Garden>Farm & Ranch>Agricultural Machinery>Backhoes"
        issues = validate_amazon_payload(payload, site_meta=site_meta("A2EUQ1WTGCTBG2"))
        self.assertFalse(any("末段" in i for i in issues), issues)
        payload["categoryBrowsePath"] = "1/2/9999999999"
        issues = validate_amazon_payload(payload, site_meta=site_meta("A2EUQ1WTGCTBG2"))
        self.assertTrue(any("末段" in i for i in issues), issues)

    def test_promo_contact_absolute_words(self):
        payload = make_payload(title="Best Backhoe Loader Free Shipping Machine")
        issues = validate_amazon_payload(payload, site_meta=site_meta("A2EUQ1WTGCTBG2"))
        self.assertTrue(any("促销" in i for i in issues))

        payload = make_payload(description="<p>100% guarantee, contact seller@x.com</p>")
        issues = validate_amazon_payload(payload, site_meta=site_meta("A2EUQ1WTGCTBG2"))
        self.assertTrue(any("促销" in i for i in issues))

        payload = make_payload(description="<p>see https://example.com for details</p>")
        issues = validate_amazon_payload(payload, site_meta=site_meta("A2EUQ1WTGCTBG2"))
        self.assertTrue(any("网址" in i for i in issues))

    def test_promo_whitelist(self):
        payload = make_payload(title="Best Backhoe Loader Heavy Duty Machine")
        site = dict(site_meta("A2EUQ1WTGCTBG2"))
        site["promo_whitelist"] = ["best"]
        issues = validate_amazon_payload(payload, site_meta=site)
        self.assertEqual([i for i in issues if "促销" in i], [])


class WarningsTests(unittest.TestCase):
    def test_default_payload_warnings(self):
        payload = make_payload()
        warns = build_warnings(payload)
        self.assertTrue(any("Generic" in w for w in warns))

    def test_more_warnings(self):
        payload = make_payload(
            title="Short",
            bulletPoint=join_bullets([f"b{i}" for i in range(10)]),
            searchTerms="word " * 80,
            description="<p>" + ("d" * 2500) + "</p>",
            extraImagesStr="", draftImgUrl="", extraImages=[],
            categoryBrowsePath="未分类", fullCid="",
            standardPrice=None, quantity=0,
        )
        warns = "\n".join(build_warnings(payload))
        self.assertIn("标题长度", warns)
        self.assertIn("卖点 10 条", warns)
        self.assertIn("Search Terms", warns)
        self.assertIn("产品描述", warns)
        self.assertIn("没有附加图", warns)
        self.assertIn("未分类", warns)
        self.assertIn("库存为 0", warns)
        self.assertIn("标准价为 0", warns)

    def test_site_switch_warning(self):
        product = make_product()
        payload = build_amazon_payload(product, None, {})
        switched = apply_site(payload, "ATVPDKIKX0DER", "", site_meta("ATVPDKIKX0DER"))
        warns = build_warnings(switched, product=product)
        self.assertTrue(any("站点已从" in w for w in warns))

    def test_empty_product_attr_warning(self):
        # 自动补齐开启时 productAttr 会被补齐（空属性提醒消失）；关闭后保留提醒
        no_fill_cfg = SimpleNamespace(amazon_autofill_attrs=False)
        payload = build_amazon_payload(make_product(productAttr="{}"), no_fill_cfg, {},
                                       currency="CAD")
        warns = build_warnings(payload)
        self.assertTrue(any("productAttr" in w for w in warns), warns)




class TitleRepetitionTests(unittest.TestCase):
    """Amazon 标题同一单词最多 2 次（平台实测 code:100470）。"""

    def _payload(self, title):
        return {"title": title, "bulletPoint": "A±B±C", "description": "<p>x</p>",
                "searchTerms": "", "sku": "S1", "brand": "B", "manufacturer": "M",
                "mainImage": "https://x/y.jpg", "categoryId": "123",
                "categoryBrowsePath": "1/123", "marketPlaceId": "A2EUQ1WTGCTBG2"}

    def test_repeated_word_blocked(self):
        from aeclone.services.amazon_payload import validate_amazon_payload
        issues = validate_amazon_payload(
            self._payload("Mini Backhoe Loader 4x4 Backhoe Loader Farm Backhoe Loader"))
        self.assertTrue(any("超过 2 次" in x for x in issues), issues)

    def test_two_occurrences_allowed(self):
        from aeclone.services.amazon_payload import validate_amazon_payload
        issues = validate_amazon_payload(
            self._payload("CE EPA Mini Backhoe Loader 4x4 WD Agricultural Farm Machine"))
        self.assertFalse(any("超过 2 次" in x for x in issues), issues)


if __name__ == "__main__":
    unittest.main(verbosity=2)
