"""Amazon 刊登流水线 / 保存收口离线单测（无网络、无付费 AI、绝不真实保存）。

跑法：``.venv/bin/python tests/test_amazon_pipeline.py``
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.config import AppConfig
from aeclone.integrations.amazon import AmazonClient
from aeclone.services import amazon_publish as PUB
from aeclone.services import amazon_pipeline as PL
from aeclone.services.amazon_payload import SAVE_KEYS, join_bullets
from aeclone.services.amazon_pipeline import (
    OP_LABELS, OP_MOVE_PENDING, OP_PUBLISH, OP_SAVE, AmazonRunResult, AmazonStep,
    _autofill_attributes_with_notes, ai_generate_all, ai_generate_field,
    autofill_attributes, forced_preview, prepare_save, required_attributes_from_schema,
    run_amazon_product, verify_publish,
)
from aeclone.services.amazon_payload import (
    REQUIRED_COMMON, missing_common_attributes, parse_product_attr,
)

ENV_KEYS = ("AURORA_NO_SAVE", "AURORA_TEST", "AURORA_SKIP_LICENSE")


def make_product(**over):
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
            "is_assembly_required": [{"value": False, "marketplace_id": "A2EUQ1WTGCTBG2"}]}),
        "standardPrice": 15999.0,
        "saleSalePrice": 14999.0,
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


def make_detail(**over):
    product = make_product(**over)
    return {"site": "CA", "product": product, "categoryName": "反铲",
            "productType": "OUTDOOR_LIVING", "categoryTree": "[]"}


class FakeClient:
    """离线替身：读接口返回固定详情；save_product 只记录不联网。"""

    def __init__(self, detail=None, template=None, publish_results=None):
        self.detail = detail if detail is not None else make_detail()
        self.saved = []
        self.category_queries = []
        self.template = template if isinstance(template, dict) else {}
        self.template_queries = []
        self.publish_results = publish_results
        self.publish_queries = []

    def product_detail(self, pid):
        return self.detail

    def currency(self, shop_id, marketplace_id):
        return "CAD"

    def category_by_path(self, site_code, node_path):
        self.category_queries.append((site_code, node_path))
        if "bad" in node_path:
            raise RuntimeError("分类数据加载失败")
        return {"id": 159365, "categoryId": "13553005011", "chineseName": "反铲"}

    def check_banned_words(self, **kwargs):
        return []

    def save_product(self, payload, op="1"):
        self.saved.append((dict(payload), str(op)))
        return {"code": 0, "msg": "Successful", "data": {"id": payload.get("id")}}

    def attribute_template(self, category_id, marketplace_id, shop_id="-1"):
        self.template_queries.append((str(category_id), str(marketplace_id),
                                      str(shop_id)))
        return dict(self.template)

    def publish_state(self, product_id, shop_id="-1"):
        self.publish_queries.append((str(product_id), str(shop_id)))
        seq = self.publish_results
        if isinstance(seq, list):
            if not seq:
                return {"state": "online", "err_msg": "", "asin": "", "title": "",
                        "counts": {}}
            idx = min(len(self.publish_queries) - 1, len(seq) - 1)
            return dict(seq[idx])
        if isinstance(seq, dict):
            return dict(seq)
        return {"state": "online", "err_msg": "", "asin": "B0TESTASIN",
                "title": "Backhoe", "counts": {"draft": 14}}

    def required_attributes_from_schema(self, category_id, product_type, site_code):
        return list((self.template or {}).get("_schema_required") or [])


class EnvIsolatedCase(unittest.TestCase):
    def setUp(self):
        self._saved_env = {k: os.environ.pop(k, None) for k in ENV_KEYS}

    def tearDown(self):
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class ConstantsTests(unittest.TestCase):
    def test_config_defaults_present(self):
        keys = [
            "amazon_cookies", "amazon_shop_id", "amazon_shop_name", "amazon_site",
            "amazon_site_code", "amazon_site_name", "amazon_test_product_id",
            "amazon_list_state", "amazon_category_id", "amazon_category_path",
            "amazon_category_name", "amazon_last_category_required", "amazon_price",
            "amazon_sale_price", "amazon_quantity", "amazon_fulfillment",
            "amazon_handling_time", "amazon_shipping_template", "amazon_brand",
            "amazon_manufacturer", "amazon_sku", "amazon_bullet_count", "amazon_op",
            "amazon_use_ai", "amazon_ai_preset", "amazon_title_prompt",
            "amazon_bullet_prompt", "amazon_description_prompt",
            "amazon_search_terms_prompt", "amazon_last_status",
            "amazon_manufacturer_contact", "amazon_publish_wait",
        ]
        cfg = AppConfig()
        for key in keys:
            self.assertTrue(hasattr(cfg, key), key)
        self.assertEqual(cfg.amazon_fulfillment, "MFN")
        self.assertEqual(cfg.amazon_bullet_count, 5)
        self.assertIsInstance(cfg.amazon_use_ai, bool)  # 安装预设可选择 AI 默认值
        self.assertEqual(cfg.amazon_list_state, "draft")

    def test_op_constants_and_labels(self):
        self.assertEqual((OP_SAVE, OP_MOVE_PENDING, OP_PUBLISH), ("1", "3", "2"))
        self.assertEqual(OP_LABELS[OP_SAVE], "保存草稿")
        self.assertEqual(OP_LABELS[OP_MOVE_PENDING], "保存并移入待发布")
        self.assertEqual(OP_LABELS[OP_PUBLISH], "保存并发布")

    def test_run_result_labels(self):
        res = AmazonRunResult(product_id="1", preview=True, ok=True)
        self.assertIn("预演完成", res.completion_label)
        res = AmazonRunResult(product_id="1", preview=True, ok=False)
        self.assertIn("预演失败", res.completion_label)
        res = AmazonRunResult(product_id="1", ok=True, saved=True)
        self.assertEqual(res.completion_label, "保存成功")
        res = AmazonRunResult(product_id="1", ok=False)
        self.assertEqual(res.completion_label, "保存失败")

    def test_log(self):
        res = AmazonRunResult(product_id="1", steps=[AmazonStep("a", True, "ok"),
                                                    AmazonStep("b", False, "bad")])
        log = res.log()
        self.assertIn("✓ a: ok", log)
        self.assertIn("✗ b: bad", log)
        self.assertEqual(len(res.incomplete_steps), 1)


class ForcedPreviewTests(EnvIsolatedCase):
    def test_forced_preview(self):
        self.assertFalse(forced_preview())
        os.environ["AURORA_NO_SAVE"] = "1"
        self.assertTrue(forced_preview())
        del os.environ["AURORA_NO_SAVE"]
        os.environ["AURORA_TEST"] = "1"
        self.assertTrue(forced_preview())


class PrepareSaveTests(EnvIsolatedCase):
    def test_prepare_save_read_only_39_keys(self):
        fake = FakeClient()
        detail, payload, issues, warnings = prepare_save(
            "220114595363182310", AppConfig(), client=fake)
        self.assertEqual(detail["site"], "CA")
        self.assertEqual(list(payload.keys()), SAVE_KEYS)
        self.assertEqual(len(payload), 39)
        self.assertEqual(issues, [])
        self.assertIsInstance(warnings, list)
        self.assertEqual(fake.saved, [])
        self.assertEqual(payload["title"], make_product()["title"])
        self.assertEqual(payload["currency"], "CAD")

    def test_prepare_save_applies_overrides(self):
        detail, payload, issues, warnings = prepare_save(
            "220114595363182310", AppConfig(),
            overrides={"title": "Manual Title", "bullets": ["a", "b", "c"],
                       "standard_price": "123.5", "quantity": "8"},
            client=FakeClient())
        self.assertEqual(payload["title"], "Manual Title")
        self.assertEqual(payload["bulletPoint"], "a±b±c")
        self.assertEqual(payload["standardPrice"], 123.5)
        self.assertEqual(payload["quantity"], 8)

    def test_prepare_save_missing_id(self):
        with self.assertRaises(ValueError):
            prepare_save("", AppConfig(), client=FakeClient())


class RunDryRunTests(EnvIsolatedCase):
    def test_default_dry_run_never_saves(self):
        fake = FakeClient()
        res = run_amazon_product("220114595363182310", AppConfig(), client=fake)
        self.assertTrue(res.ok, res.error)
        self.assertTrue(res.preview)
        self.assertFalse(res.saved)
        self.assertEqual(fake.saved, [])
        self.assertEqual(res.op, OP_SAVE)
        self.assertEqual(res.site, "A2EUQ1WTGCTBG2")
        self.assertEqual(res.site_code, "CA")
        self.assertEqual(res.currency, "CAD")
        self.assertIn("预演", res.log())
        self.assertEqual(len(res.bullets), 5)
        self.assertIn("未保存商品", res.completion_label)

    def test_dry_run_with_env_no_save_still_no_save(self):
        os.environ["AURORA_NO_SAVE"] = "1"
        fake = FakeClient()
        res = run_amazon_product("220114595363182310", AppConfig(), client=fake,
                                 dry_run=True)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(fake.saved, [])

    def test_dry_run_op_move_pending_label(self):
        fake = FakeClient()
        res = run_amazon_product("220114595363182310", AppConfig(), client=fake,
                                 dry_run=True, op=OP_MOVE_PENDING)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.op, OP_MOVE_PENDING)
        self.assertIn("保存并移入待发布", res.log())

    def test_unknown_op_fails(self):
        fake = FakeClient()
        res = run_amazon_product("220114595363182310", AppConfig(), client=fake,
                                 op="9")
        self.assertFalse(res.ok)
        self.assertIn("未知操作", res.error)
        self.assertEqual(fake.saved, [])

    def test_missing_product_id_fails(self):
        res = run_amazon_product("", AppConfig(), client=FakeClient())
        self.assertFalse(res.ok)
        self.assertIn("缺少 product_id", res.error)

    def test_validation_issues_block(self):
        fake = FakeClient(detail=make_detail(title=""))
        res = run_amazon_product("220114595363182310", AppConfig(), client=fake,
                                 dry_run=True)
        self.assertFalse(res.ok)
        self.assertTrue(res.preview)
        self.assertFalse(res.saved)
        self.assertTrue(any("标题为空" in i for i in res.issues))
        self.assertIn("保存前校验未通过", res.error)
        self.assertEqual(fake.saved, [])

    def test_forced_preview_on_real_save(self):
        os.environ["AURORA_NO_SAVE"] = "1"
        fake = FakeClient()
        res = run_amazon_product("220114595363182310", AppConfig(), client=fake,
                                 dry_run=False, confirm_write=True)
        # 验收环境必须明确拒绝真实保存：ok=False/saved=False，且不调用 save_product
        self.assertFalse(res.ok)
        self.assertFalse(res.saved)
        self.assertTrue(res.preview)
        self.assertIn("拒绝真实保存", res.error)
        self.assertEqual(fake.saved, [])
        self.assertIn("安全护栏", res.log())

    def test_forced_preview_never_calls_save_product(self):
        class NoSaveClient(FakeClient):
            def save_product(self, payload, op="1"):
                raise AssertionError("AURORA_NO_SAVE 下不允许调用 save_product")

        os.environ["AURORA_NO_SAVE"] = "1"
        os.environ["AURORA_SKIP_LICENSE"] = "1"   # 即便授权放行也必须拒绝真实写
        res = run_amazon_product("220114595363182310", AppConfig(),
                                 client=NoSaveClient(), dry_run=False,
                                 confirm_write=True)
        self.assertFalse(res.ok)
        self.assertFalse(res.saved)
        self.assertTrue(res.preview)
        self.assertIn("拒绝真实保存", res.error)

    def test_forced_preview_env_test(self):
        os.environ["AURORA_TEST"] = "1"
        fake = FakeClient()
        res = run_amazon_product("220114595363182310", AppConfig(), client=fake,
                                 dry_run=False, confirm_write=True)
        self.assertFalse(res.ok)
        self.assertFalse(res.saved)
        self.assertTrue(res.preview)
        self.assertEqual(fake.saved, [])

    def test_category_override_verified_by_remote(self):
        fake = FakeClient()
        res = run_amazon_product(
            "220114595363182310", AppConfig(), client=fake, dry_run=True,
            overrides={"category_path": "6299024011/6256963011/6256964011/13553005011"})
        self.assertTrue(res.ok, res.error)
        self.assertEqual(len(fake.category_queries), 1)
        self.assertIn("类目校验", res.log())

    def test_prepare_save_rejects_a_parent_category_before_confirmation(self):
        fake = FakeClient()
        fake.category_by_path = lambda *args: {"categoryId": "13553005011", "leafCategory": "false"}
        _, _, issues, _ = prepare_save("220114595363182310", AppConfig(), client=fake,
                                       overrides={"category_path": "6299024011/13553005011"})
        self.assertTrue(any("末级类目" in issue for issue in issues), issues)

    def test_bad_category_override_blocks(self):
        fake = FakeClient()
        res = run_amazon_product(
            "220114595363182310", AppConfig(), client=fake, dry_run=True,
            overrides={"category_path": "bad/path"})
        self.assertFalse(res.ok)
        self.assertTrue(any("类目路径" in i for i in res.issues))

    def test_banned_words_are_warnings_only(self):
        fake = FakeClient()

        def banned(**kwargs):
            return ["best", "100%"]
        fake.check_banned_words = banned
        res = run_amazon_product("220114595363182310", AppConfig(), client=fake,
                                 dry_run=True)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.banned_words, ["best", "100%"])
        self.assertTrue(any("违禁词命中" in w for w in res.warnings))

    def test_product_detail_failure_is_reported(self):
        class Broken(FakeClient):
            def product_detail(self, pid):
                raise RuntimeError("网络请求失败")
        res = run_amazon_product("220114595363182310", AppConfig(), client=Broken())
        self.assertFalse(res.ok)
        self.assertIn("读取商品", res.error)

    def test_client_creation_failure_is_reported(self):
        with patch.object(PL, "make_client", side_effect=RuntimeError("没有 Cookie")):
            res = run_amazon_product("220114595363182310", AppConfig(), client=None)
        self.assertFalse(res.ok)
        self.assertIn("创建客户端失败", res.error)


class WriteConfirmationGuardTests(EnvIsolatedCase):
    """防脚本误写硬护栏：dry_run=False 必须显式 confirm_write=True。"""

    def test_confirm_write_required_blocks_real_save(self):
        fake = FakeClient()
        res = run_amazon_product("220114595363182310", AppConfig(), client=fake,
                                 dry_run=False)   # 故意不传 confirm_write
        self.assertFalse(res.ok)
        self.assertFalse(res.saved)
        self.assertFalse(res.preview)
        self.assertIn("confirm_write=True", res.error)
        self.assertIn("写入确认", res.log())
        self.assertEqual(fake.saved, [])
        # 连详情都不读：确认护栏在连接/读详情之前就拦住
        self.assertNotIn("读取商品详情", res.log())

    def test_confirm_write_true_uses_injected_client_only(self):
        """confirm_write=True + 注入 FakeClient：只调用 FakeClient.save_product，不新建客户端。"""
        os.environ["AURORA_SKIP_LICENSE"] = "1"
        fake = FakeClient()
        with patch.object(PL, "make_client", side_effect=AssertionError(
                "不许新建真实客户端（pipeline）")), \
             patch.object(PUB, "make_client", side_effect=AssertionError(
                 "不许新建真实客户端（publish）")):
            res = run_amazon_product("220114595363182310", AppConfig(),
                                     client=fake, dry_run=False,
                                     confirm_write=True)
        self.assertTrue(res.ok, res.error)
        self.assertTrue(res.saved)
        self.assertEqual(len(fake.saved), 1)
        self.assertEqual(len(fake.saved[0][0]), 39)

    def test_dry_run_true_ignores_confirm_write(self):
        fake = FakeClient()
        res = run_amazon_product("220114595363182310", AppConfig(), client=fake,
                                 dry_run=True, confirm_write=False)
        self.assertTrue(res.ok, res.error)
        self.assertTrue(res.preview)
        self.assertEqual(fake.saved, [])


class RealSavePathTests(EnvIsolatedCase):
    def _fake_save_env(self, fake):
        os.environ["AURORA_SKIP_LICENSE"] = "1"
        return patch.object(PUB, "make_client", return_value=fake)

    def test_license_required(self):
        fake = FakeClient()
        res = run_amazon_product("220114595363182310", AppConfig(), client=fake,
                                 dry_run=False, confirm_write=True)
        self.assertFalse(res.ok)
        self.assertIn("授权校验", res.error)
        self.assertFalse(res.saved)
        self.assertEqual(fake.saved, [])

    def test_real_save_success_goes_through_publish(self):
        fake = FakeClient()
        with self._fake_save_env(fake):
            res = run_amazon_product("220114595363182310", AppConfig(), client=fake,
                                     dry_run=False, op=OP_MOVE_PENDING,
                                     confirm_write=True)
        self.assertTrue(res.ok, res.error)
        self.assertTrue(res.saved)
        self.assertFalse(res.preview)
        self.assertEqual(len(fake.saved), 1)
        payload, op = fake.saved[0]
        self.assertEqual(op, OP_MOVE_PENDING)
        self.assertEqual(list(payload.keys()), SAVE_KEYS)
        self.assertEqual(res.save_result.get("code"), 0)
        self.assertIn("保存并移入待发布成功", res.log())

    def test_real_write_2_with_ai_copy_overrides(self):
        """模拟 Lead 真实写 #2：dry_run=False + AI 文案 overrides，走 save_to_amazon。"""
        os.environ["AURORA_SKIP_LICENSE"] = "1"
        fake = FakeClient()
        ai_over = {
            "title": "5 Ton Diesel Wheeled Backhoe Loader Heavy Duty Construction Machine",
            "bullets": ["POWERFUL ENGINE: 75 KW diesel power",
                        "COMPACT 4X4 CHASSIS: easy transport",
                        "HIGH LOAD MOMENT: heavy duty lifting",
                        "LOW FUEL COST: efficient operation",
                        "EASY MAINTENANCE: quick service access"],
            "description": "<p>Heavy duty backhoe loader for construction.</p>"
                           "<ul><li>Engine: 75 KW diesel</li></ul>",
            "search_terms": "backhoe loader diesel construction machine",
        }
        with patch.object(PUB, "make_client", return_value=fake):
            res = run_amazon_product("220114595363182310", AppConfig(),
                                     client=fake, dry_run=False,
                                     overrides=ai_over, op=OP_SAVE,
                                     confirm_write=True)
        self.assertTrue(res.ok, res.error)
        self.assertTrue(res.saved)
        self.assertEqual(len(fake.saved), 1)
        sent, op = fake.saved[0]
        self.assertEqual(op, OP_SAVE)
        self.assertEqual(len(sent), 39)
        self.assertEqual(list(sent), SAVE_KEYS)
        self.assertEqual(sent["title"], ai_over["title"])
        self.assertEqual(sent["bulletPoint"], "±".join(ai_over["bullets"]))
        self.assertEqual(sent["description"], ai_over["description"])
        self.assertEqual(sent["searchTerms"], ai_over["search_terms"])
        self.assertEqual(sent["marketPlaceId"], "A2EUQ1WTGCTBG2")
        self.assertEqual(sent["currency"], "CAD")
        # productAttr：原有属性原样保留 + 缺失的发布必填属性已自动补齐
        sent_attr = parse_product_attr(sent["productAttr"])
        raw_attr = parse_product_attr(make_product()["productAttr"])
        for key, value in raw_attr.items():
            self.assertEqual(sent_attr[key], value)
        self.assertEqual(sent_attr["model_number"][0]["value"], "WY-QHCA-LTM-1")
        self.assertEqual(sent_attr["part_number"][0]["value"], "WY-QHCA-LTM-1")
        self.assertEqual(sent_attr["model_name"][0]["value"], "WY-QHCA-LTM-1")
        self.assertNotIn("manufacturer_contact_information", sent_attr)
        self.assertEqual(
            missing_common_attributes(sent["productAttr"]), ["manufacturer_contact_information"])

    def test_real_save_failure_message(self):
        class SaveFail(FakeClient):
            def save_product(self, payload, op="1"):
                return {"code": -1, "msg": "保存失败：类目不可用", "data": None}
        fake = SaveFail()
        with self._fake_save_env(fake):
            res = run_amazon_product("220114595363182310", AppConfig(), client=fake,
                                     dry_run=False, confirm_write=True)
        self.assertFalse(res.ok)
        self.assertFalse(res.saved)
        self.assertIn("类目不可用", res.error)


