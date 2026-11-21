"""Amazon 真实发布自动修复闭环 + 政策预检离线单测（无网络、绝不真实写）。

跑法：``.venv/bin/python tests/test_amazon_auto_publish.py``
覆盖：auto_publish 首轮成功 / B 类属性二轮补齐 / 100470 标题重复 / 描述违规清洗 /
CI 护栏拒绝真实写 / max_rounds 终止 / 价格库存阻断 / 属性不编造 /
policy_preflight 规则 / fix_* 清洗函数 / API 签名。
"""
import inspect
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.config import AppConfig
from aeclone.services import amazon_auto as AA
from aeclone.services.amazon_payload import build_amazon_payload

MP = "A2EUQ1WTGCTBG2"


def _attr(value: str) -> list[dict]:
    return [{"value": value, "language_tag": "en_CA", "marketplace_id": MP}]


def make_product(**over):
    product = {
        "id": "220114595363182310",
        "shopId": "9408005",
        "marketPlaceId": MP,
        "currency": "CAD",
        "title": "5 Ton Diesel Wheeled Backhoe Loader Heavy Duty Construction Machine",
        "categoryId": "13553005011",
        "categoryBrowsePath": "6299024011/6256963011/6256964011/13553005011",
        "productType": "OUTDOOR_LIVING",
        "isVariation": 0,
        "brandSettledType": "external_product_id",
        "fullCid": "5405448-",
        "sku": "WY-QHCA-LTM-1",
        "brand": "Generic",
        "manufacturer": "Generic",
        "productAttr": json.dumps({
            "model_number": _attr("HW20-28"),
            "model_name": _attr("HW20-28"),
            "part_number": _attr("WY-QHCA-LTM-1"),
            "manufacturer_contact_information": _attr("Generic, CN"),
            "item_dimensions": [{
                "length": {"value": "196", "unit": "inches"},
                "width": {"value": "63", "unit": "inches"},
                "height": {"value": "98", "unit": "inches"},
                "marketplace_id": MP}],
        }),
        "standardPrice": 8900.0,
        "saleSalePrice": 0.0,
        "standardPriceCurrency": "CAD",
        "quantity": 5,
        "conditionType": None,
        "conditionValue": "",
        "mainImage": "https://sc04.alicdn.com/kf/main.png",
        "extraImages": ["https://sc04.alicdn.com/kf/a.png"],
        "extraImagesStr": "https://sc04.alicdn.com/kf/a.png",
        "draftImgUrl": "https://sc04.alicdn.com/kf/a.png",
        "variationTheme": "",
        "skuTitle": 0,
        "description": "<p>Heavy duty backhoe loader for road construction.</p>"
                       "<ul><li>Engine: Diesel</li></ul>",
        "bulletPoint": ("Diesel engine for heavy duty construction work±"
                        "4x4 wheel drive for rough terrain±"
                        "High load moment for road projects±"
                        "Hydraulic system for precise operation±"
                        "Low fuel consumption and easy maintenance"),
        "gpsrManufacturerReference": "",
        "dsaResponsiblePartyAddress": "",
        "gpsrSafetyAttestation": 0,
        "switchFulfillmentTo": "MFN",
        "merchantShippingGroup": "legacy-template-id",
        "searchTerms": "backhoe loader diesel construction machine",
    }
    product.update(over)
    return product


class FakeClient:
    """离线替身：保存只记录不联网；发布状态按脚本返回。"""

    def __init__(self, product=None, publish_results=None, template=None,
                 schema=None):
        self.product = product if product is not None else make_product()
        self.publish_results = publish_results
        self.template = template if isinstance(template, dict) else {}
        self.schema = list(schema or [])
        self.saved = []
        self.publish_queries = 0
        self.template_queries = 0
        self.schema_queries = 0

    def product_detail(self, pid):
        return {"site": "CA", "product": dict(self.product)}

    def currency(self, shop_id, marketplace_id):
        return "CAD"

    def category_by_path(self, site_code, node_path):
        return {"id": 159365, "categoryId": "13553005011", "chineseName": "反铲"}

    def attribute_template(self, category_id, marketplace_id, shop_id="-1"):
        self.template_queries += 1
        return dict(self.template)

    def required_attributes_from_schema(self, category_id, product_type, site_code):
        self.schema_queries += 1
        return list(self.schema)

    def save_product(self, payload, op="1"):
        self.saved.append((dict(payload), str(op)))
        return {"code": 0, "msg": "Successful", "data": {"id": payload.get("id")}}

    def publish_state(self, product_id, shop_id="-1"):
        self.publish_queries += 1
        seq = self.publish_results
        if isinstance(seq, list):
            if not seq:
                return {"state": "online", "err_msg": "", "asin": "", "title": "",
                        "counts": {}}
            idx = min(self.publish_queries - 1, len(seq) - 1)
            return dict(seq[idx])
        if isinstance(seq, dict):
            return dict(seq)
        return {"state": "online", "err_msg": "", "asin": "B0DEFAULT",
                "title": "Backhoe", "counts": {}}


