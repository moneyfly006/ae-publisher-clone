from pathlib import Path
from types import SimpleNamespace
from copy import deepcopy
import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.config import AppConfig
from aeclone.services.listing_failures import classify, review_only
from aeclone.services.listing_recovery import amazon_plan, run_failed_recovery
from aeclone.services.amazon_payload import build_amazon_payload
from aeclone.services.amazon_pipeline import _classify_publish
from aeclone.services.platform_presets import prompt_specs
from aeclone.services.collect_automation import isolated_config, process_one
from aeclone.services.collect_preflight import check


def product(**values):
    p = dict(id="1", title="Backhoe Loader for Farms", bulletPoint="Steel frame±4x4 drive±2000 kg capacity",
             description="<p>5200*1600*2560 mm; rated 75 kW, 2000 kg operating weight.</p>",
             productAttr='{}', marketPlaceId="A2EUQ1WTGCTBG2", categoryId="123", shopId="s",
             standardPrice=50, quantity=8, mainImage="https://example.invalid/product.jpg",
             dxmState="offline", dxmOfflineState="publishFail", errMsg="code:100470,标题重复 [backhoe]")
    p.update(values)
    return p


class Client:
    def __init__(self, p):
        self.product = p
        self.reads = 0

    def list_products(self, state, *args):
        return dict(items=[dict(id="1", err_msg="code:100521,正在审查")], total=1, total_page=1)

    def product_detail(self, pid):
        self.reads += 1
        return {"product": deepcopy(self.product)}


