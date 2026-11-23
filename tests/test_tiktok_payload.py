"""TikTok 保存包（tiktokSave.txt）构造/站点切换/校验离线单测（不联网、不写文件）。

跑法：``.venv/bin/python tests/test_tiktok_payload.py``
"""
import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.services.tiktok_payload import (
    DEFAULT_LIMITS, OP_MOVE_PENDING, OP_SAVE, OVERRIDE_KEYS, SAVE_KEYS, SITE_META,
    apply_site, build_tiktok_payload, build_warnings, extract_detail_product,
    join_pipe, payload_price, payload_stock, price_stock_overrides, split_pipe,
    validate_tiktok_payload,
)

IMAGE_1 = "https://img.example.com/1.jpg"
IMAGE_2 = "https://img.example.com/2.jpg"
IMAGE_3 = "https://img.example.com/3.jpg"


def make_detail(**over):
    """合成一个「能过校验」的 US 商品详情（结构对齐 gettiktokProduct 的 data.product）。"""
    product = {
        "id": "1729000000000001",
        "shopId": "9408005",
        "site": "US",
        "siteIds": ["US"],
        "dxmState": "draft",
        "productName": "Portable Wireless Bluetooth Speaker Waterproof Outdoor 20W",
        "description": ("<p>Great portable speaker for outdoor travel and daily use.</p>"
                        "<ul><li>Battery: 20h</li><li>Waterproof: IPX7</li></ul>"),
        "categoryId": "601234",
        "categoryList": [{"categoryId": "601234", "categoryName": "Portable Speakers"}],
        "isNewCategory": 0,
        "productType": 1,
        "mainImage": "|".join([IMAGE_1, IMAGE_2, IMAGE_3]),
        "draftImgUrl": "|".join([IMAGE_1, IMAGE_2, IMAGE_3]),
        "packageWeight": 0.5,
        "packageLength": 10,
        "packageWidth": 10,
        "packageHeight": 10,
        "packageDimensionUnit": "CENTIMETER",
        "packageDimensionMode": "PRODUCT",
        "warehouseId": "WH-1",
        "variationList": [{
            "_combineId": "c1",
            "sellerSku": "SPK-001",
            "shopId": "9408005",
            "originalPrice": 19.99,
            "availableStock": 50,
            "skuProperties": [],
        }],
        "keyProductFeatures": [
            "Fast Charging: 30 min full charge",
            "Deep Bass: rich sound",
            "Portable: 300g light",
        ],
        "searchTerms": ["bluetooth speaker", "portable speaker"],
        "sourceUrl": "https://www.alibaba.com/product-detail/x_1.html",
        "brandId": "700001",
        "brandInfo": "Generic",
        "productUnit": "100000015",
        "productAttributes": "[]",
        "productCertifications": "[]",
        "unknownCamelField": {"keep": True},
        "variationBos": {"should": "drop"},
    }
    product.update(over)
    return {"product": product, "category": {"id": "601234", "isLeaf": 1},
            "shopId": "9408005"}


def make_payload(**over):
    return build_tiktok_payload(make_detail(**over), None, None,
                                site="US", shop_id="9408005")


