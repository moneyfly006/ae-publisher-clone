"""Alibaba 保存载荷（alibabaSave.txt zip）离线单测，不联网。"""
import io
import json
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.integrations.alibaba import AlibabaClient, SAVE_NAME


class AlibabaPayloadTests(unittest.TestCase):
    def test_build_save_dict_keeps_strings(self):
        product = {
            "id": "123", "shopId": "9", "categoryId": "202107201",
            "fullCid": "18,100005657,202107201", "subject": "Test Machine",
            "keywords": "machine, industrial", "attributes": [{"attribute_id": "1"}],
            "detailImage": [{"url": "https://x/1.jpg"}],
            "bulkDiscountPrices": [{"start_quantity": 1, "price": 10.5}],
            "wholesaleTrade": {"min_order_quantity": 1, "weight": "20"},
            "productSku": {"attributes": [], "exclude_skus": [], "special_skus": []},
            "mainImage": "https://x/a.jpg,https://x/b.jpg",
            "priceType": "ladder_price",
        }
        d = AlibabaClient.build_save_dict(product)
        self.assertEqual(d["subject"], "Test Machine")
        self.assertEqual(d["mainImage"], "https://x/a.jpg,https://x/b.jpg")
        self.assertEqual(d["draftImgUrl"], "https://x/a.jpg|https://x/b.jpg")
        for key in ("attributes", "detailImage", "bulkDiscountPrices",
                    "wholesaleTrade", "productSku"):
            self.assertIsInstance(d[key], str)
            json.loads(d[key])
        self.assertEqual(d["op"], "1")

    def test_zip_contains_alibaba_save_txt(self):
        d = AlibabaClient.build_save_dict({"id": "1", "subject": "s"})
        raw = AlibabaClient.zip_payload(d)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            self.assertIn(SAVE_NAME, z.namelist())
            data = json.loads(z.read(SAVE_NAME).decode("utf-8"))
        self.assertEqual(data["subject"], "s")




class AlibabaRequiredTests(unittest.TestCase):
    def test_autofill_uses_preset_then_other(self):
        from aeclone.services.alibaba_payload import autofill_required_attributes
        attrs, filled, unresolved = autofill_required_attributes(
            {"attributes": [{"attribute_name": "Place of Origin", "value_name": "China"}]},
            [{"attrNameId": "1", "nameEn": "Place of Origin", "required": 1, "values": []},
             {"attrNameId": "2", "nameEn": "Color", "required": 1,
              "values": [{"id": "1", "name_en": "White"}, {"id": "2", "name_en": "Other"}]},
             {"attrNameId": "3", "nameEn": "Material", "required": 1,
              "values": [{"id": "5", "name_en": "Other"}]},
             {"attrNameId": "4", "nameEn": "Feature", "required": 0, "values": []}],
            {"alibaba_attr_presets": '{"Color": "White"}'})
        names = {a["attribute_name"]: a.get("value_name") for a in attrs}
        self.assertEqual(names["Color"], "White")
        self.assertEqual(names["Material"], "Other")
        self.assertIn("Color=White", filled)
        self.assertIn("Material=Other", filled)
        self.assertEqual(unresolved, [])

    def test_invalid_preset_value_is_blocked_not_written(self):
        """预设值不在类目候选里时必须阻断，不能把无效值写进保存包。"""
        from aeclone.services.alibaba_payload import autofill_required_attributes
        attrs, filled, unresolved = autofill_required_attributes(
            {"attributes": []},
            [{"attrNameId": "2", "nameEn": "Color", "required": 1,
              "values": [{"id": "1", "name_en": "White"}, {"id": "2", "name_en": "Other"}]}],
            {"alibaba_attr_presets": '{"Color": "Blue"}'})
        self.assertEqual(attrs, [])
        self.assertEqual(filled, [])
        self.assertTrue(any("Color" in u for u in unresolved), unresolved)

    def test_autofill_does_not_fabricate_first_candidate_value(self):
        """无预设且无 Other/China 时，不能把候选列表第一个值当真实规格写入。"""
        from aeclone.services.alibaba_payload import autofill_required_attributes
        attrs, filled, unresolved = autofill_required_attributes(
            {"attributes": []},
            [{"attrNameId": "2", "nameEn": "Voltage", "required": 1,
              "values": [{"id": "48", "name_en": "48 V"}, {"id": "60", "name_en": "60 V"}]}],
            {"alibaba_attr_presets": ""})
        self.assertEqual(attrs, [])
        self.assertEqual(filled, [])
        self.assertEqual(unresolved, ["Voltage"])

    def test_validate_reports_missing_required_and_bad_tiers(self):
        from aeclone.services.alibaba_payload import validate_alibaba_save
        save = {"subject": "A", "keywords": "", "mainImage": "",
                "bulkDiscountPrices": '[{"start_quantity": 5, "price": 1},{"start_quantity": 1, "price": 2}]',
                "wholesaleTrade": '{"min_order_quantity": 0, "package_size": ""}',
                "attributes": "[]", "detailImage": "[]", "textDesc": ""}
        issues = validate_alibaba_save({}, save, None,
                                       [{"nameEn": "Color"}],
                                       [{"fieldId": "textDesc"}, {"fieldId": "detailImage"}])
        text = "；".join(issues)
        self.assertIn("关键词", text)
        self.assertIn("主图为空", text)
        self.assertIn("阶梯价数量区间", text)
        self.assertIn("MOQ", text)
        self.assertIn("必填属性缺失：Color", text)
        self.assertIn("textDesc", text)