class EnvIsolatedCase(unittest.TestCase):
    ENV_KEYS = ("AURORA_NO_SAVE", "AURORA_TEST", "AURORA_SKIP_LICENSE")

    def setUp(self):
        self._saved_env = {k: os.environ.pop(k, None) for k in self.ENV_KEYS}

    def tearDown(self):
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class SignatureTests(unittest.TestCase):
    def test_auto_publish_signature(self):
        params = inspect.signature(AA.auto_publish).parameters
        self.assertEqual(list(params), [
            "product_id", "cfg", "overrides", "op", "max_rounds", "wait_seconds",
            "auto_fix_attrs", "auto_fix_title", "auto_fix_description",
            "auto_ai_fix", "client"])
        self.assertEqual(params["op"].default, "2")
        self.assertEqual(params["max_rounds"].default, 3)
        self.assertEqual(params["wait_seconds"].default, 90)
        self.assertIs(params["auto_fix_attrs"].default, True)
        self.assertIs(params["auto_fix_title"].default, True)
        self.assertIs(params["auto_fix_description"].default, True)
        self.assertIs(params["auto_ai_fix"].default, False)
        self.assertIsNone(params["client"].default)

    def test_result_is_serializable(self):
        res = AA.AutoPublishResult({"ok": True, "rounds": [], "attempts": 1})
        self.assertTrue(res.ok)
        self.assertEqual(json.loads(json.dumps(res.to_dict()))["attempts"], 1)

    def test_config_auto_defaults(self):
        cfg = AppConfig()
        self.assertEqual(cfg.amazon_auto_max_rounds, 3)
        self.assertEqual(cfg.amazon_auto_wait, 90)
        self.assertTrue(cfg.amazon_auto_fix_attrs)
        self.assertTrue(cfg.amazon_auto_fix_title)
        self.assertTrue(cfg.amazon_auto_fix_description)
        self.assertFalse(cfg.amazon_auto_ai_fix)