class KeysAndLimitsTests(unittest.TestCase):
    def test_save_keys_51_ordered(self):
        self.assertEqual(len(SAVE_KEYS), 51)
        self.assertEqual(len(set(SAVE_KEYS)), 51)
        self.assertEqual(SAVE_KEYS[0], "id")
        self.assertEqual(SAVE_KEYS[1], "op")
        self.assertEqual(SAVE_KEYS[-1], "productCertificationsRules")
        for key in ("shopId", "site", "categoryId", "categoryList", "productName",
                    "description", "keyProductFeatures", "searchTerms", "productType",
                    "variationList", "mainImage", "draftImgUrl", "packageWeight",
                    "packageDimensionUnit", "warehouseId", "productAttributes"):
            self.assertIn(key, SAVE_KEYS)

    def test_default_limits_match_design_1_6(self):
        expected = {
            "priceMin": 0.01, "priceMax": 180000000.0, "stockMax": 99999,
            "nameMax": 255, "nameMin": 25, "descMax": 10000, "descImgMax": 30,
            "imgCountMax": 9, "imgCountMin": 1,
            "packageMin": 1000, "packageMid": 1000, "packageMax": 1000,
            "variationSizeMax": 100, "variationNameMax": 20, "variationOptionMax": 50,
        }
        self.assertEqual(DEFAULT_LIMITS, expected)

    def test_site_meta_shared_with_prompts(self):
        from aeclone.services import tiktok_prompts

        self.assertIs(SITE_META, tiktok_prompts.SITE_META)
        self.assertEqual(len(SITE_META), 24)

    def test_override_keys_match_design_3_4(self):
        expected = {
            "title", "description", "key_features", "search_terms",
            "category_id", "category_name", "category_list", "images",
            "video_id", "source_url", "product_type", "price", "stock",
            "seller_sku", "weight", "length", "width", "height",
            "dimension_unit", "warehouse_id", "product_unit", "brand_id",
            "manufacturer_id", "responsible_person_id", "product_attributes",
            "certifications", "variant_edits", "clear_images", "size_chart", "sku_images",
        }
        self.assertEqual(set(OVERRIDE_KEYS), expected)

    def test_split_join_pipe(self):
        self.assertEqual(split_pipe("a| b ||c "), ["a", "b", "c"])
        self.assertEqual(split_pipe(""), [])
        self.assertEqual(split_pipe(None), [])
        self.assertEqual(join_pipe(["a", " ", None, "b"]), "a|b")
        self.assertEqual(join_pipe([]), "")
        self.assertEqual(join_pipe("a|b"), "a|b")


class ExtractDetailTests(unittest.TestCase):
    def test_product_priority(self):
        product = extract_detail_product(make_detail())
        self.assertEqual(product["id"], "1729000000000001")
        self.assertNotIn("category", product)  # 详情 meta 键不混入商品字段

    def test_data_wrapper(self):
        detail = {"code": 0, "data": make_detail()}
        product = extract_detail_product(detail)
        self.assertEqual(product["productName"],
                         "Portable Wireless Bluetooth Speaker Waterproof Outdoor 20W")

    def test_direct_product_dict(self):
        product = extract_detail_product({"id": "1", "productName": "X"})
        self.assertEqual(product["id"], "1")

    def test_deep_copy(self):
        detail = make_detail()
        product = extract_detail_product(detail)
        product["variationList"][0]["availableStock"] = 0
        product["unknownCamelField"]["keep"] = False
        self.assertEqual(detail["product"]["variationList"][0]["availableStock"], 50)
        self.assertTrue(detail["product"]["unknownCamelField"]["keep"])

    def test_non_dict(self):
        self.assertEqual(extract_detail_product(None), {})
        self.assertEqual(extract_detail_product("x"), {})

    def test_data_null_envelope(self):
        # 详情端点无商品时 data=null（§0 实测）：不能让 envelope 混进保存包
        self.assertEqual(
            extract_detail_product({"code": 0, "msg": "", "data": None}), {})