def _hw_detail(**over):
    return make_detail(title="5 Ton Diesel Wheeled Backhoe Loader 4x4 HW20-28 "
                             "Heavy Duty Construction Machine",
                       description="<p>Heavy duty backhoe loader, made in China.</p>",
                       **over)


class AutofillAttributesTests(unittest.TestCase):
    """task-8：必填属性自动补齐（错误驱动 + 同类在线商品模板）。"""

    def test_derive_from_title_and_sku(self):
        filled = autofill_attributes(_hw_detail()["product"], AppConfig(),
                                     "A2EUQ1WTGCTBG2")
        self.assertEqual(filled["model_number"], "HW20-28")
        self.assertEqual(filled["model_name"], "HW20-28")
        self.assertEqual(filled["part_number"], "WY-QHCA-LTM-1")
        self.assertNotIn("manufacturer_contact_information", filled)

    def test_existing_and_override_not_overwritten(self):
        product = _hw_detail()["product"]
        product["productAttr"] = json.dumps({
            "model_number": [{"value": "EXISTING", "language_tag": "en_CA",
                              "marketplace_id": "A2EUQ1WTGCTBG2"}]})
        filled = autofill_attributes(product, AppConfig(), "A2EUQ1WTGCTBG2")
        self.assertNotIn("model_number", filled)      # 已有非空 → 不补
        self.assertIn("part_number", filled)
        # overrides 显式 attributes 优先
        filled2 = autofill_attributes(
            product, AppConfig(), "A2EUQ1WTGCTBG2",
            overrides={"attributes": {"part_number": "OVERRIDE-PN"}})
        self.assertNotIn("part_number", filled2)

    def test_required_subset_only(self):
        filled = autofill_attributes(_hw_detail()["product"], AppConfig(),
                                     "A2EUQ1WTGCTBG2",
                                     required=["part_number"])
        self.assertEqual(list(filled), ["part_number"])

    def test_cfg_manufacturer_contact(self):
        cfg = AppConfig()
        cfg.set("amazon_manufacturer_contact", "ACME Ltd, Shenzhen, CN")
        filled = autofill_attributes(_hw_detail()["product"], cfg,
                                     "A2EUQ1WTGCTBG2",
                                     required=["manufacturer_contact_information"])
        self.assertEqual(filled["manufacturer_contact_information"],
                         "ACME Ltd, Shenzhen, CN")

    def test_template_only_supplies_price_shape(self):
        template = {
            "power_source_type": [{"value": "diesel",
                                   "marketplace_id": "A2EUQ1WTGCTBG2"}],
            "item_depth_width_height": [{
                "length": {"value": "102", "unit": "inches"},
                "width": {"value": "77", "unit": "inches"},
                "height": {"value": "110", "unit": "inches"},
                "marketplace_id": "A2EUQ1WTGCTBG2"}],
            "supplier_declared_dg_hz_regulation": [{
                "value": "not_applicable", "marketplace_id": "A2EUQ1WTGCTBG2"}],
            "list_price": [{"value": {"currency": "CAD", "amount": 8900.0},
                            "marketplace_id": "A2EUQ1WTGCTBG2"}],
        }
        filled = autofill_attributes(
            _hw_detail()["product"], AppConfig(), "A2EUQ1WTGCTBG2",
            required=["power_source_type", "item_depth_width_height",
                      "supplier_declared_dg_hz_regulation", "list_price"],
            template=template)
        self.assertEqual(filled["power_source_type"], "diesel_powered")
        self.assertNotIn("item_depth_width_height", filled)
        self.assertNotIn("supplier_declared_dg_hz_regulation", filled)
        # list_price 以模板结构为准，amount=商品库存标准价，currency=站点币种
        self.assertEqual(filled["list_price"][0]["value"],
                         {"currency": "CAD", "amount": 15999.0})

    def test_nine_missing_attributes_scenario(self):
        required = ["item_depth_width_height", "model_number", "part_number",
                    "model_name", "country_of_origin",
                    "supplier_declared_dg_hz_regulation", "list_price",
                    "power_source_type", "manufacturer_contact_information"]
        template = {
            "item_depth_width_height": [{
                "length": {"value": "102", "unit": "inches"},
                "width": {"value": "77", "unit": "inches"},
                "height": {"value": "110", "unit": "inches"},
                "marketplace_id": "A2EUQ1WTGCTBG2"}],
            "supplier_declared_dg_hz_regulation": [{
                "value": "not_applicable", "marketplace_id": "A2EUQ1WTGCTBG2"}],
            "list_price": [{"value": {"currency": "CAD", "amount": 8900.0},
                            "marketplace_id": "A2EUQ1WTGCTBG2"}],
            "power_source_type": [{"value": "diesel",
                                   "marketplace_id": "A2EUQ1WTGCTBG2"}],
        }
        filled = autofill_attributes(_hw_detail()["product"], AppConfig(),
                                     "A2EUQ1WTGCTBG2", required=required,
                                     template=template)
        self.assertEqual(set(filled), set(required) - {"item_depth_width_height", "supplier_declared_dg_hz_regulation",
                                                     "manufacturer_contact_information"})
        self.assertEqual(filled["country_of_origin"], "CN")
        self.assertEqual(filled["model_number"], "HW20-28")

    def test_unknown_attribute_goes_to_notes(self):
        filled, notes = _autofill_attributes_with_notes(
            _hw_detail()["product"], AppConfig(), "A2EUQ1WTGCTBG2",
            required=["some_unknown_attribute"], template={})
        self.assertEqual(filled, {})
        self.assertTrue(any("some_unknown_attribute" in n and "无法自动补齐" in n
                            for n in notes))


