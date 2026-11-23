from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from ui_sandbox import sandbox, build_app, walk
import flet as ft
from aeclone.config import AppConfig
from aeclone.collect_defaults import PARAMETERS, PRESETS
from aeclone.services.collect_runtime import ReadClient, RunPolicy, RunStopped, apply_preset, interrupted_wait
from aeclone.services.collect_automation import run_collect, process_images, settings
from aeclone.services.collect_preflight import check
from aeclone.services.listing_recovery import run_failed_recovery
from aeclone.services import collect_store as store


class RuntimeTests(unittest.TestCase):
    def cfg(self, **values):
        return AppConfig(dict(amazon_collect_interval=0, **values))

    def test_all_platforms_start_with_bounded_nonpaid_defaults(self):
        cfg = AppConfig()
        for platform in ("alibaba", "amazon", "tiktok", "shopee"):
            for suffix, spec in PARAMETERS.items():
                self.assertEqual(getattr(cfg, platform + "_collect_" + suffix), spec[1])
            self.assertEqual(check(cfg, platform), [])
            self.assertFalse(getattr(cfg, platform + "_collect_ai"))
            self.assertEqual(getattr(cfg, platform + "_collect_op"), "1")

    def test_presets_only_change_active_platform_runtime(self):
        cfg = self.cfg(amazon_collect_op="2", amazon_collect_overrides='{"standard_price": "22"}', amazon_collect_ai=True)
        before = cfg.as_dict()
        apply_preset(cfg, "amazon", "slow")
        changed = {k for k, value in cfg.as_dict().items() if value != before[k]}
        self.assertTrue(changed)
        self.assertTrue(changed <= {"amazon_collect_" + s for s in PARAMETERS})

    def test_malformed_runtime_and_boolean_flags_are_blocked_before_network(self):
        for suffix, values in (("request_timeout", [0, None, "bad", True, 121]),
                               ("read_retries", [-1, 4, "1.5", False]),
                               ("interval", [-1, "nan", "inf", 31]),
                               ("failure_limit", [-1, 21, 1.5]),
                               ("ai", ["false", "true", 0, None])):
            for value in values:
                cfg = self.cfg()
                cfg.set("amazon_collect_" + suffix, value)
                self.assertTrue(check(cfg, "amazon"), (suffix, value))
                with patch("aeclone.services.collect_automation.client_for") as client:
                    with self.assertRaises(ValueError):
                        run_collect(cfg, "amazon")
                    client.assert_not_called()

    def test_read_retry_is_bounded_but_write_is_never_retried(self):
        client = SimpleNamespace(timeout=99, product_detail=Mock(side_effect=[TimeoutError("timeout"), {"id": "1"}]),
                                 save_product=Mock(side_effect=TimeoutError("timeout")))
        wrapped = ReadClient(client, self.cfg(), "amazon")
        with patch("aeclone.services.collect_runtime.interrupted_wait", return_value=True):
            self.assertEqual(wrapped.product_detail("1"), {"id": "1"})
            with self.assertRaises(TimeoutError):
                wrapped.save_product({})
        self.assertEqual(client.timeout, 30)
        self.assertEqual(client.product_detail.call_count, 2)
        client.save_product.assert_called_once()

    def test_native_amazon_and_tiktok_retry_is_not_multiplied(self):
        from aeclone.integrations.amazon import AmazonClient, _Retryable as AmazonRetry
        from aeclone.integrations.tiktok import TikTokClient, _Retryable as TikTokRetry
        for cls, retry_error, platform in ((AmazonClient, AmazonRetry, "amazon"), (TikTokClient, TikTokRetry, "tiktok")):
            client = cls({"test": "only"})
            cfg = AppConfig({platform + "_collect_read_retries": 2})
            wrapped = ReadClient(client, cfg, platform)
            with patch.object(client, "_request_once", side_effect=retry_error("请求超时")) as request, \
                 patch("aeclone.services.collect_runtime.interrupted_wait", return_value=True):
                with self.assertRaises(RuntimeError):
                    wrapped.list_products("draft", 1, 10)
            self.assertEqual(request.call_count, 3)

    def test_login_failure_stops_after_first_item_without_read_retry(self):
        client = SimpleNamespace(product_detail=Mock(side_effect=RuntimeError("登录已失效（HTTP 401）")))
        cfg = self.cfg(amazon_collect_ai=True, api_key="test-only-key")
        result = run_collect(cfg, "amazon", client=client, rows=[{"id": "1"}, {"id": "2"}], dry_run=False)
        self.assertEqual(len(result), 1)
        client.product_detail.assert_called_once()
        self.assertIn("登录", result[0]["halt_reason"])

    def test_consecutive_failures_stop_at_threshold_and_success_resets(self):
        for outcomes, expected in (([False] * 5, 3), ([False, False, True, False, False], 5)):
            with patch("aeclone.services.collect_automation.process_one", side_effect=[
                    SimpleNamespace(ok=ok, saved=False, error="invalid" if not ok else "") for ok in outcomes]):
                result = run_collect(self.cfg(), "amazon", client=object(), rows=[{"id": str(i)} for i in range(5)])
            self.assertEqual(len(result), expected)

    def test_wait_and_retry_can_be_stopped(self):
        self.assertFalse(interrupted_wait(20, lambda: True))
        client = SimpleNamespace(product_detail=Mock(side_effect=TimeoutError("timeout")))
        stopped = iter([False, True])
        wrapped = ReadClient(client, self.cfg(), "amazon", stop=lambda: next(stopped, True))
        with self.assertRaises(RunStopped):
            wrapped.product_detail("1")
        client.product_detail.assert_called_once()

    def test_stop_does_not_prevent_first_readback_of_already_submitted_item(self):
        client = SimpleNamespace(product_detail=Mock(return_value={"id": "1"}))
        wrapped = ReadClient(client, self.cfg(), "amazon", stop=lambda: True)
        self.assertEqual(wrapped.product_detail("1"), {"id": "1"})

    def test_threshold_zero_continues_and_interval_wait_observes_stop(self):
        cfg = self.cfg(amazon_collect_failure_limit=0)
        policy = RunPolicy(cfg, "amazon", lambda: False)
        self.assertTrue(policy.before(1))
        client = ReadClient(object(), cfg, "amazon")
        for _ in range(30):
            self.assertTrue(policy.after(dict(ok=False, error="invalid"), client))
        policy.stop = lambda: True
        self.assertFalse(policy.before(2))

    def test_stopping_during_scope_retry_is_a_clean_stop_without_any_save(self):
        client = SimpleNamespace(list_products=Mock(side_effect=TimeoutError("timeout")))
        for runner in (run_collect, run_failed_recovery):
            with patch("aeclone.services.collect_runtime.interrupted_wait", return_value=False), \
                 patch("aeclone.services.collect_automation.process_one") as save:
                self.assertEqual(runner(self.cfg(), "amazon", client=client), [])
                save.assert_not_called()

    def test_stopping_during_detail_retry_leaves_item_resumable(self):
        for runner, kind in ((run_collect, "collect"), (run_failed_recovery, "recovery")):
            with self.subTest(kind=kind), sandbox() as cfg:
                cfg.set("amazon_collect_interval", 0)
                if kind == "collect":
                    cfg.set("amazon_collect_ai", True)
                    cfg.set("api_key", "test-only-key")
                client = SimpleNamespace(
                    list_products=Mock(return_value={"items": [{"id": "1"}], "total_page": 1}),
                    product_detail=Mock(side_effect=TimeoutError("request timed out")))
                with patch("aeclone.services.collect_runtime.interrupted_wait", return_value=False), \
                     patch("aeclone.services.collect_automation.process_one") as save:
                    result, = runner(cfg, "amazon", client=client, dry_run=False)
                    save.assert_not_called()
                self.assertEqual(result["outcome"], "stopped")
                run = store.latest(cfg, "amazon", kind=kind)
                self.assertEqual(run["status"], "stopped")
                self.assertEqual(run["items"][0]["phase"], "queued")

    def test_tiktok_detail_is_checked_against_selected_site(self):
        cfg = AppConfig({"tiktok_shop_id": "selected", "tiktok_site": "gb"})
        client = SimpleNamespace(product_detail=Mock(return_value={
            "product": {"id": "1", "shopId": "selected", "site": "US"}}))
        with self.assertRaisesRegex(ValueError, "另一 TikTok 站点"):
            ReadClient(client, cfg, "tiktok").product_detail("1")

    def test_blank_auxiliary_prompts_use_presets_without_changing_user_config(self):
        from aeclone.services.collect_automation import isolated_config
        cfg = self.cfg(amazon_collect_sku_prompt="  ", amazon_collect_attribute_prompt="  ")
        resolved = isolated_config(cfg, "amazon")
        self.assertTrue(resolved.amazon_collect_sku_prompt.strip())
        self.assertTrue(resolved.amazon_collect_attribute_prompt.strip())
        self.assertEqual(cfg.amazon_collect_sku_prompt, "  ")

    def test_wrong_product_details_never_start_paid_processing(self):
        from aeclone.services.collect_ai import prepare
        for detail in ({}, {"product": {}}, {"product": {"id": "wrong"}}, [], None):
            client = SimpleNamespace(product_detail=lambda _: detail)
            with patch("aeclone.api.ApiClient") as ai:
                with self.assertRaises(ValueError):
                    prepare("amazon", "1", self.cfg(amazon_collect_ai=True), client, {})
                with self.assertRaises(ValueError):
                    process_images("amazon", "1", self.cfg(), client, {})
                ai.assert_not_called()

    def test_recovery_stops_on_invalid_login(self):
        client = SimpleNamespace(list_products=lambda *a: {"items": [{"id": "1"}, {"id": "2"}], "total": 2},
                                 product_detail=Mock(side_effect=RuntimeError("登录已失效")))
        results = run_failed_recovery(self.cfg(), "amazon", client=client)
        self.assertEqual(len(results), 1)
        self.assertIn("halt_reason", results[0])

    def test_platform_attribute_structures_and_boolean_numbers_are_rejected(self):
        for platform, raw in (("tiktok", '{"product_attributes": {"Material": "Steel"}}'),
                              ("amazon", '{"attributes": []}'), ("shopee", '{"attributes": {}}'),
                              ("alibaba", '{"price_tiers": [1,2]}'), ("amazon", '{"quantity": true}')):
            with self.assertRaises(ValueError):
                settings(AppConfig({platform + "_collect_overrides": raw}), platform)