class BuildPayloadTests(unittest.TestCase):
    def test_valid_payload_has_no_blocking_issues(self):
        payload = make_payload()
        self.assertEqual(validate_tiktok_payload(payload, site="US"), [])
        self.assertEqual(build_warnings(payload, site="US"), [])

    def test_unknown_keys_kept_and_variation_bos_removed(self):
        payload = make_payload()
        self.assertIn("unknownCamelField", payload)
        self.assertEqual(payload["unknownCamelField"], {"keep": True})
        self.assertNotIn("variationBos", payload)

    def test_all_save_keys_present(self):
        payload = make_payload()
        for key in SAVE_KEYS:
            self.assertIn(key, payload, key)
        self.assertEqual(payload["op"], 1)
        self.assertEqual(payload["site"], "US")
        self.assertEqual(payload["shopId"], "9408005")
        self.assertEqual(payload["productType"], 1)

    def test_empty_detail_fills_defaults(self):
        payload = build_tiktok_payload({}, None, None)
        for key in SAVE_KEYS:
            self.assertIn(key, payload, key)
        self.assertEqual(payload["categoryList"], [])
        self.assertEqual(payload["variationList"], [])
        self.assertEqual(payload["packageDimensionUnit"], "CENTIMETER")
        self.assertEqual(payload["op"], 1)

    def test_op_parameter(self):
        payload = make_payload()
        payload = build_tiktok_payload(make_detail(), None, None, op=OP_MOVE_PENDING)
        self.assertEqual(payload["op"], 3)
        self.assertEqual(build_tiktok_payload(make_detail(), None, None)["op"],
                         int(OP_SAVE))

    def test_build_does_not_mutate_detail(self):
        detail = make_detail()
        snapshot = copy.deepcopy(detail)
        build_tiktok_payload(detail, None, {"title": "New title " * 3})
        self.assertEqual(detail, snapshot)

    def test_cfg_fallbacks(self):
        cfg = {
            "tiktok_shop_id": "SHOP-9",
            "tiktok_site": "gb",
            "tiktok_weight": "1.5",
            "tiktok_length": "20",
            "tiktok_dimension_unit": "inch",
            "tiktok_sku": "CFG-SKU",
            "tiktok_warehouse_id": "CFG-WH",
        }
        payload = build_tiktok_payload({}, cfg, None)
        self.assertEqual(payload["shopId"], "SHOP-9")
        self.assertEqual(payload["site"], "GB")
        self.assertEqual(payload["packageWeight"], 1.5)
        self.assertEqual(payload["packageLength"], 20)
        self.assertEqual(payload["packageDimensionUnit"], "INCH")
        self.assertEqual(payload["warehouseId"], "CFG-WH")
        self.assertEqual(payload["sellerSku"], "CFG-SKU")

    def test_seller_sku_auto_generated(self):
        detail = make_detail(variationList=[{"originalPrice": 10, "availableStock": 1}])
        payload = build_tiktok_payload(detail, None, None, site="US")
        sku = payload["variationList"][0]["sellerSku"]
        self.assertTrue(sku)
        self.assertTrue(sku.startswith("AURORA-"))

    def test_image_fallback_from_img_url(self):
        detail = make_detail(mainImage="", draftImgUrl="", imgUrl=f"{IMAGE_1}|{IMAGE_2}")
        payload = build_tiktok_payload(detail, None, None, site="US")
        self.assertEqual(payload["mainImage"], f"{IMAGE_1}|{IMAGE_2}")

    def test_numbers_normalized_from_strings(self):
        detail = make_detail(packageWeight="0.5", packageLength="10",
                             packageWidth="10.25", packageHeight="10",
                             variationList=[{"sellerSku": "S1", "originalPrice": "19.99",
                                             "availableStock": "50"}])
        payload = build_tiktok_payload(detail, None, None, site="US")
        self.assertEqual(payload["packageWeight"], 0.5)
        self.assertIsInstance(payload["packageLength"], int | float)
        self.assertEqual(payload["packageWidth"], 10.25)
        row = payload["variationList"][0]
        self.assertEqual(row["originalPrice"], 19.99)
        self.assertEqual(row["availableStock"], 50)