class DeterministicAttributeMappingTests(unittest.TestCase):
    """task-8 收尾：错误驱动第二轮确定性映射（无 template 也能补）。"""

    def test_missing_booleans_are_not_invented(self):
        product = _hw_detail()["product"]
        product["productAttr"] = "{}"
        filled = autofill_attributes(
            product, AppConfig(), "A2EUQ1WTGCTBG2",
            required=["contains_liquid_contents", "batteries_required",
                      "is_expiration_dated_product", "is_assembly_required"])
        self.assertNotIn("contains_liquid_contents", filled)
        self.assertNotIn("batteries_required", filled)
        self.assertNotIn("is_expiration_dated_product", filled)
        self.assertNotIn("is_assembly_required", filled)

    def test_is_assembly_required_true_when_title_mentions_assembly(self):
        product = _hw_detail()["product"]
        product["productAttr"] = "{}"
        product["title"] = product["title"] + " assembly required"
        filled = autofill_attributes(product, AppConfig(), "A2EUQ1WTGCTBG2",
                                     required=["is_assembly_required"])
        self.assertIs(filled["is_assembly_required"], True)

    def test_power_source_enum_mapping(self):
        cases = {
            "5 Ton Diesel Wheeled Backhoe Loader": "diesel_powered",
            "Hydraulic Concrete Breaker": "hydraulic_powered",
            "Gasoline Engine Water Pump": "gasoline_powered",
            "Battery Powered Garden Tool": "Battery Powered",
            "Electric Lawn Mower": "electric",
        }
        for title, expected in cases.items():
            product = _hw_detail()["product"]
            product["title"] = title
            filled = autofill_attributes(product, AppConfig(), "A2EUQ1WTGCTBG2",
                                         required=["power_source_type"])
            self.assertEqual(filled["power_source_type"], expected, title)

    def test_unknown_power_source_goes_to_notes(self):
        product = _hw_detail()["product"]
        product["title"] = "Heavy Duty Construction Machine"
        filled, notes = _autofill_attributes_with_notes(
            product, AppConfig(), "A2EUQ1WTGCTBG2",
            required=["power_source_type"], template={})
        self.assertNotIn("power_source_type", filled)
        self.assertTrue(any("需用户填写" in n for n in notes))

    def test_unknown_dangerous_goods_classification_is_unset(self):
        filled = autofill_attributes(_hw_detail()["product"], AppConfig(),
                                     "A2EUQ1WTGCTBG2",
                                     required=["supplier_declared_dg_hz_regulation"])
        self.assertNotIn("supplier_declared_dg_hz_regulation", filled)

    def test_unknown_origin_goes_to_notes(self):
        product = _hw_detail()["product"]
        product["description"] = "<p>Heavy duty backhoe loader.</p>"
        product["title"] = "5 Ton Diesel Wheeled Backhoe Loader HW20-28"
        filled, notes = _autofill_attributes_with_notes(
            product, AppConfig(), "A2EUQ1WTGCTBG2",
            required=["country_of_origin"], template={})
        self.assertNotIn("country_of_origin", filled)
        self.assertTrue(any("需用户填写" in n for n in notes))

    def test_list_price_default_format(self):
        filled = autofill_attributes(_hw_detail()["product"], AppConfig(),
                                     "A2EUQ1WTGCTBG2",
                                     required=["list_price"])
        self.assertEqual(filled["list_price"], [{
            "currency": "CAD", "value": "15999.00",
            "marketplace_id": "A2EUQ1WTGCTBG2"}])

    def test_item_dimensions_reused_from_product_attr(self):
        product = _hw_detail()["product"]
        product["productAttr"] = json.dumps({
            "item_dimensions": [{
                "length": {"unit": "inches", "value": "196"},
                "width": {"unit": "inches", "value": "63"},
                "height": {"unit": "inches", "value": "98"},
                "marketplace_id": "A2EUQ1WTGCTBG2"}],
        })
        # 已存在 item_dimensions → 不重复补
        self.assertEqual(autofill_attributes(product, AppConfig(), "A2EUQ1WTGCTBG2",
                                             required=["item_dimensions"]), {})
        # item_depth_width_height 复用同一 productAttr item_dimensions（复制改键）
        filled2 = autofill_attributes(product, AppConfig(), "A2EUQ1WTGCTBG2",
                                      required=["item_depth_width_height"])
        self.assertEqual(filled2["item_depth_width_height"][0]["height"]["value"], "98")

    def test_item_dimensions_regex_from_text(self):
        product = _hw_detail()["product"]
        product["description"] = ("<p>Backhoe loader. Dimension: "
                                  "6200*1960*2800mm, weight 5400kg.</p>")
        filled = autofill_attributes(product, AppConfig(), "A2EUQ1WTGCTBG2",
                                     required=["item_dimensions"])
        item = filled["item_dimensions"][0]
        self.assertEqual(item["length"], {"value": "244", "unit": "inches"})
        self.assertEqual(item["width"], {"value": "77", "unit": "inches"})
        self.assertEqual(item["height"], {"value": "110", "unit": "inches"})
        self.assertEqual(item["marketplace_id"], "A2EUQ1WTGCTBG2")

    def test_dimensions_without_units_are_unset(self):
        product = _hw_detail()["product"]
        product["description"] = "<p>Dimension: 6200x1960x2800 Make:Hengwang for transport.</p>"
        filled = autofill_attributes(product, AppConfig(), "A2EUQ1WTGCTBG2",
                                     required=["item_dimensions"])
        self.assertNotIn("item_dimensions", filled)

    def test_item_dimensions_missing_goes_to_notes(self):
        product = _hw_detail()["product"]
        product["productAttr"] = "{}"
        product["description"] = "<p>No dimensions here.</p>"
        filled, notes = _autofill_attributes_with_notes(
            product, AppConfig(), "A2EUQ1WTGCTBG2",
            required=["item_dimensions"], template={})
        self.assertNotIn("item_dimensions", filled)
        self.assertTrue(any("item_dimensions" in n and "无法自动补齐" in n
                            for n in notes))

    def test_required_attributes_from_overrides_second_round(self):
        """第二轮：required_attributes 透传 → 只补这些（含确定性映射）。"""
        product = _hw_detail()["product"]
        ov = {"required_attributes": ["contains_liquid_contents",
                                      "power_source_type",
                                      "country_of_origin"]}
        filled = autofill_attributes(product, AppConfig(), "A2EUQ1WTGCTBG2",
                                     overrides=ov)
        self.assertEqual(set(filled), {"power_source_type", "country_of_origin"})
        self.assertNotIn("contains_liquid_contents", filled)
        self.assertEqual(filled["power_source_type"], "diesel_powered")
        self.assertEqual(filled["country_of_origin"], "CN")