class RecoveryTests(unittest.TestCase):
    def test_mixed_review_and_actionable_errors_are_failures(self):
        for code in ("100470", "90220", "8560", "13013", "123456"):
            error = "code:100521,正在审查;code:" + code + ",发布失败"
            self.assertFalse(review_only("amazon", error))
            self.assertEqual(_classify_publish(dict(state="publishFail", err_msg=error))["status"], "failed")

    def test_review_only_is_not_retried(self):
        cfg = AppConfig(dict(amazon_collect_op="2"))
        with patch("aeclone.services.collect_automation.process_one") as save:
            result = run_failed_recovery(cfg, "amazon", client=Client(product(errMsg="code:100521,正在审查")), dry_run=False)
        save.assert_not_called()
        self.assertEqual(result[0]["status"], "reviewing")

    def test_authoritative_detail_error_replaces_stale_review_list(self):
        result = run_failed_recovery(AppConfig(), "amazon", client=Client(product()), dry_run=True)
        self.assertNotEqual(result[0]["status"], "reviewing")

    def test_schema_title_tracks_editor_title_and_other_data_survives(self):
        p = product(productAttr=json.dumps({"item_name": [{"value": "Backhoe Backhoe Backhoe"}], "size": [{"value": "75kW"}]}))
        original = deepcopy(p)
        out = build_amazon_payload(p, isolated_config(AppConfig(), "amazon"), {"title": "New farm equipment"})
        attrs = json.loads(out["productAttr"])
        self.assertEqual(attrs["item_name"][0]["value"], out["title"])
        self.assertEqual(attrs["size"], json.loads(p["productAttr"])["size"])
        self.assertEqual(p, original)
        self.assertEqual(out["quantity"], 8)
        self.assertEqual(out["standardPrice"], 50)

    def test_bullets_sync_as_separate_schema_entries(self):
        out = build_amazon_payload(product(), AppConfig(), {"bullets": ["One", "Two", "Three", "Four", "Five"]})
        values = json.loads(out["productAttr"])["bullet_point"]
        self.assertEqual([v["value"] for v in values], ["One", "Two", "Three", "Four", "Five"])

    def test_repair_preserves_actual_dimensions_and_weight(self):
        p = product(title="Hot Sale Backhoe Backhoe Backhoe Loader")
        plan = amazon_plan(p, AppConfig(), error=p["errMsg"])
        self.assertNotIn("Sale", plan["overrides"]["title"])
        self.assertLessEqual(plan["overrides"]["title"].lower().split().count("backhoe"), 2)
        desc = plan["overrides"].get("description", p["description"])
        self.assertIn("5200*1600*2560", desc)
        self.assertIn("2000 kg", desc)

    def test_missing_variation_theme_does_not_invent_a_theme(self):
        p = product(errMsg="code:90220,属性:bullet_point;code:90220,属性:variation_theme;code:90220,属性:product_description")
        plan = amazon_plan(p, AppConfig(), error=p["errMsg"])
        self.assertIn("bullet_point", plan["overrides"]["attributes"])
        self.assertIn("product_description", plan["overrides"]["attributes"])
        self.assertNotIn("variation_theme", plan["overrides"]["attributes"])
        self.assertTrue(any("variation_theme" in b for b in plan["blockers"]))

    def test_recovery_accepts_explicit_attributes_and_backfills_from_source(self):
        error = "code:90220,属性:contains_liquid_contents;code:90220,属性:country_of_origin"
        p = product(countryOfOrigin="CA", errMsg=error)
        plan = amazon_plan(p, AppConfig(), {"attributes": {"contains_liquid_contents": False}}, error)
        self.assertEqual(plan["blockers"], [])
        self.assertEqual(plan["overrides"]["attributes"], {"contains_liquid_contents": False, "country_of_origin": "CA"})
        self.assertTrue(plan["actions"])

    def test_recovery_does_not_resend_when_publish_readback_is_unknown(self):
        src = Client(product(title="Hot Sale Backhoe Backhoe Backhoe Loader"))
        def publish(*args, before_submit, **kwargs):
            before_submit()
            src.product["errMsg"] = "code:90220,属性:country_of_origin"
            src.product["countryOfOrigin"] = "CA"
            return SimpleNamespace(ok=False, saved=True, error="未能确认发布状态",
                                   publish_state="unknown", publish_status="unknown")
        with patch("aeclone.services.collect_automation.process_one", side_effect=publish) as save:
            result = run_failed_recovery(AppConfig({"amazon_collect_op": "2"}), "amazon", client=src, dry_run=False)
        save.assert_called_once()
        self.assertEqual(result[0]["outcome"], "uncertain")

    def test_identifier_and_catalog_never_blindly_republish(self):
        for error in ("code:8560,属性:externally_assigned_product_identifier", "code:13013,无法加入型录"):
            with patch("aeclone.services.collect_automation.process_one") as save:
                result = run_failed_recovery(AppConfig(dict(amazon_collect_op="2")), "amazon",
                    client=Client(product(title="Hot Sale Backhoe", errMsg=error)), dry_run=False)
            save.assert_not_called()
            self.assertFalse(result[0]["ok"])

    def test_draft_repair_can_preserve_unresolved_items_without_publishing(self):
        client = Client(product(title="Hot Sale Backhoe", errMsg="code:8560,缺识别码"))
        with patch("aeclone.services.collect_automation.process_one", return_value=SimpleNamespace(ok=True, saved=True, error="")) as save:
            result = run_failed_recovery(AppConfig(), "amazon", client=client, dry_run=False)
        self.assertEqual(save.call_args.kwargs["op"], "1")
        self.assertTrue(result[0]["saved"])
        self.assertFalse(result[0]["ok"])
        self.assertEqual(result[0]["status"], "partial")

    def test_identical_failed_repair_is_not_resent(self):
        client = Client(product(title="Backhoe Backhoe Backhoe Farm Loader"))
        with patch("aeclone.services.collect_automation.process_one", return_value=SimpleNamespace(ok=False, saved=True, error="failed")) as save:
            run_failed_recovery(AppConfig(dict(amazon_collect_op="2", amazon_collect_max_attempts=3)), "amazon", client=client, dry_run=False)
        self.assertEqual(save.call_count, 1)

    def test_stop_and_readonly_analysis_never_save(self):
        for dry_run, stop in ((True, None), (False, lambda: True)):
            with patch("aeclone.services.collect_automation.process_one") as save:
                run_failed_recovery(AppConfig(), "amazon", client=Client(product()), dry_run=dry_run, stop=stop)
            save.assert_not_called()

    def test_alibaba_scope_is_explicit_and_not_recategorized(self):
        failure = classify("alibaba", "PUB_BIZCHECK_CAT_PUB_RESTRICT")[0]
        self.assertEqual(failure.kind, "category_scope")
        self.assertFalse(failure.repairable)

    def test_effective_prompts_complete_even_when_editors_are_blank(self):
        for platform in ("alibaba", "amazon", "tiktok", "shopee"):
            specs = [x for section in ("text", "stock", "category") for x in prompt_specs(platform, section)]
            data = {key: "  " for key, _, _ in specs}
            data[platform + "_collect_image_prompt"] = " "
            cfg = isolated_config(AppConfig(data), platform)
            for key, _, default in specs:
                self.assertEqual(getattr(cfg, key), default)
            self.assertTrue(getattr(cfg, platform + "_collect_image_prompt").strip())
        self.assertEqual(sum("bullet" in key for key, _, _ in prompt_specs("amazon")), 1)
        self.assertIn("alibaba_keyword_prompt", [x[0] for x in prompt_specs("alibaba")])

    def test_amazon_price_migration_reaches_native_price(self):
        from aeclone.services.collect_automation import settings
        cfg = AppConfig(dict(amazon_collect_overrides='{"price":"75"}'))
        ov = settings(cfg, "amazon")
        self.assertEqual(build_amazon_payload(product(), cfg, ov)["standardPrice"], 75)

    def test_preflight_checks_only_active_platform(self):
        cfg = AppConfig(dict(amazon_collect_ai=True, api_key="", alibaba_collect_ai=False,
                             price_multiplier="-5", write_price_to_product=True))
        self.assertTrue(any("API Key" in issue.title for issue in check(cfg, "amazon")))
        self.assertEqual(check(cfg, "alibaba"), [])

    def test_image_range_is_not_partially_accepted(self):
        for raw in ("1,garbage", "9-1", "1-9999999", "", "7"):
            cfg = AppConfig(dict(alibaba_collect_images=True, alibaba_collect_image_rules="",
                                 alibaba_collect_image_indices=raw, api_key="fake"))
            self.assertTrue(any("序号" in item.title for item in check(cfg, "alibaba")))

    def test_disabling_auto_repair_reaches_pipeline_unchanged(self):
        cfg = AppConfig(dict(amazon_collect_auto_repair=False))
        with patch("aeclone.services.amazon_pipeline.run_amazon_product") as run:
            process_one("amazon", "1", cfg, object(), {"title": "Hot Sale Tool"}, dry_run=True, op="1", use_ai=False)
        self.assertEqual(run.call_args.args[2]["title"], "Hot Sale Tool")

    def test_specifications_are_not_phone_numbers_and_real_phones_are_removed(self):
        from aeclone.services.amazon_payload import has_contact_number, remove_contact_numbers
        from aeclone.services.amazon_auto import sanitize_description
        text = "Capacity 2000-10000 pcs/day; depth 2000-2600 mm; dimensions 5500*1720*2650 5800*1850*2750"
        self.assertFalse(has_contact_number(text))
        self.assertIn("2000-10000", sanitize_description(text))
        self.assertIn("2000-2600", remove_contact_numbers(text))
        self.assertIn("5500*1720*2650", remove_contact_numbers(text))
        self.assertTrue(has_contact_number("Call +86 138 1234 5678"))
        self.assertNotIn("138", remove_contact_numbers("Call +86 138 1234 5678"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