class OverridesTests(unittest.TestCase):
    def test_full_override_mapping(self):
        overrides = {
            "title": "New Override Title For Bluetooth Speaker Outdoor",
            "description": "Plain text description for the speaker.",
            "key_features": ["A: b", "C: d", "E: f"],
            "search_terms": ["speaker", "bluetooth"],
            "category_id": "999001",
            "category_name": "New Speakers",
            "images": [IMAGE_1, IMAGE_2],
            "video_id": "v-123",
            "source_url": "https://example.com/src",
            "product_type": 0,
            "price": "25.5",
            "stock": "12",
            "seller_sku": "NEW-SKU",
            "weight": "1.25",
            "length": "20",
            "width": 30,
            "height": "15",
            "dimension_unit": "INCH",
            "warehouse_id": "WH-2",
            "product_unit": "100000016",
            "brand_id": "700002",
            "manufacturer_id": "800002",
            "responsible_person_id": "900002",
            "product_attributes": [{"name": "Power", "value": "20W"}],
            "certifications": '[{"id": 1}]',
        }
        payload = build_tiktok_payload(make_detail(), None, overrides,
                                       site="US", shop_id="9408005")
        self.assertEqual(payload["productName"], overrides["title"])
        self.assertEqual(payload["description"],
                         "<p>Plain text description for the speaker.</p>")
        self.assertEqual(payload["keyProductFeatures"], ["A: b", "C: d", "E: f"])
        self.assertEqual(payload["searchTerms"], ["speaker", "bluetooth"])
        self.assertEqual(payload["categoryId"], "999001")
        self.assertEqual(payload["categoryList"],
                         [{"categoryId": "999001", "categoryName": "New Speakers"}])
        self.assertEqual(payload["mainImage"], f"{IMAGE_1}|{IMAGE_2}")
        self.assertEqual(payload["draftImgUrl"], f"{IMAGE_1}|{IMAGE_2}")
        self.assertEqual(payload["videoId"], "v-123")
        self.assertEqual(payload["sourceUrl"], "https://example.com/src")
        self.assertEqual(payload["productType"], 0)
        row = payload["variationList"][0]
        self.assertEqual(row["sellerSku"], "NEW-SKU")
        self.assertEqual(row["originalPrice"], 25.5)
        self.assertEqual(row["availableStock"], 12)
        self.assertEqual(payload["packageWeight"], 1.25)
        self.assertEqual(payload["packageLength"], 20)
        self.assertEqual(payload["packageWidth"], 30)
        self.assertEqual(payload["packageHeight"], 15)
        self.assertEqual(payload["packageDimensionUnit"], "INCH")
        self.assertEqual(payload["warehouseId"], "WH-2")
        self.assertEqual(payload["productUnit"], "100000016")
        self.assertEqual(payload["brandId"], "700002")
        self.assertEqual(payload["manufacturerId"], "800002")
        self.assertEqual(payload["responsiblePersonId"], "900002")
        self.assertEqual(json.loads(payload["productAttributes"]),
                         [{"name": "Power", "value": "20W"}])
        self.assertEqual(payload["productCertifications"], '[{"id": 1}]')

    def test_category_list_override_json_string(self):
        overrides = {"category_list": '[{"categoryId": "1", "categoryName": "A"},'
                                     ' {"categoryId": "2", "categoryName": "B"}]'}
        payload = build_tiktok_payload(make_detail(), None, overrides, site="US")
        self.assertEqual(len(payload["categoryList"]), 2)
        self.assertEqual(payload["categoryList"][-1]["categoryId"], "2")

    def test_images_override_pipe_string(self):
        payload = build_tiktok_payload(
            make_detail(), None, {"images": f"{IMAGE_1}|{IMAGE_2}"}, site="US")
        self.assertEqual(payload["mainImage"], f"{IMAGE_1}|{IMAGE_2}")

    def test_empty_overrides_do_not_override(self):
        base = make_payload()
        empties = {
            "title": "", "description": "   ", "key_features": [], "search_terms": [],
            "category_id": "", "category_name": "", "category_list": [],
            "images": [], "video_id": "", "source_url": "", "product_type": None,
            "price": "", "stock": None, "seller_sku": "",
            "weight": "", "length": "", "width": "", "height": "",
            "dimension_unit": "", "warehouse_id": "", "product_unit": "",
            "brand_id": "", "manufacturer_id": "", "responsible_person_id": "",
            "product_attributes": "", "certifications": None,
        }
        payload = build_tiktok_payload(make_detail(), None, empties,
                                       site="US", shop_id="9408005")
        for key in ("productName", "description", "categoryId", "categoryList",
                    "mainImage", "productType", "packageWeight",
                    "packageDimensionUnit", "warehouseId", "brandId",
                    "productUnit", "productAttributes"):
            self.assertEqual(payload[key], base[key], key)
        self.assertEqual(payload.get("sellerSku"), base.get("sellerSku"))
        self.assertEqual(payload["keyProductFeatures"], base["keyProductFeatures"])
        self.assertEqual(payload["searchTerms"], base["searchTerms"])
        self.assertEqual(payload["variationList"][0]["originalPrice"],
                         base["variationList"][0]["originalPrice"])