class PublishVerifyTests(EnvIsolatedCase):
    """task-8：发布状态核实 + op=2 端到端（离线）。"""

    SAMPLE_ERR = ("发布失败:code:90220,“型号” 为必填项，但缺失。,属性:model_number;"
                  "code:90220,“制造商联系信息” 为必填项，但缺失。,"
                  "属性:manufacturer_contact_information;"
                  "code:90220,“零件编号” 为必填项，但缺失。,属性:part_number;"
                  "code:90220,“型号名称” 为必填项，但缺失。,属性:model_name;")

    def test_verify_publish_single_check_online(self):
        fake = FakeClient(publish_results={"state": "online", "asin": "B0ABC",
                                           "title": "T", "err_msg": "",
                                           "counts": {"online": 143}})
        out = verify_publish("220114595363182310", AppConfig(), client=fake,
                             wait_seconds=0)
        self.assertEqual(out["status"], "online")
        self.assertTrue(out["ok"])
        self.assertEqual(out["asin"], "B0ABC")
        self.assertEqual(out["checks"], 1)

    def test_verify_publish_failed_required_attrs(self):
        fake = FakeClient(publish_results={"state": "publishFail",
                                           "err_msg": self.SAMPLE_ERR,
                                           "asin": "", "title": "T"})
        out = verify_publish("220114595363182310", AppConfig(), client=fake,
                             wait_seconds=0)
        self.assertEqual(out["status"], "failed")
        self.assertFalse(out["ok"])
        self.assertEqual(out["required_attrs"],
                         ["model_number", "manufacturer_contact_information",
                          "part_number", "model_name"])
        self.assertIn("缺失必填属性", out["detail"])

    def test_verify_publish_reviewing(self):
        fake = FakeClient(publish_results={
            "state": "publishFail", "err_msg": "发布失败:code:100521,商品正在审核中",
            "asin": "", "title": "T"})
        out = verify_publish("220114595363182310", AppConfig(), client=fake,
                             wait_seconds=0)
        self.assertEqual(out["status"], "reviewing")
        self.assertTrue(out["ok"])
        self.assertIn("审核", out["detail"])

    def test_verify_publish_processing_single(self):
        fake = FakeClient(publish_results={"state": "publishing", "err_msg": "",
                                           "asin": "", "title": "T"})
        out = verify_publish("220114595363182310", AppConfig(), client=fake,
                             wait_seconds=0)
        self.assertEqual(out["status"], "processing")
        self.assertFalse(out["ok"])
        self.assertEqual(out["checks"], 1)

    def test_verify_publish_polls_until_online(self):
        fake = FakeClient(publish_results=[{"state": "publishing", "err_msg": ""},
                                           {"state": "online", "asin": "B0X",
                                            "err_msg": ""}])
        with patch("aeclone.services.amazon_pipeline.time.sleep") as sleep:
            out = verify_publish("220114595363182310", AppConfig(), client=fake,
                                 wait_seconds=5, interval=1)
        self.assertEqual(out["status"], "online")
        self.assertEqual(out["asin"], "B0X")
        self.assertEqual(len(fake.publish_queries), 2)
        sleep.assert_called()

    def test_run_op2_preview_autofills_without_writing(self):
        fake = FakeClient(detail=_hw_detail(), template={
            "power_source_type": [{"value": "diesel",
                                   "marketplace_id": "A2EUQ1WTGCTBG2"}]})
        res = run_amazon_product("220114595363182310", AppConfig(), client=fake,
                                 dry_run=True, op=OP_PUBLISH)
        self.assertTrue(res.ok, res.error)
        self.assertIn("必填属性补齐", res.log())
        self.assertNotIn("发布结果核实", res.log())
        self.assertEqual(fake.saved, [])
        self.assertEqual(fake.publish_queries, [])
        self.assertEqual(len(fake.template_queries), 1)

    def test_run_op2_real_save_online(self):
        os.environ["AURORA_SKIP_LICENSE"] = "1"
        cfg = AppConfig()
        cfg.set("amazon_publish_wait", 0)
        fake = FakeClient(detail=_hw_detail(), publish_results={
            "state": "online", "asin": "B0NEW", "title": "T", "err_msg": ""})
        res = run_amazon_product("220114595363182310", cfg, client=fake,
                                 dry_run=False, confirm_write=True,
                                 op=OP_PUBLISH)
        self.assertTrue(res.ok, res.error)
        self.assertTrue(res.saved)
        self.assertEqual(res.publish_state, "online")
        self.assertEqual(res.publish_asin, "B0NEW")
        self.assertEqual(res.required_attrs, [])
        self.assertEqual(len(fake.publish_queries), 1)
        self.assertIn("发布结果核实", res.log())
        self.assertEqual(res.completion_label, "保存并发布成功")
        payload = fake.saved[0][0]
        attr = parse_product_attr(payload["productAttr"])
        self.assertEqual(attr["model_number"][0]["value"], "HW20-28")
        self.assertEqual(attr["model_number"][0]["language_tag"], "en_CA")
        self.assertEqual(attr["part_number"][0]["value"], "WY-QHCA-LTM-1")
        self.assertNotIn("manufacturer_contact_information", attr)

    def test_run_op2_publish_failed_records_required_and_notes(self):
        os.environ["AURORA_SKIP_LICENSE"] = "1"
        cfg = AppConfig()
        cfg.set("amazon_publish_wait", 0)
        fake = FakeClient(detail=_hw_detail(), publish_results={
            "state": "publishFail", "asin": "", "title": "T",
            "err_msg": self.SAMPLE_ERR})
        res = run_amazon_product("220114595363182310", cfg, client=fake,
                                 dry_run=False, confirm_write=True,
                                 op=OP_PUBLISH)
        self.assertTrue(res.saved)
        self.assertFalse(res.ok)
        self.assertEqual(res.publish_state, "publishFail")
        self.assertIn("90220", res.publish_error)
        self.assertEqual(set(res.required_attrs),
                         {"model_number", "manufacturer_contact_information",
                          "part_number", "model_name"})
        self.assertTrue(any("缺失必填属性" in n for n in res.attribute_notes))
        self.assertEqual(res.completion_label, "已保存，发布失败")
        self.assertIn("发布结果核实", res.log())

    def test_run_op2_publish_reviewing_is_not_software_failure(self):
        os.environ["AURORA_SKIP_LICENSE"] = "1"
        cfg = AppConfig()
        cfg.set("amazon_publish_wait", 0)
        fake = FakeClient(detail=_hw_detail(), publish_results={
            "state": "publishFail", "asin": "", "title": "T",
            "err_msg": "发布失败:code:100521,商品正在审核中"})
        res = run_amazon_product("220114595363182310", cfg, client=fake,
                                 dry_run=False, confirm_write=True,
                                 op=OP_PUBLISH)
        self.assertTrue(res.ok, res.error)
        self.assertTrue(res.saved)
        self.assertEqual(res.publish_error, "")
        self.assertIn("审核", res.publish_detail)
        self.assertEqual(res.completion_label, "已保存，平台审核中")

    def test_run_op2_required_attributes_from_overrides(self):
        """调用方把上次失败的 required attrs 传回 overrides → 只补这些。"""
        fake = FakeClient(detail=_hw_detail(), template={
            "power_source_type": [{"value": "diesel",
                                   "marketplace_id": "A2EUQ1WTGCTBG2"}]})
        res = run_amazon_product(
            "220114595363182310", AppConfig(), client=fake, dry_run=True,
            op=OP_PUBLISH,
            overrides={"required_attributes": ["model_number", "country_of_origin"]})
        self.assertTrue(res.ok, res.error)
        self.assertIn("必填属性补齐", res.log())
        # 预演时 payload 不对外返回，但补齐 step 应只提到这两项
        step = next(s for s in res.steps if s.name == "必填属性补齐")
        self.assertIn("model_number", step.detail)
        self.assertIn("country_of_origin", step.detail)
        self.assertNotIn("part_number", step.detail)

    def test_run_op1_autofills_without_template_or_verify(self):
        """op=1 保存草稿也自动补齐必填属性；但不拉同类模板、不做发布核实。"""
        fake = FakeClient(detail=_hw_detail())
        res = run_amazon_product("220114595363182310", AppConfig(), client=fake,
                                 dry_run=True, op=OP_SAVE)
        self.assertTrue(res.ok, res.error)
        step = next((s for s in res.steps if s.name == "必填属性补齐"), None)
        self.assertIsNotNone(step)
        self.assertIn("model_number", step.detail)
        self.assertEqual(fake.template_queries, [])   # op=1 不拉同类在线商品模板
        self.assertEqual(fake.publish_queries, [])
        self.assertEqual(fake.saved, [])

    def test_run_op1_autofill_can_be_disabled_by_cfg(self):
        cfg = AppConfig()
        cfg.set("amazon_autofill_attrs", False)
        fake = FakeClient(detail=_hw_detail())
        res = run_amazon_product("220114595363182310", cfg, client=fake,
                                 dry_run=True, op=OP_SAVE)
        self.assertTrue(res.ok, res.error)
        step = next(s for s in res.steps if s.name == "必填属性补齐")
        self.assertIn("已关闭", step.detail)
        self.assertTrue(any("未自动补齐（开关已关闭）" in w for w in res.warnings))

    def test_required_attributes_from_schema_helper(self):
        fake = FakeClient(template={"_schema_required": ["model_number", "part_number"]})
        self.assertEqual(required_attributes_from_schema("159365", "OUTDOOR_LIVING",
                                                         "CA", client=fake),
                         ["model_number", "part_number"])
        self.assertEqual(required_attributes_from_schema("1", "T", "CA"), [])


