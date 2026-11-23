from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.config import AppConfig
from aeclone.services import collect_ai
from aeclone.services.platform_presets import prompt_specs, TEXT_STYLES, IMAGE_STYLES, styled_prompt


class AiTests(unittest.TestCase):
    def test_style_choices_keep_platform_constraints_and_five_point_count(self):
        for platform in ("alibaba", "amazon", "tiktok", "shopee"):
            for key, label, default in prompt_specs(platform):
                for style in TEXT_STYLES:
                    prompt = styled_prompt(default, style)
                    self.assertTrue(prompt.startswith(default))
                    self.assertTrue(prompt.strip())
        bullets = next(default for key, _, default in prompt_specs("amazon") if "bullet" in key)
        self.assertIn("{count}", bullets)
        self.assertEqual(len(IMAGE_STYLES), 4)

    def test_missing_mode_never_rewrites_populated_fields(self):
        cfg = AppConfig(dict(amazon_collect_ai_fields='["title","description"]'))
        with patch("aeclone.services.amazon_pipeline.ai_generate_field", return_value=("<p>New description</p>", [])) as generate:
            result = collect_ai.generate_copy("amazon", dict(id="1", title="Original"), cfg, {})
        self.assertNotIn("title", result)
        self.assertIn("description", result)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(generate.call_args.args[2], "description")

    def test_rewrite_only_selected_fields_and_explicit_overrides_win(self):
        cfg = AppConfig(dict(amazon_collect_ai_fields='["title"]', amazon_collect_ai_mode="rewrite"))
        with patch("aeclone.services.amazon_pipeline.ai_generate_field", return_value=("New title", [])) as generate:
            result = collect_ai.generate_copy("amazon", dict(title="Old title", description="Original desc"), cfg, {})
            self.assertEqual(result, {"title": "New title"})
            result = collect_ai.generate_copy("amazon", dict(title="Old title"), cfg, {"title": "Explicit title"})
            self.assertEqual(result["title"], "Explicit title")
            self.assertEqual(generate.call_count, 1)

    def test_bullets_are_one_call_with_exactly_five_outputs(self):
        cfg = AppConfig(dict(amazon_collect_ai_fields='["bullets"]'))
        with patch("aeclone.services.amazon_pipeline.ai_generate_field", return_value=(["A", "B", "C", "D", "E"], [])) as generate:
            result = collect_ai.generate_copy("amazon", dict(title="facts"), cfg, {})
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(generate.call_args.kwargs["count"], 5)
        self.assertEqual(len(result["bullets"]), 5)
        with patch("aeclone.services.amazon_pipeline.ai_generate_field", return_value=(["A", "B"], [])):
            with self.assertRaises(ValueError):
                collect_ai.generate_copy("amazon", {}, cfg, {})

    def test_non_us_tiktok_never_generates_unsupported_bullets(self):
        cfg = AppConfig(dict(tiktok_collect_ai_fields='["title","key_features","search_terms"]', tiktok_site="GB"))
        with patch("aeclone.services.tiktok_pipeline.ai_generate_field", return_value=("New title", [])) as generate:
            result = collect_ai.generate_copy("tiktok", dict(site="GB"), cfg, {})
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(result, {"title": "New title"})

    def test_shopee_extended_description_stays_in_its_native_field(self):
        cfg = AppConfig(dict(shopee_collect_ai_fields='["description"]'))
        with patch("aeclone.services.shopee.generate_copy", return_value="Facts\nline two"):
            result = collect_ai.generate_copy("shopee", dict(descriptionType="extended"), cfg, {})
        self.assertNotIn("description", result)
        self.assertIn("<br>", result["descriptionInfoStr"])

    def test_shopee_existing_extended_description_is_not_missing(self):
        cfg = AppConfig(dict(shopee_collect_ai_fields='["description"]'))
        source = dict(descriptionType="extended", description="", descriptionInfoStr="<p>Existing facts</p>")
        with patch("aeclone.services.shopee.generate_copy", return_value="Unexpected rewrite") as generate:
            self.assertEqual(collect_ai.generate_copy("shopee", source, cfg, {}), {})
        generate.assert_not_called()

    def test_shopee_explicit_extended_description_is_never_overwritten(self):
        cfg = AppConfig(dict(shopee_collect_ai_fields='["description"]', shopee_collect_ai_mode="rewrite"))
        explicit = {"descriptionInfoStr": "<p>Explicit edited facts</p>"}
        with patch("aeclone.services.shopee.generate_copy", return_value="Unexpected rewrite") as generate:
            self.assertEqual(collect_ai.generate_copy("shopee", dict(descriptionType="extended"), cfg, explicit), explicit)
        generate.assert_not_called()

    def test_attribute_ai_queries_target_category_and_keeps_explicit_values(self):
        from unittest.mock import Mock
        definition = dict(nameEn="Material", values=[dict(name_en="Steel", id="new-value")])
        client = SimpleNamespace(required_attributes=Mock(return_value=[definition]))
        source = dict(categoryId="old", subject="Steel frame", attributes=[])
        api = SimpleNamespace(chat=Mock(return_value=json.dumps({"Material": dict(value="Steel", source="Steel frame")})))
        with patch("aeclone.api.ApiClient", return_value=api):
            overrides = dict(category_id="new")
            collect_ai.apply_attributes("alibaba", source, AppConfig(), client, overrides)
            client.required_attributes.assert_called_once_with("new")
            self.assertEqual(overrides["attributes"], {"Material": "Steel"})
            client.required_attributes.reset_mock()
            api.chat.reset_mock()
            explicit = dict(category_id="new", attributes={"Material": "Plastic"})
            collect_ai.apply_attributes("alibaba", source, AppConfig(), client, explicit)
            api.chat.assert_not_called()
            self.assertEqual(explicit["attributes"], {"Material": "Plastic"})
        self.assertEqual(source["categoryId"], "old")

    def test_empty_attribute_can_be_filled_but_populated_attribute_is_preserved(self):
        definitions = [dict(nameEn="Material", values=[dict(name_en="Steel", id="1")])]
        client = SimpleNamespace(required_attributes=lambda _: definitions)
        source = dict(categoryId="1", subject="Steel frame", attributes=[dict(attribute_name="Material", value_name="")])
        api = SimpleNamespace(chat=lambda *a, **k: json.dumps({"Material": dict(value="Steel", source="Steel frame")}))
        with patch("aeclone.api.ApiClient", return_value=api):
            result = collect_ai.suggest_attributes("alibaba", source, AppConfig(), client)
        self.assertIn("Material", result)

    def test_empty_shopee_attribute_is_filled_without_duplicate_native_id(self):
        definitions = [dict(attributeId=7, attributeName="Material", attributeValueList=[dict(valueId=9, valueName="Steel")])]
        client = SimpleNamespace(attributes=lambda *a: definitions)
        source = dict(categoryId="1", itemName="Steel frame", attributes=[dict(attribute_id=7, attribute_value_list=[])])
        api = SimpleNamespace(chat=lambda *a, **k: json.dumps({"Material": dict(value="Steel", source="Steel frame")}))
        with patch("aeclone.api.ApiClient", return_value=api):
            overrides = {}
            collect_ai.apply_attributes("shopee", source, AppConfig(), client, overrides)
        self.assertEqual(len(overrides["attributes"]), 1)
        self.assertEqual(overrides["attributes"][0]["attribute_id"], 7)
        self.assertEqual(overrides["attributes"][0]["attribute_value_list"][0]["value_id"], 9)
        self.assertEqual(source["attributes"][0]["attribute_value_list"], [])

    def test_shopee_target_category_does_not_inherit_previous_category_ids(self):
        from unittest.mock import Mock
        definitions = [dict(attributeId=70, attributeName="Material", attributeValueList=[dict(valueId=90, valueName="Steel")])]
        client = SimpleNamespace(attributes=Mock(return_value=definitions))
        source = dict(categoryId="old", itemName="Steel frame", shopId="shop",
                      attributes=[dict(attribute_id=7, attribute_value_list=[dict(value_id=9)])])
        api = SimpleNamespace(chat=lambda *a, **k: json.dumps({"Material": dict(value="Steel", source="Steel frame")}))
        with patch("aeclone.api.ApiClient", return_value=api):
            overrides = dict(categoryId="new")
            collect_ai.apply_attributes("shopee", source, AppConfig(), client, overrides)
        client.attributes.assert_called_once_with("new", "shop")
        self.assertEqual([row["attribute_id"] for row in overrides["attributes"]], [70])
        self.assertEqual(source["attributes"][0]["attribute_id"], 7)

    def test_tiktok_attribute_ai_uses_target_category_and_respects_native_overrides(self):
        from unittest.mock import Mock
        definitions = [dict(attributeId="70", attributeName="Material", values=[dict(valueId="90", valueName="Steel")])]
        client = SimpleNamespace(category_attributes=Mock(return_value=definitions))
        source = dict(categoryId="old", site="GB", productName="Steel frame", productAttributes=[])
        explicit = dict(category_id="new", product_attributes=[dict(attributeId="70", values=[dict(valueId="91", valueName="Plastic")])])
        api = SimpleNamespace(chat=Mock(return_value=json.dumps({"Material": dict(value="Steel", source="Steel frame")})))
        with patch("aeclone.api.ApiClient", return_value=api):
            collect_ai.apply_attributes("tiktok", source, AppConfig(), client, explicit)
            client.category_attributes.assert_called_once_with("GB", "new")
            api.chat.assert_not_called()
            self.assertEqual(explicit["product_attributes"][0]["values"][0]["valueId"], "91")
            overrides = dict(category_id="new")
            collect_ai.apply_attributes("tiktok", source, AppConfig(), client, overrides)
            self.assertEqual(overrides["product_attributes"][0]["values"][0]["valueId"], "90")

    def test_amazon_attribute_ai_does_not_query_schema_with_previous_product_type(self):
        from unittest.mock import Mock
        client = SimpleNamespace(required_attributes_from_schema=Mock(return_value=["material"]))
        source = dict(categoryId="old", productType="OLD_TYPE", marketPlaceId="ATVPDKIKX0DER", title="Steel frame")
        api = SimpleNamespace(chat=Mock(return_value=json.dumps({"material": dict(value="Steel", source="Steel frame")})))
        with patch("aeclone.api.ApiClient", return_value=api):
            overrides = dict(category_id="new", attributes={"material": "Plastic"})
            collect_ai.apply_attributes("amazon", source, AppConfig(), client, overrides)
            client.required_attributes_from_schema.assert_called_once_with("new", "", "US")
            api.chat.assert_not_called()
            overrides = dict(category_id="new")
            collect_ai.apply_attributes("amazon", source, AppConfig(), client, overrides)
            self.assertEqual(overrides["attributes"], {"material": "Steel"})

    def test_missing_skus_are_unique_and_existing_child_identity_is_retained(self):
        cfg = AppConfig()
        fake = SimpleNamespace(chat=lambda *a, **k: "ToolCode")
        values = []
        with patch("aeclone.api.ApiClient", return_value=fake):
            for pid in ("10000000000000001", "10000000000000002"):
                ov = {}
                collect_ai.generate_sku("shopee", dict(id=pid, name="Tool"), cfg, ov)
                values.append(ov["itemSku"])
            ov = {}
            collect_ai.generate_sku("tiktok", dict(id="1", variationList=[dict(sellerSku="EXISTING")]), cfg, ov)
            self.assertEqual(ov, {})
        self.assertNotEqual(*values)
        self.assertTrue(all(len(value) <= 40 for value in values))

    def test_attribute_ai_rejects_invented_values_and_accepts_exact_evidence(self):
        definitions = [dict(nameEn="Material", values=[dict(name_en="Steel", id="1"), dict(name_en="Plastic", id="2")])]
        client = SimpleNamespace(required_attributes=lambda _: definitions)
        p = dict(categoryId="1", subject="Steel frame", attributes=[])
        for response, expected in (({"Material": dict(value="Steel", source="Steel frame")}, True),
                                   ({"Material": dict(value="Plastic", source="Steel frame")}, False),
                                   ({"Material": dict(value="Steel", source="Invented source")}, False)):
            api = SimpleNamespace(chat=lambda *a, **k: json.dumps(response))
            with patch("aeclone.api.ApiClient", return_value=api):
                suggestions = collect_ai.suggest_attributes("alibaba", p, AppConfig(), client)
            self.assertEqual(bool(suggestions), expected)

    def test_existing_and_sensitive_attributes_do_not_trigger_generation(self):
        definitions = [dict(nameEn="Weight", values=[]), dict(nameEn="Material", values=[])]
        client = SimpleNamespace(required_attributes=lambda _: definitions)
        p = dict(categoryId="1", subject="Steel frame", attributes=[dict(attribute_name="Material", value_name="Steel")])
        with patch("aeclone.api.ApiClient") as api:
            self.assertEqual(collect_ai.suggest_attributes("alibaba", p, AppConfig(), client), {})
        api.assert_not_called()

    def test_alibaba_fills_only_missing_existing_variant_codes(self):
        product = dict(id="10000000000000001", subject="Steel storage box", productSku=json.dumps({
            "special_skus": [dict(id="red", sku_code="RED-KEEP", price=10), dict(id="blue", sku_code="", price=12)]}))
        api = SimpleNamespace(chat=lambda *a, **k: "StorageBox")
        ov = {}
        with patch("aeclone.api.ApiClient", return_value=api):
            collect_ai.generate_sku("alibaba", product, AppConfig(), ov)
        rows = ov["product_sku"]["special_skus"]
        self.assertEqual(rows[0]["sku_code"], "RED-KEEP")
        self.assertTrue(rows[1]["sku_code"])
        self.assertEqual(rows[1]["id"], "blue")
        self.assertEqual(rows[1]["price"], 12)
        self.assertEqual(json.loads(product["productSku"])["special_skus"][1]["sku_code"], "")

    def test_shopee_attribute_native_ids_are_taken_from_candidates(self):
        client = SimpleNamespace(attributes=lambda *a: [dict(attributeId=7, attributeName="Material", attributeValueList=[dict(valueId=9, valueName="Steel")])])
        product = dict(id="1", categoryId="1", itemName="Steel frame", attributes=[])
        api = SimpleNamespace(chat=lambda *a, **k: json.dumps({"Material": dict(value="Steel", source="Steel frame")}))
        with patch("aeclone.api.ApiClient", return_value=api):
            ov = {}
            collect_ai.apply_attributes("shopee", product, AppConfig(), client, ov)
        self.assertEqual(ov["attributes"][0]["attribute_id"], 7)
        self.assertEqual(ov["attributes"][0]["attribute_value_list"][0]["value_id"], 9)

    def test_disabled_ai_does_not_read_or_call_anything(self):
        self.assertEqual(collect_ai.prepare("amazon", "1", AppConfig(), object(), {"quantity": 0}), {"quantity": 0})

    def test_concurrent_autosaves_are_atomic_and_unchanged_config_skips_write(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            cfg = AppConfig(dict(amazon_collect_keyword="pool"))
            with patch.object(AppConfig, "path", return_value=path):
                with ThreadPoolExecutor(max_workers=8) as pool:
                    list(pool.map(lambda _: cfg.save(), range(40)))
                self.assertEqual(json.loads(path.read_text())["amazon_collect_keyword"], "pool")
                previous = path.stat().st_mtime_ns
                cfg.save()
                self.assertEqual(path.stat().st_mtime_ns, previous)
                cfg.set("amazon_collect_keyword", "changed")
                cfg.save()
                self.assertEqual(json.loads(path.read_text())["amazon_collect_keyword"], "changed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