def _pool_product(**over):
    product = {
        "id": "220114598434958580", "shopId": "9466975",
        "categoryId": "202107201", "fullCid": "18,100005657,100005663,202107201",
        "subject": "Customized Fiberglass Swimming Pool Container",
        "keywords": "fiberglass pool, container pool, above ground pool",
        "attributes": [{"attribute_id": "1", "attribute_name": "Place of Origin",
                        "value_id": "100000458", "value_name": "China"}],
        "mainImage": "https://img.example.com/a.jpg",
        "detailImage": [{"url": "https://img.example.com/d1.jpg"}],
        "textDesc": "<p>detail</p>",
        "bulkDiscountPrices": [{"start_quantity": 1, "price": 4001.4}],
        "wholesaleTrade": {"min_order_quantity": 1, "package_size": "300X200X150",
                           "weight": "300", "sale_type": "normal", "unit_type": "Piece"},
        "priceType": "ladder_price",
        "productSku": {"attributes": [], "exclude_skus": [], "special_skus": []},
    }
    product.update(over)
    return product


def _cfg(**over):
    from aeclone.config import AppConfig
    data = {"alibaba_category_id": "", "alibaba_full_cid": "", "alibaba_keywords": "",
            "alibaba_price_type": "ladder_price", "alibaba_attr_presets": "",
            "alibaba_min_order_quantity": 1, "alibaba_unit_type": "Piece"}
    data.update(over)
    return AppConfig(data)


class AlibabaCategoryOverrideTests(unittest.TestCase):
    def test_cfg_category_overrides_product_and_old_value_gone(self):
        from aeclone.services.alibaba_payload import build_alibaba_save
        cfg = _cfg(alibaba_category_id="100010756",
                   alibaba_full_cid="18,100005657,100010658,100010756")
        save = build_alibaba_save(_pool_product(), cfg)
        self.assertEqual(save["categoryId"], "100010756")
        self.assertEqual(save["fullCid"], "18,100005657,100010658,100010756")
        self.assertNotIn("202107201", save["categoryId"])
        self.assertNotIn("202107201", save["fullCid"])

    def test_overrides_provide_category(self):
        from aeclone.services.alibaba_payload import build_alibaba_save
        save = build_alibaba_save(_pool_product(), _cfg(),
                                  {"categoryId": "127726092",
                                   "fullCid": "201943703,1326,100010658,127726092"})
        self.assertEqual(save["categoryId"], "127726092")
        self.assertEqual(save["fullCid"], "201943703,1326,100010658,127726092")

    def test_only_category_derives_full_cid_last_segment(self):
        from aeclone.services.alibaba_payload import build_alibaba_save, resolve_category
        product = _pool_product()
        cfg = _cfg(alibaba_category_id="100010756")   # 未配置 fullCid
        cid, full = resolve_category(product, cfg)
        self.assertEqual(cid, "100010756")
        self.assertTrue(full.endswith(",100010756"), full)
        self.assertNotIn("202107201", full)
        save = build_alibaba_save(product, cfg)
        self.assertEqual(save["categoryId"], "100010756")
        self.assertEqual(save["fullCid"], full)

    def test_product_category_kept_when_nothing_configured(self):
        from aeclone.services.alibaba_payload import build_alibaba_save
        save = build_alibaba_save(_pool_product(), _cfg())
        self.assertEqual(save["categoryId"], "202107201")
        self.assertEqual(save["fullCid"], "18,100005657,100005663,202107201")