class AiTests(EnvIsolatedCase):
    class FakeApi:
        def __init__(self, fail_bullets=False):
            self.calls = []
            self.fail_bullets = fail_bullets

        def chat(self, model, messages, max_tokens=2048):
            content = messages[0]["content"]
            self.calls.append(content)
            if "写一个可直接上架" in content:
                return "New AI Generated Backhoe Loader Heavy Duty Machine"
            if "恰好输出" in content:
                if self.fail_bullets:
                    raise RuntimeError("模型超时")
                return '["A durable frame","B diesel engine","C easy use",' \
                       '"D low fuel","E wide use"]'
            if "产品描述文案专家" in content:
                return "<p>Good product for construction.</p><ul><li>Diesel</li></ul>"
            if "generic_keyword" in content:
                return "backhoe loader diesel construction"
            return "fallback"

    def test_ai_generate_field_success(self):
        fake = self.FakeApi()
        with patch.object(PL, "_ai_client", return_value=fake):
            title, notes = ai_generate_field(make_product(), AppConfig(), "title",
                                             "A2EUQ1WTGCTBG2")
            self.assertIn("AI Generated", title)
            self.assertTrue(notes)
            bullets, _ = ai_generate_field(make_product(), AppConfig(), "bullets",
                                           "A2EUQ1WTGCTBG2", count=5)
            self.assertEqual(len(bullets), 5)
            desc, _ = ai_generate_field(make_product(), AppConfig(), "description",
                                        "A2EUQ1WTGCTBG2")
            self.assertIn("<p>", desc)
            terms, _ = ai_generate_field(make_product(), AppConfig(), "search_terms",
                                         "A2EUQ1WTGCTBG2")
            self.assertEqual(terms, "backhoe loader diesel construction")

    def test_ai_generate_field_unknown(self):
        value, notes = ai_generate_field(make_product(), AppConfig(), "unknown")
        self.assertIsNone(value)
        self.assertTrue(any("未知字段" in n for n in notes))

    def test_ai_generate_field_failure_returns_notes(self):
        class Boom:
            def chat(self, *a, **kw):
                raise RuntimeError("模型超时")
        with patch.object(PL, "_ai_client", return_value=Boom()):
            value, notes = ai_generate_field(make_product(), AppConfig(), "title",
                                             "A2EUQ1WTGCTBG2")
        self.assertEqual(value, "")
        self.assertTrue(any("失败" in n for n in notes))

    def test_ai_generate_all_partial_failure(self):
        fake = self.FakeApi(fail_bullets=True)
        with patch.object(PL, "_ai_client", return_value=fake):
            values, notes = ai_generate_all(make_product(), AppConfig(),
                                            "A2EUQ1WTGCTBG2")
        self.assertIn("AI Generated", values["title"])
        self.assertEqual(values["bullets"], [])
        self.assertIn("<p>", values["description"])
        self.assertTrue(values["search_terms"])
        self.assertTrue(any("五点描述" in n and "失败" in n for n in notes))

    def test_use_ai_in_pipeline(self):
        fake_client = FakeClient(detail=make_detail(title="", bulletPoint="",
                                                    description="", searchTerms=""))
        fake_api = self.FakeApi()
        with patch.object(PL, "_ai_client", return_value=fake_api):
            res = run_amazon_product("220114595363182310", AppConfig(),
                                     client=fake_client, dry_run=True, use_ai=True)
        self.assertTrue(res.ok, res.error)
        self.assertTrue(res.ai_used)
        self.assertIn("AI Generated", res.title)
        self.assertEqual(len(res.bullets), 5)
        self.assertIn("<p>", res.description)
        self.assertTrue(res.search_terms)
        self.assertEqual(fake_client.saved, [])

    def test_use_ai_does_not_override_existing_values(self):
        fake_client = FakeClient()
        fake_api = self.FakeApi()
        ov = {"title": "My Manual Title", "bullets": ["manual one", "manual two",
                                                     "manual three"]}
        with patch.object(PL, "_ai_client", return_value=fake_api):
            res = run_amazon_product("220114595363182310", AppConfig(), client=fake_client,
                                     dry_run=True, use_ai=True, overrides=ov)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.title, "My Manual Title")
        self.assertEqual(res.bullets, ["manual one", "manual two", "manual three"])
        # 标题/五点已有值 → 不应再调对应的 AI prompt
        self.assertFalse(any("写一个可直接上架" in c for c in fake_api.calls))
        self.assertFalse(any("恰好输出" in c for c in fake_api.calls))

    def test_ai_failure_does_not_block_preview(self):
        fake_client = FakeClient(detail=make_detail(bulletPoint="", searchTerms=""))

        class Boom:
            def chat(self, *a, **kw):
                raise RuntimeError("模型超时")
        with patch.object(PL, "_ai_client", return_value=Boom()):
            res = run_amazon_product("220114595363182310", AppConfig(),
                                     client=fake_client, dry_run=True, use_ai=True)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(fake_client.saved, [])
        self.assertTrue(any("AI：" in w for w in res.warnings))