class ValidateTests(unittest.TestCase):
    def issues(self, payload, **kwargs):
        kwargs.setdefault("site", payload.get("site") or "US")
        return validate_tiktok_payload(payload, **kwargs)

    def test_valid_payload(self):
        self.assertEqual(self.issues(make_payload()), [])

    def test_shop_and_site(self):
        payload = make_payload()
        payload["shopId"] = ""
        self.assertTrue(any("店铺" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["site"] = ""
        self.assertTrue(any("未选择站点" in x for x in self.issues(payload, site="")))
        payload = make_payload()
        payload["site"] = "XX"
        self.assertTrue(any("不在支持的 24 个" in x for x in self.issues(payload)))

    def test_category(self):
        payload = make_payload()
        payload["categoryId"] = ""
        self.assertTrue(any("类目" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["isLeaf"] = 0
        self.assertTrue(any("叶子类目" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["categorySite"] = "GB"
        self.assertTrue(any("类目站点" in x for x in self.issues(payload)))

    def test_title_length(self):
        cases = ("", "short title", "x" * 24, "x" * 256)
        for value in cases:
            payload = make_payload()
            payload["productName"] = value
            issues = self.issues(payload)
            self.assertTrue(any("标题" in x for x in issues), value)
        for value in ("x" * 25, "x" * 255,
                      "Portable Wireless Bluetooth Speaker Waterproof Outdoor 20W"):
            payload = make_payload()
            payload["productName"] = value
            self.assertFalse(any("标题" in x for x in self.issues(payload)), value)

    def test_title_whitespace_only(self):
        payload = make_payload()
        payload["productName"] = "   "
        self.assertTrue(any("标题为空" in x for x in self.issues(payload)))

    def test_limits_injection_title(self):
        payload = make_payload()
        payload["productName"] = "Short Title"
        self.assertTrue(any("标题" in x for x in self.issues(payload)))
        self.assertFalse(any("标题" in x for x in self.issues(
            payload, limits={"nameMin": 10, "nameMax": 100})))

    def test_description(self):
        payload = make_payload()
        payload["description"] = ""
        self.assertTrue(any("描述为空" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["description"] = "a" * 10001
        self.assertTrue(any("描述长度" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["description"] = "<p>ok</p><div>bad</div>"
        self.assertTrue(any("不允许的 HTML 标签" in x
                            for x in self.issues(payload)))
        payload = make_payload()
        payload["description"] = "<p>ok</p><ul><li>a</li></ul>"
        self.assertFalse(any("HTML 标签" in x for x in self.issues(payload)))

    def test_description_limits_injection(self):
        payload = make_payload()
        payload["description"] = "<p>" + "a" * 200 + "</p>"
        self.assertFalse(any("描述长度" in x for x in self.issues(payload)))
        self.assertTrue(any("描述长度" in x for x in self.issues(
            payload, limits={"descMax": 100})))

    def test_images(self):
        payload = make_payload()
        payload["mainImage"] = ""
        self.assertTrue(any("主图数量" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["mainImage"] = "|".join(f"{IMAGE_1}?i={i}" for i in range(10))
        self.assertTrue(any("超过上限 9" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["mainImage"] = "ftp://img.example.com/1.jpg"
        self.assertTrue(any("不是 http(s)" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["mainImage"] = IMAGE_1
        self.assertFalse(any("主图" in x for x in self.issues(payload)))

    def test_price(self):
        payload = make_payload()
        payload["variationList"][0]["originalPrice"] = 0.001
        self.assertTrue(any("价格" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["variationList"][0]["originalPrice"] = 200000000
        self.assertTrue(any("超出范围" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["variationList"] = []
        payload.pop("price", None)
        self.assertTrue(any("价格必填" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["variationList"] = [
            {"sellerSku": "A", "originalPrice": 10, "availableStock": 3},
            {"sellerSku": "B", "availableStock": 3},
        ]
        self.assertTrue(any("第 2 个 SKU 价格必填" in x
                            for x in self.issues(payload)))

    def test_price_limits_injection(self):
        payload = make_payload()
        payload["variationList"][0]["originalPrice"] = 200
        self.assertFalse(any("价格" in x for x in self.issues(payload)))
        self.assertTrue(any("价格" in x for x in self.issues(
            payload, limits={"priceMax": 100})))

    def test_stock(self):
        payload = make_payload()
        payload["variationList"][0]["availableStock"] = 100000
        self.assertTrue(any("库存" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["variationList"][0]["availableStock"] = -1
        self.assertTrue(any("库存" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["variationList"][0]["availableStock"] = 0
        self.assertFalse(any("库存" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["variationList"][0]["availableStock"] = None
        self.assertTrue(any("库存必填" in x for x in self.issues(payload)))

    def test_stock_limits_injection(self):
        payload = make_payload()
        payload["variationList"][0]["availableStock"] = 50
        self.assertFalse(any("库存" in x for x in self.issues(payload)))
        self.assertTrue(any("库存" in x for x in self.issues(
            payload, limits={"stockMax": 10})))

    def test_weight_and_dimension_unit(self):
        payload = make_payload()
        payload["packageWeight"] = 0
        self.assertTrue(any("重量" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["packageWeight"] = None
        self.assertTrue(any("重量" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["packageDimensionUnit"] = "KM"
        self.assertTrue(any("尺寸单位" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["packageDimensionUnit"] = "CM"
        self.assertFalse(any("尺寸单位" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["packageLength"] = 0
        self.assertTrue(any("包裹长度" in x for x in self.issues(payload)))

    def test_us_key_features_limits(self):
        payload = make_payload()
        payload["keyProductFeatures"] = [f"F{i}: value" for i in range(6)]
        self.assertTrue(any("五点描述最多 5 条" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["keyProductFeatures"] = [f"Feature: {'a' * 400}" for _ in range(5)]
        self.assertTrue(any("五点描述总长度" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["keyProductFeatures"] = ["A: b", "C: d", "E: f"]
        self.assertFalse(any("五点描述" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["keyProductFeatures"] = ["A: b"]
        self.assertFalse(any("五点描述" in x for x in self.issues(payload)))

    def test_us_search_terms_limits(self):
        payload = make_payload()
        payload["searchTerms"] = [f"kw{i}" for i in range(16)]
        self.assertTrue(any("搜索关键词最多 15 个" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["searchTerms"] = ["x" * 30 for _ in range(10)]
        self.assertTrue(any("搜索关键词总长度" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["searchTerms"] = ["speaker"]
        self.assertFalse(any("搜索关键词" in x for x in self.issues(payload)))

    def test_non_us_key_features_only_blocks_on_publish(self):
        payload = make_payload()
        payload["site"] = "GB"
        issues = validate_tiktok_payload(payload, site="GB")
        self.assertFalse(any("不支持五点描述" in x for x in issues))
        self.assertFalse(any("不支持搜索关键词" in x for x in issues))
        publish_issues = validate_tiktok_payload(payload, site="GB", for_publish=True)
        self.assertTrue(any("不支持五点描述" in x for x in publish_issues))
        self.assertTrue(any("不支持搜索关键词" in x for x in publish_issues))

    def test_global_english_only(self):
        payload = make_payload()
        payload["productName"] = "无线蓝牙音箱 Portable Wireless Bluetooth Speaker 20W"
        issues = validate_tiktok_payload(payload, site="US", is_global=True)
        self.assertTrue(any("全球产品标题含中文" in x for x in issues))
        payload = make_payload()
        self.assertEqual(validate_tiktok_payload(payload, site="US", is_global=True), [])

    def test_sku_required(self):
        payload = make_payload()
        payload["variationList"][0]["sellerSku"] = ""
        self.assertTrue(any("缺少 sellerSku" in x for x in self.issues(payload)))
        payload = make_payload()
        payload["variationList"] = []
        payload.pop("sellerSku", None)
        self.assertTrue(any("SKU 必填" in x for x in self.issues(payload)))

    def test_validate_does_not_mutate(self):
        payload = make_payload()
        snapshot = copy.deepcopy(payload)
        validate_tiktok_payload(payload, site="US")
        build_warnings(payload, site="US")
        self.assertEqual(payload, snapshot)

    def test_build_with_non_site_content_still_shaped(self):
        # 站点不支持五点/关键词时 build 不擅自丢弃（发布前由校验阻断）
        payload = build_tiktok_payload(make_detail(), None,
                                       {"key_features": ["A: b", "C: d", "E: f"]},
                                       site="US")
        self.assertEqual(len(payload["keyProductFeatures"]), 3)
        switched = build_tiktok_payload(make_detail(), None, None, site="GB")
        self.assertEqual(switched["site"], "GB")
        self.assertEqual(switched["categoryId"], "")  # 切站点必须重选类目


class WarningsTests(unittest.TestCase):
    def warns(self, payload, site=""):
        return build_warnings(payload, site=site or payload.get("site") or "US")

    def test_valid_payload_no_warnings(self):
        self.assertEqual(self.warns(make_payload()), [])

    def test_warning_title_short(self):
        payload = make_payload()
        payload["productName"] = "Short Speaker"
        self.assertTrue(any("标题" in w for w in self.warns(payload)))

    def test_warning_title_chinese(self):
        payload = make_payload()
        payload["productName"] = "无线蓝牙音箱 Portable Wireless Bluetooth Speaker"
        warns = self.warns(payload)
        self.assertTrue(any("中文字符" in w for w in warns))

    def test_warning_images_and_brand_certs(self):
        payload = make_payload()
        payload["mainImage"] = IMAGE_1
        payload["brandId"] = ""
        payload["brandInfo"] = ""
        payload["productCertifications"] = ""
        warns = self.warns(payload)
        self.assertTrue(any("主图仅 1 张" in w for w in warns))
        self.assertTrue(any("品牌" in w for w in warns))
        self.assertTrue(any("资质" in w for w in warns))

    def test_warning_stock_price(self):
        payload = make_payload()
        payload["variationList"][0]["availableStock"] = 0
        self.assertTrue(any("库存为 0" in w for w in self.warns(payload)))
        payload = make_payload()
        payload["variationList"] = []
        payload.pop("price", None)
        warns = self.warns(payload)
        self.assertTrue(any("价格为空" in w for w in warns))
        self.assertTrue(any("库存为空" in w for w in warns))

    def test_warning_description_without_list(self):
        payload = make_payload()
        payload["description"] = "<p>Just a paragraph without parameter list.</p>"
        self.assertTrue(any("参数列表" in w for w in self.warns(payload)))

    def test_warning_non_us_features(self):
        payload = make_payload()
        payload["site"] = "GB"
        warns = self.warns(payload, site="GB")
        self.assertTrue(any("不支持五点描述" in w for w in warns))
        self.assertTrue(any("不支持搜索关键词" in w for w in warns))

    def test_warning_promo_contact(self):
        payload = make_payload()
        payload["productName"] = ("Best Portable Wireless Bluetooth Speaker "
                                  "100% Free Shipping Hot Sale Outdoor 20W")
        warns = self.warns(payload)
        self.assertTrue(any("促销" in w for w in warns))

    def test_warning_warehouse_weight(self):
        payload = make_payload()
        payload["warehouseId"] = ""
        payload["packageWeight"] = None
        warns = self.warns(payload)
        self.assertTrue(any("仓库" in w for w in warns))
        self.assertTrue(any("重量" in w for w in warns))

    def test_warning_certification_rules(self):
        payload = make_payload()
        payload["productCertifications"] = ""
        payload["productCertificationsRules"] = {"required": ["CE"]}
        warns = self.warns(payload)
        self.assertTrue(any("类目要求资质" in w for w in warns))


class ApplySiteTests(unittest.TestCase):
    def test_switch_clears_category_and_warehouse(self):
        payload = make_payload()
        switched = apply_site(payload, "de")
        self.assertEqual(switched["site"], "DE")
        self.assertEqual(switched["categoryId"], "")
        self.assertEqual(switched["categoryList"], [])
        self.assertEqual(switched["isNewCategory"], 0)
        self.assertEqual(switched["warehouseId"], "")
        self.assertEqual(switched["mainImage"], payload["mainImage"])
        self.assertEqual(switched["productName"], payload["productName"])

    def test_same_site_keeps_category(self):
        payload = make_payload()
        switched = apply_site(payload, "US")
        self.assertEqual(switched["categoryId"], payload["categoryId"])
        self.assertEqual(switched["warehouseId"], payload["warehouseId"])

    def test_shop_id_updated_only_when_given(self):
        payload = make_payload()
        self.assertEqual(apply_site(payload, "GB")["shopId"], payload["shopId"])
        self.assertEqual(apply_site(payload, "GB", shop_id="NEW")["shopId"], "NEW")

    def test_site_ids_codes_synced(self):
        payload = make_payload()
        payload["siteIds"] = ["US"]
        self.assertEqual(apply_site(payload, "DE")["siteIds"], ["DE"])

    def test_opaque_site_ids_untouched(self):
        payload = make_payload()
        payload["siteIds"] = ["123456789"]
        self.assertEqual(apply_site(payload, "DE")["siteIds"], ["123456789"])

    def test_returns_copy(self):
        payload = make_payload()
        snapshot = copy.deepcopy(payload)
        apply_site(payload, "DE")
        self.assertEqual(payload, snapshot)


class PriceStockTests(unittest.TestCase):
    def test_single_row_update(self):
        payload = make_payload()
        out = price_stock_overrides(payload, "25.5", "12", "SPK-001")
        row = out["variationList"][0]
        self.assertEqual(row["originalPrice"], 25.5)
        self.assertEqual(row["availableStock"], 12)
        self.assertEqual(row["sellerSku"], "SPK-001")
        # 入参不变
        self.assertEqual(payload["variationList"][0]["originalPrice"], 19.99)

    def test_single_row_sku_rename(self):
        payload = make_payload()
        out = price_stock_overrides(payload, None, None, "NEW-SKU")
        self.assertEqual(out["variationList"][0]["sellerSku"], "NEW-SKU")

    def test_invalid_price_ignored(self):
        payload = make_payload()
        out = price_stock_overrides(payload, "abc", "")
        self.assertEqual(out["variationList"][0]["originalPrice"], 19.99)

    def test_multi_rows_without_sku_untouched(self):
        detail = make_detail(variationList=[
            {"sellerSku": "A", "originalPrice": 10, "availableStock": 3},
            {"sellerSku": "B", "originalPrice": 20, "availableStock": 4},
        ])
        payload = build_tiktok_payload(detail, None, None, site="US")
        out = price_stock_overrides(payload, 99, 77)
        self.assertEqual(out["variationList"][0]["originalPrice"], 10)
        self.assertEqual(out["variationList"][1]["originalPrice"], 20)

    def test_multi_rows_with_unique_sku(self):
        detail = make_detail(variationList=[
            {"sellerSku": "A", "originalPrice": 10, "availableStock": 3},
            {"sellerSku": "B", "originalPrice": 20, "availableStock": 4},
        ])
        payload = build_tiktok_payload(detail, None, None, site="US")
        out = price_stock_overrides(payload, "15.5", "9", "B")
        self.assertEqual(out["variationList"][0]["originalPrice"], 10)
        self.assertEqual(out["variationList"][1]["originalPrice"], 15.5)
        self.assertEqual(out["variationList"][1]["availableStock"], 9)

    def test_multi_rows_unknown_sku_untouched(self):
        detail = make_detail(variationList=[
            {"sellerSku": "A", "originalPrice": 10, "availableStock": 3},
            {"sellerSku": "B", "originalPrice": 20, "availableStock": 4},
        ])
        payload = build_tiktok_payload(detail, None, None, site="US")
        out = price_stock_overrides(payload, 99, 77, "NOPE")
        self.assertEqual(out["variationList"][0]["originalPrice"], 10)
        self.assertEqual(out["variationList"][1]["originalPrice"], 20)

    def test_top_level_for_single_product_without_rows(self):
        payload = build_tiktok_payload({}, None, None)
        self.assertEqual(payload["variationList"], [])
        out = price_stock_overrides(payload, "9.99", "5", "S-1")
        self.assertEqual(out["price"], 9.99)
        self.assertEqual(out["quantity"], 5)
        self.assertEqual(out["sellerSku"], "S-1")
        self.assertEqual(payload_price(out), 9.99)
        self.assertEqual(payload_stock(out), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