class AlibabaFobCleanupTests(unittest.TestCase):
    def test_fob_clears_tiers_and_tariffs_hs(self):
        from aeclone.services.alibaba_payload import build_alibaba_save
        product = _pool_product(
            wholesaleTrade={"min_order_quantity": 1, "package_size": "300X200X150",
                            "weight": "300",
                            "tariffsHsCode": [{"toCountry": "US", "hsCode": ""}]})
        cfg = _cfg(alibaba_price_type="fob_price", alibaba_fob_currency="USD",
                   alibaba_fob_min_price="100", alibaba_fob_max_price="200",
                   alibaba_hs_code="8413709990")
        save = build_alibaba_save(product, cfg)
        self.assertEqual(save["priceType"], "fob_price")
        self.assertEqual(json.loads(save["bulkDiscountPrices"]), [])
        trade = json.loads(save["wholesaleTrade"])
        self.assertNotIn("tariffsHsCode", trade)
        self.assertIn("min_order_quantity", trade)
        fob = json.loads(save["sourcingTrade"])
        self.assertEqual(fob["fob_currency"], "USD")
        self.assertEqual(fob["fob_min_price"], 100.0)
        self.assertEqual(fob["fob_max_price"], 200.0)

    def test_ladder_keeps_tiers_and_hs(self):
        from aeclone.services.alibaba_payload import build_alibaba_save
        product = _pool_product(
            wholesaleTrade={"min_order_quantity": 1, "package_size": "300X200X150",
                            "weight": "300",
                            "tariffsHsCode": [{"toCountry": "US", "hsCode": ""}]})
        cfg = _cfg(alibaba_hs_code="8413709990")
        save = build_alibaba_save(product, cfg)
        self.assertEqual(save["priceType"], "ladder_price")
        self.assertTrue(json.loads(save["bulkDiscountPrices"]))
        trade = json.loads(save["wholesaleTrade"])
        self.assertEqual(trade["tariffsHsCode"][0]["hsCode"], "8413709990")


class AlibabaValidateCategoryTests(unittest.TestCase):
    def _base_save(self, **over):
        save = {"subject": "Pool", "keywords": "pool, spa", "mainImage": "https://x/a.jpg",
                "categoryId": "202107201", "fullCid": "18,100005657,100005663,202107201",
                "priceType": "ladder_price",
                "bulkDiscountPrices": '[{"start_quantity":1,"price":10}]',
                "wholesaleTrade": '{"min_order_quantity":1,"package_size":"1X1X1","weight":"2"}',
                "attributes": "[]", "detailImage": "[]", "textDesc": ""}
        save.update(over)
        return save

    def test_missing_or_none_category_is_blocked(self):
        from aeclone.services.alibaba_payload import validate_alibaba_save
        for cid in (None, "", "None", "null", "undefined"):
            with self.subTest(cid=cid):
                issues = validate_alibaba_save({}, self._base_save(categoryId=cid), None, [], [])
                self.assertTrue(any("类目" in i for i in issues), issues)

    def test_non_numeric_category_is_blocked(self):
        from aeclone.services.alibaba_payload import validate_alibaba_save
        issues = validate_alibaba_save({}, self._base_save(categoryId="abc"), None, [], [])
        self.assertTrue(any("不是有效数字" in i for i in issues), issues)

    def test_full_cid_mismatch_is_blocked(self):
        from aeclone.services.alibaba_payload import validate_alibaba_save
        issues = validate_alibaba_save({}, self._base_save(
            categoryId="100010756", fullCid="18,100005657,100005663,202107201"), None, [], [])
        self.assertTrue(any("fullCid 末段" in i for i in issues), issues)

    def test_clean_category_and_full_cid_pass(self):
        from aeclone.services.alibaba_payload import validate_alibaba_save
        issues = validate_alibaba_save({}, self._base_save(), None, [], [])
        self.assertFalse(any("类目" in i or "fullCid" in i for i in issues), issues)