class PublishGuardTests(EnvIsolatedCase):
    def test_save_to_amazon_forced_preview(self):
        os.environ["AURORA_NO_SAVE"] = "1"
        with self.assertRaises(RuntimeError):
            PUB.save_to_amazon("220114595363182310", AppConfig())

    def test_save_to_amazon_license_required(self):
        with self.assertRaises(RuntimeError):
            PUB.save_to_amazon("220114595363182310", AppConfig())

    def test_save_to_amazon_issues_rejected(self):
        os.environ["AURORA_SKIP_LICENSE"] = "1"
        fake = FakeClient(detail=make_detail(title=""))
        with patch.object(PUB, "make_client", return_value=fake):
            with self.assertRaises(ValueError):
                PUB.save_to_amazon("220114595363182310", AppConfig())
        self.assertEqual(fake.saved, [])

    def test_save_to_amazon_allow_invalid(self):
        os.environ["AURORA_SKIP_LICENSE"] = "1"
        fake = FakeClient(detail=make_detail(title=""))
        with patch.object(PUB, "make_client", return_value=fake):
            out = PUB.save_to_amazon("220114595363182310", AppConfig(),
                                     allow_invalid=True)
        self.assertEqual(len(fake.saved), 1)
        self.assertEqual(len(out["payload"]), 39)
        self.assertTrue(out["issues"])
        self.assertEqual(out["product_id"], "220114595363182310")

    def test_save_to_amazon_uses_passed_client(self):
        os.environ["AURORA_SKIP_LICENSE"] = "1"
        fake = FakeClient()
        with patch.object(PUB, "make_client",
                          side_effect=AssertionError("传了 client 不许再 make_client")):
            out = PUB.save_to_amazon("220114595363182310", AppConfig(),
                                     client=fake, op=OP_SAVE)
        self.assertEqual(out["result"]["code"], 0)
        self.assertEqual(len(fake.saved), 1)
        self.assertEqual(len(out["payload"]), 39)

    def test_save_to_amazon_forwards_client_and_keeps_guards(self):
        # forced_preview 护栏优先于一切（即使传了 client 也拒绝）
        os.environ["AURORA_NO_SAVE"] = "1"
        fake = FakeClient()
        with self.assertRaises(RuntimeError):
            PUB.save_to_amazon("220114595363182310", AppConfig(), client=fake)

    def test_save_to_amazon_success_dict(self):
        os.environ["AURORA_SKIP_LICENSE"] = "1"
        fake = FakeClient()
        with patch.object(PUB, "make_client", return_value=fake):
            out = PUB.save_to_amazon("220114595363182310", AppConfig(),
                                     op=OP_MOVE_PENDING)
        self.assertEqual(set(out), {"result", "issues", "warnings", "payload",
                                    "product_id", "op"})
        self.assertEqual(out["result"]["code"], 0)
        self.assertEqual(fake.saved[0][1], OP_MOVE_PENDING)
        self.assertEqual(len(out["payload"]), 39)

    def test_make_client_cookie_priority(self):
        c = PUB.make_client({"amazon_cookies": "dxm_i=1; JSESSIONID=2"})
        self.assertIsInstance(c, AmazonClient)
        with patch.object(PUB, "_browser_cookies", return_value={}):
            with self.assertRaises(RuntimeError):
                PUB.make_client({"amazon_cookies": "", "manual_cookies": {}})
        c2 = PUB.make_client({"amazon_cookies": "", "manual_cookies": {"dxm_i": "x"}})
        self.assertIsInstance(c2, AmazonClient)


if __name__ == "__main__":
    unittest.main(verbosity=2)