class AutoPublishFlowTests(EnvIsolatedCase):
    def setUp(self):
        super().setUp()
        os.environ["AURORA_SKIP_LICENSE"] = "1"

    def _online(self, asin="B0OK"):
        return {"state": "online", "err_msg": "", "asin": asin, "title": "T",
                "counts": {}}

    def _fail(self, err):
        return {"state": "publishFail", "err_msg": err, "asin": "", "title": "T",
                "counts": {}}

    def test_first_round_success(self):
        fake = FakeClient(publish_results=self._online())
        res = AA.auto_publish("220114595363182310", AppConfig(), client=fake,
                              wait_seconds=0)
        self.assertTrue(res.ok, res)
        self.assertTrue(res.saved)
        self.assertEqual(res.attempts, 1)
        self.assertEqual(res.final_state, "online")
        self.assertEqual(res.asin, "B0OK")
        self.assertEqual(len(fake.saved), 1)
        self.assertEqual(fake.saved[0][1], "2")
        self.assertEqual(len(res.rounds), 1)
        self.assertEqual(res.rounds[0]["status"], "online")
        self.assertEqual(res.blockers, [])

    def test_schema_required_attrs_autofilled_before_first_publish(self):
        fake = FakeClient(
            publish_results=self._online(),
            schema=["contains_liquid_contents", "power_source_type",
                    "supplier_declared_dg_hz_regulation"])
        res = AA.auto_publish("220114595363182310", AppConfig(), client=fake,
                              wait_seconds=0)
        self.assertTrue(res.ok, res)
        self.assertEqual(len(fake.saved), 1)
        attr = json.loads(fake.saved[0][0]["productAttr"])
        self.assertEqual(attr["contains_liquid_contents"][0]["value"], False)
        self.assertEqual(attr["power_source_type"][0]["value"], "diesel_powered")
        self.assertEqual(attr["supplier_declared_dg_hz_regulation"][0]["value"],
                         "not_applicable")

    def test_preflight_fixes_before_first_publish(self):
        product = make_product(
            title="Backhoe Loader Loader Loader Heavy Duty Construction Machine")
        fake = FakeClient(product=product, publish_results=self._online())
        res = AA.auto_publish("220114595363182310", AppConfig(), client=fake,
                              wait_seconds=0)
        self.assertTrue(res.ok, res)
        self.assertEqual(len(fake.saved), 1)
        saved_title = str(fake.saved[0][0]["title"])
        self.assertLessEqual(saved_title.lower().count("loader"), 2)
        self.assertTrue({"dedupe_title_words", "sanitize_title"}
                        & set(res.rounds[0]["fixes"]))

    def test_b_class_attrs_fixed_in_second_round(self):
        err = "发布失败:code:90220,属性:contains_liquid_contents;"
        fake = FakeClient(publish_results=[self._fail(err), self._online("B0B")])
        res = AA.auto_publish("220114595363182310", AppConfig(), client=fake,
                              wait_seconds=0)
        self.assertTrue(res.ok, res)
        self.assertEqual(res.attempts, 2)
        self.assertEqual(len(fake.saved), 2)
        self.assertIn("contains_liquid_contents", res.required_attrs)
        self.assertTrue(any("attrs" in fix for fix in res.fix_log))
        # 第二轮保存包 productAttr 里已带自动补的布尔属性
        attr = json.loads(fake.saved[1][0]["productAttr"])
        self.assertIn("contains_liquid_contents", attr)
        self.assertEqual(attr["contains_liquid_contents"][0]["value"], False)
        self.assertEqual(res.rounds[0]["status"], "failed")
        self.assertEqual(res.rounds[1]["status"], "online")

    def test_title_100470_deduped_and_republished(self):
        product = make_product(title="Backhoe Loader Heavy Duty Loader Machine HW20-28")
        fake = FakeClient(product=product, publish_results=[
            self._fail("标题重复:code:100470,重复词 [loader]"),
            self._online("B0T"),
        ])
        res = AA.auto_publish("220114595363182310", AppConfig(), client=fake,
                              wait_seconds=0)
        self.assertTrue(res.ok, res)
        self.assertEqual(res.attempts, 2)
        self.assertIn("fix_title_repetition", res.fix_log)
        second_title = str(fake.saved[1][0]["title"])
        self.assertEqual(second_title.lower().count("loader"), 1)

    def test_description_violation_cleaned(self):
        fake = FakeClient(publish_results=[
            self._fail("描述内容违规：含促销与联系方式"),
            self._online("B0D"),
        ])
        res = AA.auto_publish("220114595363182310", AppConfig(), client=fake,
                              wait_seconds=0)
        self.assertTrue(res.ok, res)
        self.assertEqual(res.attempts, 2)
        self.assertIn("sanitize_content", res.fix_log)

    def test_env_guard_rejects_real_write(self):
        os.environ["AURORA_TEST"] = "1"
        fake = FakeClient(publish_results=self._online())
        res = AA.auto_publish("220114595363182310", AppConfig(), client=fake)
        self.assertFalse(res.ok)
        self.assertFalse(res.saved)
        self.assertEqual(fake.saved, [])
        self.assertTrue(any("CI" in b or "AURORA_TEST" in b for b in res.blockers))

    def test_env_no_save_guard(self):
        os.environ["AURORA_NO_SAVE"] = "1"
        fake = FakeClient()
        res = AA.auto_publish("220114595363182310", AppConfig(), client=fake)
        self.assertFalse(res.ok)
        self.assertEqual(fake.saved, [])

    def test_max_rounds_stops(self):
        err = "发布失败:code:90220,属性:contains_liquid_contents;"
        fake = FakeClient(publish_results=self._fail(err))
        res = AA.auto_publish("220114595363182310", AppConfig(), client=fake,
                              max_rounds=2, wait_seconds=0)
        self.assertFalse(res.ok)
        self.assertEqual(len(res.rounds), 2)
        self.assertEqual(res.attempts, 2)
        self.assertEqual(len(fake.saved), 2)

    def test_unfixable_attr_blocks_without_extra_write(self):
        err = "发布失败:code:90220,属性:some_unknown_attribute;"
        fake = FakeClient(publish_results=[self._fail(err), self._online()])
        res = AA.auto_publish("220114595363182310", AppConfig(), client=fake,
                              wait_seconds=0)
        self.assertFalse(res.ok)
        self.assertEqual(len(fake.saved), 1)   # 第二轮预检阻断，不再写
        self.assertTrue(any("some_unknown_attribute" in b for b in res.blockers))

    def test_price_missing_blocks_before_write(self):
        fake = FakeClient(product=make_product(standardPrice=0.0))
        res = AA.auto_publish("220114595363182310", AppConfig(), client=fake,
                              wait_seconds=0)
        self.assertFalse(res.ok)
        self.assertEqual(fake.saved, [])
        self.assertTrue(any("价格" in b for b in res.blockers))

    def test_quantity_zero_blocks_before_write(self):
        fake = FakeClient(product=make_product(quantity=0))
        res = AA.auto_publish("220114595363182310", AppConfig(), client=fake,
                              wait_seconds=0)
        self.assertFalse(res.ok)
        self.assertEqual(fake.saved, [])
        self.assertTrue(any("库存" in b for b in res.blockers))

    def test_attrs_never_fabricated(self):
        """schema 要求但无法确定值的属性：阻断，不编造、不盲目发布。"""
        product = make_product(productAttr="{}")
        fake = FakeClient(product=product, template={},
                          schema=["some_unknown_attribute"])
        res = AA.auto_publish("220114595363182310", AppConfig(), client=fake,
                              wait_seconds=0)
        self.assertFalse(res.ok)
        self.assertEqual(fake.saved, [])
        self.assertTrue(any("some_unknown_attribute" in b for b in res.blockers))
        # 已尝试自动补的只有确定性映射字段，不会写入未知值
        self.assertFalse(any("some_unknown" in str(p.get("productAttr"))
                             for p, _op in fake.saved))