class AlibabaValidateSkuTests(unittest.TestCase):
    def _save(self, product_sku, price_type="sku_price"):
        return {"subject": "Pool", "keywords": "pool, spa", "mainImage": "https://x/a.jpg",
                "categoryId": "202107201", "fullCid": "18,100005657,100005663,202107201",
                "priceType": price_type, "productSku": product_sku,
                "bulkDiscountPrices": "[]",
                "wholesaleTrade": '{"min_order_quantity":1,"package_size":"1X1X1","weight":"2"}',
                "attributes": "[]", "detailImage": "[]", "textDesc": ""}

    def test_sku_price_without_product_sku_is_blocked(self):
        from aeclone.services.alibaba_payload import validate_alibaba_save
        for raw in (None, "", "{}", '{"attributes": [], "special_skus": []}'):
            with self.subTest(raw=raw):
                issues = validate_alibaba_save({}, self._save(raw), None, [], [])
                self.assertTrue(any("sku_price" in i for i in issues), issues)

    def test_sku_price_with_valid_skus_passes(self):
        from aeclone.services.alibaba_payload import validate_alibaba_save
        raw = json.dumps({
            "attributes": [{"attribute_id": "-1", "attribute_name": "Color",
                            "value_id": "-1", "value_name": "Blue"}],
            "special_skus": [{"exclude": False, "price": 12.5, "sku_code": "SKU-1",
                              "attributes": [{"attribute_name": "Color",
                                              "value_name": "Blue"}]}],
        })
        issues = validate_alibaba_save({}, self._save(raw), None, [], [])
        self.assertFalse(any("sku_price" in i for i in issues), issues)

    def test_sku_price_all_excluded_is_blocked_with_reason(self):
        from aeclone.services.alibaba_payload import validate_alibaba_save
        raw = json.dumps({"attributes": [{"attribute_name": "Color", "value_name": "Blue"}],
                          "special_skus": [{"exclude": True, "price": 12.5}]})
        issues = validate_alibaba_save({}, self._save(raw), None, [], [])
        self.assertTrue(any("无有效 SKU" in i for i in issues), issues)

    def test_ladder_price_ignores_product_sku(self):
        from aeclone.services.alibaba_payload import validate_alibaba_save
        save = self._save(None, price_type="ladder_price")
        save["bulkDiscountPrices"] = '[{"start_quantity":1,"price":10}]'
        issues = validate_alibaba_save({}, save, None, [], [])
        self.assertFalse(any("Sku" in i or "SKU" in i for i in issues), issues)


class AlibabaKeywordsPreserveTests(unittest.TestCase):
    def test_product_keywords_kept_when_no_config(self):
        from aeclone.services.alibaba_payload import build_alibaba_save
        save = build_alibaba_save(_pool_product(), _cfg())
        self.assertEqual(save["keywords"],
                         "fiberglass pool, container pool, above ground pool")

    def test_cfg_keywords_win_over_product(self):
        from aeclone.services.alibaba_payload import build_alibaba_save
        cfg = _cfg(alibaba_keywords="custom pool, fiberglass pool")
        save = build_alibaba_save(_pool_product(), cfg)
        self.assertEqual(save["keywords"], "custom pool, fiberglass pool")

    def test_override_keywords_win_over_cfg(self):
        from aeclone.services.alibaba_payload import build_alibaba_save
        cfg = _cfg(alibaba_keywords="custom pool, fiberglass pool")
        save = build_alibaba_save(_pool_product(), cfg,
                                  {"keywords": ["override pool", "spa pool"]})
        self.assertEqual(save["keywords"], "override pool, spa pool")

    def test_keywords_generated_from_title_only_when_absent(self):
        from aeclone.services.alibaba_payload import build_alibaba_save
        product = _pool_product(keywords="")
        save = build_alibaba_save(product, _cfg())
        # 标题 "Customized Fiberglass Swimming Pool Container" → 英文单词
        self.assertTrue(save["keywords"])
        self.assertNotIn("fiberglass pool", save["keywords"])


class AlibabaDeliverPeriodTests(unittest.TestCase):
    def test_deliver_periods_written_from_config(self):
        from aeclone.services.alibaba_payload import build_alibaba_save
        cfg = _cfg(alibaba_process_period="15", alibaba_deliver_quantity="3")
        save = build_alibaba_save(_pool_product(), cfg)
        trade = json.loads(save["wholesaleTrade"])
        self.assertEqual(trade["deliver_periods"], [{"quantity": 3, "process_period": 15}])

    def test_deliver_periods_defaults_to_lead_time(self):
        from aeclone.services.alibaba_payload import build_alibaba_save
        cfg = _cfg(alibaba_lead_time=20, alibaba_process_period="")
        save = build_alibaba_save(_pool_product(), cfg)
        trade = json.loads(save["wholesaleTrade"])
        self.assertEqual(trade["deliver_periods"][0]["process_period"], 20)
        self.assertGreaterEqual(trade["deliver_periods"][0]["quantity"], 1)