class RuntimeUiTests(unittest.TestCase):
    def test_every_platform_can_preview_and_apply_every_preset_without_changing_destination(self):
        import json
        from aeclone.ui.collect_workflow import RUN_PAGE
        with sandbox() as cfg:
            app = build_app()
            for platform, page in RUN_PAGE.items():
                app._switch_platform(platform)
                cfg.set(platform + "_collect_op", "2")
                for name, (_, values) in PRESETS.items():
                    app._switch(page)
                    controls = list(walk(app.content.controls))
                    chooser = next(c for c in controls if getattr(c, "label", "") == "运行参数预设")
                    previous = cfg.as_dict()
                    chooser.value = name
                    chooser.on_change(SimpleNamespace(control=chooser))
                    self.assertEqual(cfg.as_dict(), previous)
                    next(c for c in controls if getattr(c, "text", "") == "应用运行预设").on_click(None)
                    disk = json.loads(cfg.path().read_text())
                    for key, value in values.items():
                        self.assertEqual(disk[platform + "_collect_" + key], value)
                    self.assertEqual(getattr(cfg, platform + "_collect_op"), "2")

    def test_thread_start_failure_does_not_leave_the_app_permanently_running(self):
        from aeclone.ui.collect_workflow import launch
        with sandbox() as cfg:
            cfg.set("amazon_shop_id", "shop-test")
            cfg.set("amazon_site", "A2EUQ1WTGCTBG2")
            app = build_app()
            app._switch_platform("amazon")
            with patch.object(app, "_confirm") as confirm:
                launch(app, "amazon")
            with patch("threading.Thread.start", side_effect=RuntimeError("worker unavailable")):
                confirm.call_args.args[2]()
            self.assertFalse(app._running)
            self.assertIsNone(app._collect_running)
            self.assertFalse(app._stop_requested)

    def test_preset_button_saves_only_current_platform_and_clears_invalid_runtime_field(self):
        with sandbox() as cfg:
            app = build_app()
            app._switch_platform("amazon")
            app._switch(21)
            controls = list(walk(app.content.controls))
            field = next(c for c in controls if getattr(c, "label", "") == "平台请求超时（秒）")
            field.value = "-1"
            field.on_change(SimpleNamespace(control=field))
            self.assertTrue(field.error_text)
            chooser = next(c for c in controls if getattr(c, "label", "") == "运行参数预设")
            chooser.value = "slow"
            button = next(c for c in controls if getattr(c, "text", "") == "应用运行预设")
            button.on_click(None)
            self.assertEqual(cfg.amazon_collect_request_timeout, 60)
            self.assertEqual(cfg.alibaba_collect_request_timeout, 30)
            self.assertFalse(field.error_text)
            self.assertFalse(app._collect_errors["amazon"])
            app._switch(19)
            app._switch(21)
            self.assertEqual(next(c for c in walk(app.content.controls) if getattr(c, "label", "") == "平台请求超时（秒）").value, "60")


if __name__ == "__main__":
    unittest.main(verbosity=2)