class PolicyPreflightTests(unittest.TestCase):
    def _payload(self, **over):
        payload = build_amazon_payload(make_product(), None, {}, currency="CAD")
        payload.update(over)
        return payload

    def _run(self, payload, cfg=None):
        return AA.policy_preflight(payload, make_product(), cfg or {})

    def test_valid_payload_no_blockers(self):
        pf = self._run(self._payload())
        self.assertEqual(pf["blockers"], [])
        self.assertEqual(pf["autofix_actions"], [])

    def test_title_rules(self):
        pf = self._run(self._payload(title="Best Backhoe 😀 <b>Loader</b> https://x.com "
                                           "Loader Loader Heavy Duty Construction Machine"))
        self.assertIn("sanitize_title", pf["autofix_actions"])
        self.assertIn("dedupe_title_words", pf["autofix_actions"])
        pf2 = self._run(self._payload(title="DIESEL BACKHOE LOADER HEAVY MACHINE"))
        self.assertIn("sanitize_title", pf2["autofix_actions"])
        pf3 = self._run(self._payload(title=""))
        self.assertIn("标题为空", pf3["blockers"])

    def test_bullets_rules(self):
        eleven = "±".join([f"bullet {i}" for i in range(11)])
        pf = self._run(self._payload(bulletPoint=eleven))
        self.assertIn("sanitize_bullets", pf["autofix_actions"])
        pf2 = self._run(self._payload(bulletPoint="a±<b>c</b>±d"))
        self.assertIn("sanitize_bullets", pf2["autofix_actions"])

    def test_description_rules(self):
        pf = self._run(self._payload(
            description="<p>best price</p><script>x</script>"
                        "<p>contact a@b.com https://x.com</p>"))
        self.assertIn("sanitize_description", pf["autofix_actions"])
        pf2 = self._run(self._payload(description=""))
        self.assertIn("产品描述为空", pf2["blockers"])

    def test_search_terms_rules(self):
        pf = self._run(self._payload(searchTerms="Best, Loader! with the sale"))
        self.assertIn("sanitize_search_terms", pf["autofix_actions"])

    def test_sku_rules(self):
        pf = self._run(self._payload(sku="bad sku!!"))
        self.assertIn("sanitize_sku", pf["autofix_actions"])
        pf2 = self._run(self._payload(sku=""))
        self.assertIn("SKU 为空", pf2["blockers"])

    def test_description_tag_whitelist_and_st_brand(self):
        pf = self._run(self._payload(description="<h3>Spec</h3><p>ok</p>"))
        self.assertIn("sanitize_description", pf["autofix_actions"])
        product = make_product(brand="Hengwang")
        payload = self._payload(searchTerms="hengwang backhoe loader")
        pf2 = AA.policy_preflight(payload, product, {})
        self.assertIn("sanitize_search_terms", pf2["autofix_actions"])
        self.assertEqual(
            AA.sanitize_search_terms("hengwang backhoe loader",
                                     banned=["hengwang"]),
            "backhoe loader")

    def test_price_quantity_blockers(self):
        pf = self._run(self._payload(standardPrice=None))
        self.assertTrue(any("价格" in b for b in pf["blockers"]))
        pf2 = self._run(self._payload(quantity=0))
        self.assertTrue(any("库存" in b for b in pf2["blockers"]))

    def test_base_required_fields(self):
        pf = self._run(self._payload(mainImage="ftp://x", categoryId="",
                                     brand="", manufacturer=""))
        joined = " ".join(pf["blockers"])
        self.assertIn("主图", joined)
        self.assertIn("类目", joined)
        self.assertIn("品牌", joined)
        self.assertIn("制造商", joined)

    def test_required_attributes_completeness(self):
        cfg = {"_preflight_required": ["model_number", "country_of_origin"]}
        pf = self._run(self._payload(productAttr="{}"), cfg)
        joined = " ".join(pf["blockers"])
        self.assertIn("model_number", joined)
        self.assertIn("country_of_origin", joined)
        # 已有属性值时不阻断
        cfg2 = {"_preflight_required": ["model_number"]}
        pf2 = self._run(self._payload(), cfg2)
        self.assertEqual(pf2["blockers"], [])

    def test_schema_required_strict_and_warning(self):
        cfg = {"_schema_required": ["some_extra_attr"],
               "_schema_required_strict": True}
        pf = self._run(self._payload(productAttr="{}"), cfg)
        self.assertTrue(any("some_extra_attr" in b for b in pf["blockers"]))
        cfg2 = {"_schema_required": ["some_extra_attr"],
                "_schema_required_strict": False}
        pf2 = self._run(self._payload(productAttr="{}"), cfg2)
        self.assertFalse(any("some_extra_attr" in b for b in pf2["blockers"]))
        self.assertTrue(any("some_extra_attr" in w for w in pf2["warnings"]))