class AlibabaTextDescTests(unittest.TestCase):
    def test_build_strips_html_from_text_desc(self):
        from aeclone.services.alibaba_payload import build_alibaba_save
        cfg = _cfg(alibaba_category_id="202107201")
        save = build_alibaba_save(_pool_product(), cfg, {
            "text_desc": "<h3>Title</h3><p>Hello <b>world</b></p><script>x</script>"})
        desc = save["textDesc"]
        self.assertNotIn("<", desc, "平台 textDesc 不接受 HTML")
        self.assertIn("Title", desc)
        self.assertIn("Hello", desc)
        self.assertNotIn("x</", desc)

    def test_validate_rejects_over_2000_chars(self):
        from aeclone.services.alibaba_payload import validate_alibaba_save
        product = _pool_product()
        save = {"categoryId": "202107201", "subject": "x" * 60,
                "textDesc": "y" * 2500, "mainImage": "https://x/a.jpg",
                "priceType": "ladder_price", "bulkDiscountPrices": [],
                "wholesaleTrade": "{}", "attributes": "[]"}
        issues = validate_alibaba_save(product, save, _cfg(), [], [])
        self.assertTrue(any("2000" in i for i in issues), issues)


class AlibabaCompanyFieldsTests(unittest.TestCase):
    def test_company_desc_and_faq_written_to_payload(self):
        from aeclone.services.alibaba_payload import build_alibaba_save
        cfg = _cfg(alibaba_category_id="202107201",
                   alibaba_company_desc="We are a factory. " * 20,
                   alibaba_company_faq="Q: MOQ? A: 1 set.")
        save = build_alibaba_save(_pool_product(), cfg)
        self.assertIn("companyDesc", save)
        self.assertIn("We are a factory", save["companyDesc"])
        self.assertIn("companyFaqDesc", save)
        self.assertIn("MOQ", save["companyFaqDesc"])


class AlibabaPriceTypePreserveTests(unittest.TestCase):
    """更新已有商品时不能把定价模型静默改掉（sku_price → 空阶梯价）。"""

    SKU_PRODUCT = None

    def _sku_product(self):
        return _pool_product(
            priceType="sku_price",
            bulkDiscountPrices=[],
            productSku={
                "attributes": [{"attribute_name": "Color", "attribute_id": "210190015"}],
                "exclude_skus": [],
                "special_skus": [{"price": 12.5, "sku_code": "RED-1",
                                  "attributes": [{"attribute_name": "Color",
                                                  "value_name": "Red"}]}],
            })

    def test_sku_price_not_silently_rewritten_to_ladder(self):
        from aeclone.services.alibaba_payload import build_alibaba_save, validate_alibaba_save
        save = build_alibaba_save(self._sku_product(), _cfg())
        self.assertEqual(save["priceType"], "sku_price")
        self.assertEqual(json.loads(save["bulkDiscountPrices"]), [])
        self.assertTrue(save["productSku"])
        issues = validate_alibaba_save(self._sku_product(), save, _cfg())
        self.assertEqual(issues, [])

    def test_explicit_price_type_override_still_wins(self):
        from aeclone.services.alibaba_payload import build_alibaba_save
        save = build_alibaba_save(self._sku_product(), _cfg(),
                                  {"price_type": "ladder_price",
                                   "price_tiers": [{"start_quantity": 1, "price": 99}]})
        self.assertEqual(save["priceType"], "ladder_price")
        self.assertEqual(json.loads(save["bulkDiscountPrices"])[0]["price"], 99)

    def test_ladder_product_keeps_model_when_no_tiers_configured(self):
        from aeclone.services.alibaba_payload import build_alibaba_save
        save = build_alibaba_save(_pool_product(), _cfg())
        self.assertEqual(save["priceType"], "ladder_price")
        # 没有配置阶梯价档位时保留商品原有阶梯价，不清空
        self.assertEqual(json.loads(save["bulkDiscountPrices"])[0]["price"], 4001.4)

    def test_fob_product_not_rewritten_by_default_ladder_config(self):
        from aeclone.services.alibaba_payload import build_alibaba_save
        product = _pool_product(priceType="fob_price", bulkDiscountPrices=[])
        save = build_alibaba_save(product, _cfg())
        self.assertEqual(save["priceType"], "fob_price")
        self.assertTrue(json.loads(save["sourcingTrade"] or "{}"))

    def test_explicit_marker_allows_config_to_change_model(self):
        from aeclone.services.alibaba_payload import build_alibaba_save
        cfg = _cfg(alibaba_price_type_explicit="1",
                   alibaba_price_tiers='[{"start_quantity":1,"price":88}]')
        save = build_alibaba_save(self._sku_product(), cfg)
        self.assertEqual(save["priceType"], "ladder_price")
        self.assertEqual(json.loads(save["bulkDiscountPrices"])[0]["price"], 88)


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    sys.exit(0 if result.result.wasSuccessful() else 1)

