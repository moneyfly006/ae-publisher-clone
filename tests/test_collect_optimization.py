"""Behavioral acceptance for durable runs, budgets, profiles and visual editors."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import flet as ft
from ui_sandbox import sandbox, build_app, walk
from aeclone.config import AppConfig
from aeclone.services import collect_store as store
from aeclone.services.collect_automation import run_collect, settings, validate_overrides, isolated_config
from aeclone.services.collect_budget import Budget, estimate
from aeclone.services.collect_profiles import sync
from aeclone.services.collect_runtime import ReadClient
from aeclone.services.collect_preflight import check
from aeclone.ui.collect_results import csv_text, repair_page


class OptimizationTests(unittest.TestCase):
    def setUp(self):
        self.context = sandbox()
        self.cfg = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.cfg.set("amazon_collect_interval", 0)
        self.cfg.set("amazon_shop_id", "store-one")
        self.app = build_app()

    def client(self, **product):
        return SimpleNamespace(product_detail=Mock(side_effect=lambda pid: {"product": dict(id=pid, dxmState="draft", **product)}))

    def test_task_journal_preserves_scope_results_overrides_and_no_credentials(self):
        self.cfg.set("api_key", "secret-key-test")
        self.cfg.set("amazon_cookies", "secret-cookie-test")
        self.cfg.set("amazon_title_prompt", "Original prompt snapshot")
        rows = [{"id": "1", "title": "One"}]
        with patch("aeclone.services.collect_automation.process_one", return_value=SimpleNamespace(ok=True, saved=True, error="")):
            result = run_collect(self.cfg, "amazon", rows=rows, client=self.client(), dry_run=False)
        run = store.get(result[0]["run_id"])
        self.assertEqual(run["items"][0]["phase"], "done")
        self.assertEqual(run["items"][0]["result"]["outcome"], "draft_saved")
        self.assertEqual(run["status"], "complete")
        self.assertNotIn("secret-key-test", json.dumps(run))
        self.assertNotIn("secret-cookie-test", json.dumps(run))
        self.assertEqual(run["config"]["amazon_title_prompt"], "Original prompt snapshot")
        self.assertEqual(store.latest(self.cfg, "amazon")["id"], run["id"])

    def test_interrupted_submission_is_read_back_and_never_replayed(self):
        def interrupt_during_submit(*args, before_submit, **kwargs):
            before_submit()
            raise KeyboardInterrupt

        with patch("aeclone.services.collect_automation.process_one", side_effect=interrupt_during_submit):
            with self.assertRaises(KeyboardInterrupt):
                run_collect(self.cfg, "amazon", rows=[{"id": "1"}, {"id": "2"}], client=self.client(), dry_run=False)
        run = store.latest(self.cfg, "amazon")
        self.assertEqual(run["items"][0]["phase"], "submitting")
        self.assertEqual(run["items"][1]["phase"], "queued")
        self.cfg.set("amazon_collect_op", "2")  # Resume uses the original draft destination.
        with patch("aeclone.services.collect_automation.process_one", return_value=SimpleNamespace(ok=True, saved=True, error="")) as save:
            results = run_collect(self.cfg, "amazon", resume_id=run["id"], client=self.client(), dry_run=False)
        self.assertEqual(save.call_count, 1)
        self.assertEqual(save.call_args.args[1], "2")
        self.assertEqual(save.call_args.kwargs["op"], "1")
        self.assertEqual(results[0]["outcome"], "uncertain")
        self.assertEqual(results[1]["outcome"], "draft_saved")

    def test_resume_rejects_different_shop_and_skips_completed_items(self):
        run_id = store.create(self.cfg, "amazon", [{"id": "1"}])
        store.item_update(run_id, "1", "done", result=dict(id="1", ok=True, saved=True, outcome="draft_saved"))
        with patch("aeclone.services.collect_automation.process_one") as save:
            result = run_collect(self.cfg, "amazon", resume_id=run_id, client=self.client())
            save.assert_not_called()
        self.assertEqual(result[0]["outcome"], "draft_saved")
        self.cfg.set("amazon_shop_id", "other")
        with self.assertRaisesRegex(ValueError, "不匹配"):
            run_collect(self.cfg, "amazon", resume_id=run_id, client=self.client())

    def test_same_shop_scope_cannot_run_in_two_windows_and_lock_is_released(self):
        with store.exclusive_scope(self.cfg, "amazon"):
            with self.assertRaisesRegex(ValueError, "已有任务"):
                run_collect(self.cfg, "amazon", rows=[], client=self.client())
        self.assertEqual(run_collect(self.cfg, "amazon", rows=[], client=self.client()), [])

    def test_new_checks_do_not_remove_older_unfinished_task_from_history(self):
        pending = store.create(self.cfg, "amazon", [{"id": "1"}])
        newer = store.create(self.cfg, "amazon", [], dry=True)
        self.assertEqual({r["id"] for r in store.history(self.cfg, "amazon")}, {pending, newer})

    def test_recovery_journal_does_not_replay_interrupted_write(self):
        from aeclone.services.listing_recovery import run_failed_recovery
        run_id = store.create(self.cfg, "amazon", [{"id": "1"}], kind="recovery")
        store.item_update(run_id, "1", "submitting", prepared=dict(title="Edited"))
        with patch("aeclone.services.collect_automation.process_one") as save:
            result = run_failed_recovery(self.cfg, "amazon", resume_id=run_id, client=self.client())
            save.assert_not_called()
        self.assertEqual(result[0]["outcome"], "uncertain")

    def test_recovery_resume_preserves_review_notice_when_detail_omits_error(self):
        from aeclone.services.listing_recovery import run_failed_recovery
        self.cfg.set("amazon_collect_op", "2")
        rows = [{"id": "1", "err_msg": "code:100521,正在审查"}]
        client = SimpleNamespace(product_detail=Mock(return_value={"product": {
            "id": "1", "dxmState": "offline", "dxmOfflineState": "publishFail", "title": "Hot Sale Backhoe"}}))
        with patch("aeclone.services.collect_automation.process_one") as save:
            run_id = store.create(self.cfg, "amazon", rows, kind="recovery")
            result, = run_failed_recovery(self.cfg, "amazon", resume_id=run_id, client=client)
            self.assertEqual(result["outcome"], "reviewing")
            legacy_id = store.create(self.cfg, "amazon", [{"id": "1"}], kind="recovery")
            result, = run_failed_recovery(self.cfg, "amazon", resume_id=legacy_id, client=client)
            self.assertIn("未返回发布失败原因", result["error"])
            save.assert_not_called()

    def test_budget_call_limit_is_persistent_across_sessions(self):
        self.cfg.set("amazon_collect_ai_max_calls", 1)
        run = store.create(self.cfg, "amazon", [{"id": "1"}])
        first = Budget(self.cfg, "amazon", run)
        self.assertEqual(first.call("text:title", {"id": "1"}, {}, lambda: "Generated"), "Generated")
        restarted = Budget(self.cfg, "amazon", run)
        self.assertEqual(restarted.call("text:title", {"id": "1"}, {}, Mock(side_effect=AssertionError())), "Generated")
        with self.assertRaises(store.BudgetExceeded):
            restarted.call("text:title", {"id": "2"}, {}, lambda: "Other")
        self.assertEqual(store.get(run)["calls"], 1)

    def test_budget_money_reserves_before_call_and_failure_is_not_free(self):
        self.cfg.set("amazon_collect_ai_budget", 0.3)
        self.cfg.set("amazon_collect_text_call_price", 0.2)
        run = store.create(self.cfg, "amazon", [{"id": "1"}])
        budget = Budget(self.cfg, "amazon", run)
        with self.assertRaisesRegex(RuntimeError, "network"):
            budget.call("text:title", {"id": "1"}, {}, Mock(side_effect=RuntimeError("network")))
        paid = Mock(return_value="must not execute")
        with self.assertRaises(store.BudgetExceeded):
            budget.call("text:title", {"id": "1"}, {}, paid)
        paid.assert_not_called()
        self.assertAlmostEqual(store.get(run)["cost"], 0.2)

    def test_budgeted_image_request_has_no_hidden_paid_retry(self):
        from aeclone.services import ImageService
        self.cfg.set("image_retry_times", 2)
        isolated = isolated_config(self.cfg, "amazon")
        api = SimpleNamespace(images_edit=Mock(side_effect=RuntimeError("HTTP 503")))
        service = ImageService(api, isolated)
        with self.assertRaises(RuntimeError):
            service._img_edit("keep facts", [], "qwen-image-2.1-turbo")
        api.images_edit.assert_called_once()
        self.assertEqual(self.cfg.image_retry_times, 2)

    def test_cache_invalidates_on_facts_rules_or_shop_but_not_status(self):
        run = store.create(self.cfg, "amazon", [{"id": "1"}])
        budget = Budget(self.cfg, "amazon", run)
        operation = Mock(return_value="Generated")
        product = dict(id="1", title="Real facts", dxmState="draft")
        budget.call("text:title", product, {}, operation)
        budget.call("text:title", dict(product, dxmState="publishFail", errMsg="new error"), {}, operation)
        self.assertEqual(operation.call_count, 1)
        budget.call("text:title", dict(product, title="Changed facts"), {}, operation)
        self.cfg.set("amazon_title_prompt", "Changed rule")
        budget.call("text:title", product, {}, operation)
        self.cfg.set("amazon_shop_id", "store-two")
        budget.call("text:title", product, {}, operation)
        self.assertEqual(operation.call_count, 4)

    def test_budget_caches_field_results_when_saving_fails(self):
        self.cfg.set("amazon_collect_ai", True)
        self.cfg.set("amazon_collect_ai_fields", '["title"]')
        self.cfg.set("api_key", "test")
        with patch("aeclone.services.amazon_pipeline.ai_generate_field", return_value=("Generated title", [])) as generate, \
             patch("aeclone.services.collect_automation.process_one", return_value=SimpleNamespace(ok=False, saved=False, error="Missing facts")):
            for _ in range(2):
                run_collect(self.cfg, "amazon", rows=[{"id": "1"}], client=self.client(), dry_run=False)
        self.assertEqual(generate.call_count, 1)

    def test_budget_stops_remaining_items_without_submission(self):
        self.cfg.set("amazon_collect_ai", True)
        self.cfg.set("amazon_collect_ai_fields", '["title"]')
        self.cfg.set("amazon_collect_ai_max_calls", 1)
        self.cfg.set("api_key", "test")
        with patch("aeclone.services.amazon_pipeline.ai_generate_field", return_value=("Generated title", [])), \
             patch("aeclone.services.collect_automation.process_one", return_value=SimpleNamespace(ok=True, saved=True, error="")) as save:
            results = run_collect(self.cfg, "amazon", rows=[{"id": "1"}, {"id": "2"}, {"id": "3"}], client=self.client(), dry_run=False)
        self.assertEqual(len(results), 2)
        self.assertEqual(save.call_count, 1)
        self.assertIn("预算", results[-1]["halt_reason"])
        self.assertEqual(store.latest(self.cfg, "amazon")["items"][-1]["phase"], "queued")

    def test_approved_resume_budget_increase_keeps_spent_calls_and_continues_pending_item(self):
        self.cfg.set("amazon_collect_ai", True)
        self.cfg.set("amazon_collect_ai_fields", '["title"]')
        self.cfg.set("amazon_collect_ai_max_calls", 1)
        self.cfg.set("api_key", "test")
        with patch("aeclone.services.amazon_pipeline.ai_generate_field", return_value=("Generated title", [])), \
             patch("aeclone.services.collect_automation.process_one", return_value=SimpleNamespace(ok=True, saved=True, error="")) as save:
            first = run_collect(self.cfg, "amazon", rows=[{"id": "1"}, {"id": "2"}, {"id": "3"}], client=self.client(), dry_run=False)
            run = store.latest(self.cfg, "amazon")
            self.assertEqual(run["items"][1]["phase"], "queued")
            object.__setattr__(self.cfg, "_resume_limits", {"amazon_collect_ai_max_calls": 3})
            save.reset_mock()
            final = run_collect(self.cfg, "amazon", resume_id=run["id"], client=self.client())
        self.assertEqual([call.args[1] for call in save.call_args_list], ["2", "3"])
        self.assertEqual(store.get(run["id"])["calls"], 3)
        self.assertEqual(store.get(run["id"])["status"], "complete")

    def test_unconfirmed_ai_request_is_not_repeated_after_interruption(self):
        run = store.create(self.cfg, "amazon", [{"id": "1"}])
        budget = Budget(self.cfg, "amazon", run)
        with self.assertRaises(KeyboardInterrupt):
            budget.call("text:title", {"id": "1"}, {}, Mock(side_effect=KeyboardInterrupt))
        operation = Mock(return_value="do not call")
        with self.assertRaises(store.UnknownAIRequest):
            Budget(self.cfg, "amazon", run).call("text:title", {"id": "1"}, {}, operation)
        operation.assert_not_called()

    def test_storage_error_is_visible_without_breaking_the_publishing_page(self):
        import sqlite3
        self.app._switch_platform("amazon")
        with patch("aeclone.services.collect_store.history", side_effect=sqlite3.DatabaseError("invalid database")):
            self.app._switch(21)
        self.assertTrue(any(isinstance(c, ft.Text) and "任务记录暂时不可读" in c.value for c in walk(self.app.content.controls)))

    def test_money_cap_requires_prices_and_invalid_budgets_do_not_raise(self):
        self.cfg.set("amazon_collect_ai", True)
        self.cfg.set("api_key", "test")
        self.cfg.set("amazon_collect_ai_budget", 5)
        self.assertTrue(any("单价" in r.title for r in check(self.cfg, "amazon")))
        self.cfg.set("amazon_collect_ai_budget", "bad")
        self.assertTrue(any("预算参数" in r.title for r in check(self.cfg, "amazon")))

    def test_estimations_are_explicit_upper_bounds_and_unknown_price_is_not_zero_cost(self):
        self.cfg.set("amazon_collect_ai", True)
        self.cfg.set("amazon_collect_ai_fields", '["title","bullets"]')
        value = estimate(self.cfg, "amazon", 10)
        self.assertEqual(value["text"], 20)
        self.assertIsNone(value["estimate"])
        self.cfg.set("amazon_collect_text_call_price", 0.1)
        self.assertEqual(estimate(self.cfg, "amazon", 10)["estimate"], 2)

    def test_profiles_roundtrip_shop_site_prompts_and_do_not_touch_aliexpress(self):
        before = self.cfg.title_prompt
        self.app._switch_platform("amazon")
        sync(self.app, "amazon")
        self.cfg.set("amazon_collect_overrides", '{"standard_price":"12"}')
        self.cfg.set("amazon_title_prompt", "Shop one rule")
        self.cfg.set("amazon_shop_id", "store-two")
        sync(self.app, "amazon")
        self.assertEqual(self.cfg.amazon_collect_overrides, "{}")
        self.cfg.set("amazon_collect_overrides", '{"standard_price":"24"}')
        self.cfg.set("amazon_shop_id", "store-one")
        sync(self.app, "amazon")
        self.assertEqual(json.loads(self.cfg.amazon_collect_overrides)["standard_price"], "12")
        self.assertEqual(self.cfg.amazon_title_prompt, "Shop one rule")
        self.cfg.set("amazon_site", "new-site")
        sync(self.app, "amazon")
        self.assertEqual(self.cfg.amazon_collect_overrides, "{}")
        self.assertEqual(self.cfg.title_prompt, before)

    def test_detail_phase_cache_and_reference_cache_revalidate_before_writes(self):
        native = SimpleNamespace(product_detail=Mock(return_value={"product": {"id": "1"}}),
                                 attributes=Mock(return_value=[{"id": "a"}]))
        client = ReadClient(native, self.cfg, "amazon")
        client.prepare_item()
        one = client.product_detail("1")
        one["product"]["id"] = "mutated"
        self.assertEqual(client.product_detail("1")["product"]["id"], "1")
        self.assertEqual(native.product_detail.call_count, 1)
        client.attributes("category")
        client.attributes("category")
        native.attributes.assert_called_once()
        client.fresh()
        client.product_detail("1")
        client.product_detail("1")
        self.assertEqual(native.product_detail.call_count, 3)

    def test_other_shop_product_is_blocked_before_its_presets_can_apply(self):
        client = ReadClient(SimpleNamespace(product_detail=lambda pid: {"product": dict(id=pid, shopId="other-shop")}), self.cfg, "amazon")
        with self.assertRaisesRegex(ValueError, "另一家 Amazon 店铺"):
            client.product_detail("1")

    def test_statuses_never_equate_draft_to_online_publication(self):
        for record, op, expected in ((dict(checked=True, ok=True), "1", "checked"),
                (dict(saved=True, ok=True), "1", "draft_saved"), (dict(saved=True, ok=True), "3", "uncertain"),
                (dict(saved=True, ok=True), "2", "uncertain"), (dict(state="online"), "2", "published"),
                (dict(saved=True, ok=False, error="回读失败"), "1", "uncertain"),
                (dict(error="code:100521,正在审查"), "2", "reviewing"),
                (dict(error="code:100521,正在审查;code:8560,invalid"), "2", "needs_info")):
            self.assertEqual(store.status(record, op), expected)

    def test_csv_and_repair_routes_preserve_text_without_spreadsheet_formulas(self):
        csv = csv_text([dict(id="1", outcome="needs_info", error="=SUM(1,2)")])
        self.assertIn("'=SUM", csv)
        self.assertEqual(repair_page("amazon", dict(error="UPC缺失")), 17)
        self.assertEqual(repair_page("tiktok", dict(error="图片转存失败")), 27)
        self.assertEqual(repair_page("shopee", dict(error="登录失效")), 36)

    def test_nested_trade_and_tier_validation_blocks_partial_drafts(self):
        for value in (dict(price_tiers=[dict(start_quantity="", price="3")]),
                      dict(price_tiers=[dict(start_quantity=1, price=3), dict(start_quantity=1, price=4)]),
                      dict(wholesale_trade=dict(deliver_periods=[dict(quantity=2, process_period=-1)])),
                      dict(wholesale_trade=dict(inventory=[dict(warehouseCode="", value=2)]))):
            with self.assertRaises(ValueError):
                validate_overrides(value)

    def test_runtime_label_reports_actual_custom_parameters(self):
        self.app._switch_platform("amazon")
        self.cfg.set("amazon_collect_request_timeout", 47)
        self.app._switch(21)
        texts = [c.value for c in walk(self.app.content.controls) if isinstance(c, ft.Text)]
        self.assertTrue(any("自定义" in v and "47秒" in v for v in texts))
        chooser = next(c for c in walk(self.app.content.controls) if getattr(c, "label", "") == "运行参数预设")
        self.assertIsNone(chooser.value)

    def test_default_functional_page_is_lazy_and_advanced_fields_build_once(self):
        app = build_app(eager_editors=False)
        app._switch_platform("amazon")
        with patch.object(app, "_amazon_cards", wraps=app._amazon_cards) as cards:
            app._switch(19)
            cards.assert_not_called()
            tabs = next(c for c in walk(app.content.controls) if isinstance(c, ft.Tabs))
            tabs.selected_index = 1
            tabs.on_change(SimpleNamespace(control=tabs))
            cards.assert_called_once()
            tabs.on_change(SimpleNamespace(control=tabs))
            cards.assert_called_once()
        self.assertTrue(any(getattr(c, "text", "") == "AI 生成五点" for c in walk(app.content.controls)))

    def test_visual_tiers_and_trade_save_native_fields_without_losing_old_values(self):
        old_trade = {"custom": {"enabled": True, "codes": ["a", 3]}}
        self.cfg.set("alibaba_collect_overrides", json.dumps(dict(wholesale_trade=old_trade,
                     price_tiers=[dict(qty=2, price=14, market="EU")])))
        self.app._switch(32)
        controls = lambda: list(walk(self.app.content.controls))
        next(c for c in controls() if getattr(c, "text", "") == "添加报价档位").on_click(None)
        for label, value in (("起订数量", "5"), ("单价", "12.5")):
            fields = [c for c in controls() if getattr(c, "label", "") == label]
            fields[-1].value = value
        save_tiers = next(c for c in controls() if getattr(c, "text", "") == "保存阶梯报价")
        save_tiers.on_click(None)
        ov = settings(self.cfg, "alibaba")
        self.assertEqual(ov["price_tiers"], [dict(qty=2, price=14, market="EU"), dict(start_quantity="5", price="12.5")])
        fields[-1].value = "-5"
        save_tiers.on_click(None)
        self.assertEqual(settings(self.cfg, "alibaba")["price_tiers"], ov["price_tiers"])
        weight = next(c for c in controls() if getattr(c, "label", "") == "产品重量 KG")
        weight.value = "4.5"
        weight.on_change(SimpleNamespace(control=weight))
        stock = next(c for c in controls() if getattr(c, "label", "") == "库存")
        stock.value = "0"
        stock.on_change(SimpleNamespace(control=stock))
        self.assertEqual(str(self.cfg.alibaba_weight), "4.5")
        self.assertEqual(str(self.cfg.alibaba_stock), "0")

    def test_tiktok_visual_candidate_selection_uses_native_ids(self):
        self.app._switch_platform("tiktok")
        self.cfg.set("tiktok_collect_overrides", '{"category_id":"100"}')
        self.app._switch(24)
        controls = lambda: list(walk(self.app.content.controls))
        client = SimpleNamespace(category_attributes=Mock(return_value=[dict(attributeId="7", attributeName="Material", values=[dict(valueId="9", valueName="Steel")])]))
        with patch("aeclone.services.collect_automation.client_for", return_value=client), \
             patch("threading.Thread.start", lambda thread: thread.run()):
            next(c for c in controls() if getattr(c, "text", "") == "加载平台属性候选").on_click(None)
        attribute = next(c for c in controls() if getattr(c, "label", "") == "添加类目属性")
        attribute.value = "7"
        next(c for c in controls() if getattr(c, "text", "") == "添加属性").on_click(None)
        candidate = next(c for c in controls() if getattr(c, "label", "") == "Material")
        candidate.value = "0"
        next(c for c in controls() if getattr(c, "text", "") == "保存批量属性").on_click(None)
        values = settings(self.cfg, "tiktok")["product_attributes"]
        self.assertEqual(values[0]["attributeId"], "7")
        self.assertEqual(values[0]["values"][0]["valueId"], "9")

    def test_shopee_visual_attribute_edits_existing_value_with_native_id(self):
        self.app._switch_platform("shopee")
        self.cfg.set("shopee_collect_overrides", json.dumps({"categoryId": "100", "attributes": [
            {"attribute_id": 7, "attribute_value_list": [{"value_id": 9, "original_value_name": "Steel"}],
             "source": "existing"}]}))
        self.app._switch(38)
        controls = lambda: list(walk(self.app.content.controls))
        client = SimpleNamespace(attributes=Mock(return_value={"attributeList": [
            dict(attribute_id=7, name="Material", attribute_value_list=[
                dict(value_id=9, original_value_name="Steel"), dict(value_id=10, original_value_name="Aluminum")]),
            dict(attribute_id=8, name="Finish", attribute_value_list=[dict(value_id=11, original_value_name="Gloss")])]}))
        with patch("aeclone.services.collect_automation.client_for", return_value=client), \
             patch("threading.Thread.start", lambda thread: thread.run()):
            next(c for c in controls() if getattr(c, "text", "") == "加载平台属性候选").on_click(None)
        option = next(c for c in controls() if getattr(c, "label", "") == "Material")
        self.assertEqual(option.value, "0")
        option.value = "1"
        next(c for c in controls() if getattr(c, "label", "") == "添加类目属性").value = "8"
        next(c for c in controls() if getattr(c, "text", "") == "添加属性").on_click(None)
        next(c for c in controls() if getattr(c, "label", "") == "Finish").value = "0"
        next(c for c in controls() if getattr(c, "text", "") == "保存批量属性").on_click(None)
        self.assertEqual(settings(self.cfg, "shopee")["attributes"], [
            {"attribute_id": 7, "attribute_value_list": [{"value_id": 10, "original_value_name": "Aluminum"}],
             "source": "existing"}, {"attribute_id": 8, "attribute_value_list": [
                 {"value_id": 11, "original_value_name": "Gloss"}]}])

    def test_result_readback_only_updates_authoritative_state_and_never_resubmits(self):
        self.app._switch_platform("amazon")
        run = store.create(self.cfg, "amazon", [{"id": "1"}])
        store.item_update(run, "1", "done", result=dict(id="1", ok=False, saved=False, outcome="uncertain", error="timeout"))
        self.app._switch(21)
        button = next(c for c in walk(self.app.content.controls) if getattr(c, "text", "") == "回读平台状态")
        client = self.client()
        client.product_detail.side_effect = lambda pid: {"product": {"id": pid, "dxmState": "online"}}
        with patch("aeclone.services.collect_automation.client_for", return_value=client), \
             patch("threading.Thread.start", lambda thread: thread.run()), \
             patch("aeclone.services.collect_automation.process_one") as save:
            button.on_click(None)
            save.assert_not_called()
        self.assertEqual(store.get(run)["items"][0]["result"]["outcome"], "published")

    def test_result_filter_paging_export_and_persisted_resume_entry(self):
        self.app._switch_platform("amazon")
        run = store.create(self.cfg, "amazon", [{"id": str(i)} for i in range(25)])
        for i in range(24):
            store.item_update(run, str(i), "done", result=dict(id=str(i), ok=False, error="UPC缺失", outcome="needs_info"))
        self.app._switch(21)
        controls = lambda: list(walk(self.app.content.controls))
        self.assertTrue(any(getattr(c, "text", "") == "继续未完成任务" for c in controls()))
        next(c for c in controls() if getattr(c, "text", "") == "结果下一页").on_click(None)
        self.assertTrue(any(isinstance(c, ft.Text) and "第2/2页" in c.value for c in controls()))
        search = next(c for c in controls() if getattr(c, "label", "") == "搜索本次任务结果")
        search.value = "no matches"
        search.on_change(SimpleNamespace(control=search))
        self.assertTrue(any(isinstance(c, ft.Text) and "0条" in c.value for c in controls()))
        export = next(c for c in controls() if getattr(c, "text", "") == "导出筛选结果")
        with patch.object(ft.FilePicker, "save_file") as picker:
            export.on_click(None)
            picker.assert_called_once()
        jump = next(c for c in controls() if getattr(c, "text", "") == "查看任务结果")
        with patch.object(ft.Column, "scroll_to") as scroll:
            jump.on_click(None)
            scroll.assert_called_once_with(key="collect-results", duration=200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