class FixFunctionTests(unittest.TestCase):
    def test_dedupe_title_words(self):
        out = AA.dedupe_title_words("Loader loader LOADER heavy loader", 2)
        self.assertEqual(out.lower().count("loader"), 2)
        self.assertIn("heavy", out)

    def test_fix_title_repetition(self):
        out = AA.fix_title_repetition("Backhoe Loader Loader Loader Machine", 2)
        self.assertLessEqual(out.lower().count("loader"), 2)
        out2 = AA.fix_title_repetition("Backhoe Loader Loader Machine", 2,
                                       banned_word="loader")
        self.assertEqual(out2.lower().count("loader"), 1)

    def test_sanitize_title(self):
        out = AA.sanitize_title(
            "5 TON DIESEL LOADER best price 😀 https://x.com a@b.com")
        self.assertNotIn("best", out.lower())
        self.assertNotIn("http", out.lower())
        self.assertNotIn("@", out)
        self.assertNotIn("😀", out)
        self.assertLessEqual(len(out), 200)
        self.assertFalse(out.isupper())

    def test_sanitize_bullets(self):
        out = AA.sanitize_bullets("a±<b>best c</b>±d±e", max_items=3)
        self.assertLessEqual(len(out), 3)
        self.assertNotIn("<b>", " ".join(out))
        self.assertNotIn("best", " ".join(out).lower())
        out2 = AA.sanitize_bullets(["x" * 900], max_items=5)
        self.assertLessEqual(len(out2[0]), 700)

    def test_sanitize_description(self):
        out = AA.sanitize_description(
            "<p>best price</p><script>bad()</script><ul><li>one</li></ul>"
            "<p>contact a@b.com</p>")
        self.assertNotIn("best", out.lower())
        self.assertNotIn("<script", out.lower())
        self.assertNotIn("@", out)
        self.assertIn("<li>", out)
        self.assertLessEqual(len(out.encode("utf-8")), 20000)

    def test_sanitize_search_terms(self):
        out = AA.sanitize_search_terms("Best Price, BACKHOE loader for with the!")
        self.assertNotIn(",", out)
        self.assertNotIn("best", out)
        self.assertLessEqual(len(out.encode("utf-8")), 2000)
        self.assertTrue(out == out.lower())

    def test_sanitize_sku(self):
        self.assertEqual(AA.sanitize_sku("bad sku!!"), "bad-sku")
        self.assertEqual(AA.sanitize_sku("中文"), "")
        self.assertLessEqual(len(AA.sanitize_sku("A" * 60)), 40)

    def test_fixes_do_not_fabricate(self):
        out = AA.sanitize_description("<p>Heavy duty loader.</p>")
        self.assertNotIn("certified", out.lower())
        title = AA.sanitize_title("Best Backhoe Loader")
        self.assertNotIn("certified", title.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)
