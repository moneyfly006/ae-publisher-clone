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
    ai_generate_all, ai_generate_field, forced_preview, prepare_save,
    run_amazon_product,
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

    def __init__(self, detail=None):
        self.detail = detail if detail is not None else make_detail()
        self.saved = []
        self.category_queries = []

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
        ]
        cfg = AppConfig()
        for key in keys:
            self.assertTrue(hasattr(cfg, key), key)
        self.assertEqual(cfg.amazon_fulfillment, "MFN")
        self.assertEqual(cfg.amazon_bullet_count, 5)
        self.assertFalse(cfg.amazon_use_ai)
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
                                 dry_run=False)
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
                                 client=NoSaveClient(), dry_run=False)
        self.assertFalse(res.ok)
        self.assertFalse(res.saved)
        self.assertTrue(res.preview)
        self.assertIn("拒绝真实保存", res.error)

    def test_forced_preview_env_test(self):
        os.environ["AURORA_TEST"] = "1"
        fake = FakeClient()
        res = run_amazon_product("220114595363182310", AppConfig(), client=fake,
                                 dry_run=False)
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


class RealSavePathTests(EnvIsolatedCase):
    def _fake_save_env(self, fake):
        os.environ["AURORA_SKIP_LICENSE"] = "1"
        return patch.object(PUB, "make_client", return_value=fake)

    def test_license_required(self):
        fake = FakeClient()
        res = run_amazon_product("220114595363182310", AppConfig(), client=fake,
                                 dry_run=False)
        self.assertFalse(res.ok)
        self.assertIn("授权校验", res.error)
        self.assertFalse(res.saved)
        self.assertEqual(fake.saved, [])

    def test_real_save_success_goes_through_publish(self):
        fake = FakeClient()
        with self._fake_save_env(fake):
            res = run_amazon_product("220114595363182310", AppConfig(), client=fake,
                                     dry_run=False, op=OP_MOVE_PENDING)
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
                                     overrides=ai_over, op=OP_SAVE)
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
        # productAttr 原样回写（未切站点）
        self.assertEqual(sent["productAttr"], make_product()["productAttr"])

    def test_real_save_failure_message(self):
        class SaveFail(FakeClient):
            def save_product(self, payload, op="1"):
                return {"code": -1, "msg": "保存失败：类目不可用", "data": None}
        fake = SaveFail()
        with self._fake_save_env(fake):
            res = run_amazon_product("220114595363182310", AppConfig(), client=fake,
                                     dry_run=False)
        self.assertFalse(res.ok)
        self.assertFalse(res.saved)
        self.assertIn("类目不可用", res.error)


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
