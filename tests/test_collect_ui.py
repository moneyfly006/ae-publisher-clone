"""Exercise actual Flet controls for automatic settings and platform entrypoints."""
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from ui_sandbox import sandbox, build_app, walk
import flet as ft
from aeclone.config import AppConfig
from aeclone.ui.collect_workflow import RUN_PAGE, SECTIONS, launch
from aeclone.platforms import ALIEXPRESS


class CollectUiTests(unittest.TestCase):
    def setUp(self):
        self.context = sandbox()
        self.cfg = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.app = build_app()

    def controls(self):
        return list(walk(self.app.content.controls))

    def test_no_independent_list_in_any_navigation(self):
        for platform in RUN_PAGE:
            self.app._switch_platform(platform)
            self.assertNotIn("采集箱商品", [p.label for p in self.app.active_platform.pages])
            self.assertEqual(self.app.btn_run_text.value, "自动处理采集箱")
            self.app._switch(0)
            self.assertNotIn("选择商品", [getattr(c, "text", "") for c in self.controls()])

    def test_old_list_routes_go_to_run_page(self):
        for platform, retired in (("alibaba", 29), ("tiktok", 23), ("shopee", 37)):
            self.app._switch_platform(platform)
            self.app._switch(retired)
            self.assertEqual(self.app._page_idx, RUN_PAGE[platform])

    def test_each_run_page_has_scope_destination_and_no_list(self):
        for platform, page in RUN_PAGE.items():
            self.app._switch_platform(platform)
            self.app._switch(page)
            labels = [getattr(c, "label", "") for c in self.controls()]
            self.assertIn("采集箱关键词（可选）", labels)
            self.assertIn("完成后去向", labels)
            self.assertNotIn("商品 ID", labels)
            self.assertIn("开始自动处理", [getattr(c, "text", "") for c in self.controls()])

    def test_tiktok_first_tab_has_only_function_specific_settings(self):
        self.app._switch_platform("tiktok")
        expected = {24: "统一类目 ID（可选）", 25: "统一售价（可选）", 35: "统一库存（可选，0 表示清零）",
                    26: "启用自动AI文案（付费）", 27: "自动处理商品图片（付费 AI + 图床）"}
        for page, label in expected.items():
            self.app._switch(page)
            tabs = next(c for c in self.controls() if isinstance(c, ft.Tabs))
            first = list(walk([tabs.tabs[0].content]))
            labels = [getattr(c, "label", "") for c in first]
            self.assertIn(label, labels)
            for other_page, other_label in expected.items():
                if other_page != page:
                    self.assertNotIn(other_label, labels)

    def test_batch_changes_persist_without_overwriting_single_product(self):
        self.app._switch_platform("tiktok")
        self.cfg.set("tiktok_price", "87")
        self.app._switch(25)
        field = next(c for c in self.controls() if getattr(c, "label", "") == "统一售价（可选）")
        field.value = "19.50"
        field.on_change(SimpleNamespace(control=field))
        self.app._switch(26)
        self.app._switch(25)
        field = next(c for c in self.controls() if getattr(c, "label", "") == "统一售价（可选）")
        self.assertEqual(field.value, "19.50")
        self.assertEqual(self.cfg.tiktok_price, "87")
        self.assertEqual(json.loads(self.cfg.tiktok_collect_overrides)["price"], "19.50")

    def test_launch_confirmation_contains_current_range_and_paid_flags(self):
        self.app._switch_platform("amazon")
        self.cfg.set("amazon_shop_id", "shop-test")
        self.cfg.set("amazon_site", "A2EUQ1WTGCTBG2")
        self.app._switch(21)
        self.cfg.set("amazon_collect_keyword", "pool")
        self.cfg.set("amazon_collect_limit", 2)
        self.cfg.set("amazon_collect_ai", True)
        self.cfg.set("api_key", "test-only-key")
        with patch.object(self.app, "_confirm") as confirm:
            launch(self.app, "amazon")
        text = confirm.call_args.args[1]
        self.assertIn("pool", text)
        self.assertIn("2 件", text)
        self.assertIn("计费", text)
        self.assertFalse(getattr(self.app, "_running", False))

    def test_stop_requests_do_not_use_smt_abort_semantics(self):
        self.app._collect_running = "amazon"
        self.app._running = True
        self.app._abort_now = False
        self.app._request_stop()
        self.assertTrue(self.app._stop_requested)
        self.assertFalse(self.app._abort_now)

    def test_automatic_task_cannot_overlap_manual_images_even_after_confirmation(self):
        self.app._switch_platform("amazon")
        self.cfg.set("amazon_shop_id", "shop-test")
        self.cfg.set("amazon_site", "A2EUQ1WTGCTBG2")
        self.app._switch(21)
        with patch.object(self.app, "_confirm") as confirm:
            launch(self.app, "amazon")
        start = confirm.call_args.args[2]
        self.app._media_jobs = {("amazon", "one")}
        with patch("threading.Thread.start") as worker:
            start()
            worker.assert_not_called()
        self.assertFalse(getattr(self.app, "_running", False))
        with patch.object(self.app, "_confirm") as confirm:
            launch(self.app, "amazon")
            confirm.assert_not_called()

    def test_aliexpress_navigation_unchanged(self):
        self.assertEqual([p.index for p in ALIEXPRESS.pages], [0, 1, 2, 6, 7, 3, 5, 4, 8, 46, 10, 9, 11])
        self.app._switch_platform("aliexpress")
        for page in ALIEXPRESS.pages:
            self.app._switch(page.index)
            self.assertEqual(self.app._current, page.index)

    def test_invalid_inventory_is_not_saved_or_silently_converted_to_zero(self):
        self.app._switch_platform("amazon")
        self.app._switch(34)
        field = next(c for c in self.controls() if getattr(c, "label", "") == "统一库存（可选，0 表示清零）")
        field.value = "bad"
        field.on_change(SimpleNamespace(control=field))
        self.assertTrue(field.error_text)
        self.assertNotIn("quantity", json.loads(self.cfg.amazon_collect_overrides))
        with patch.object(self.app, "_confirm") as confirm:
            launch(self.app, "amazon")
            confirm.assert_not_called()
        field.value = "0"
        field.on_change(SimpleNamespace(control=field))
        self.assertEqual(json.loads(self.cfg.amazon_collect_overrides)["quantity"], "0")
        self.assertFalse(self.app._collect_errors["amazon"])

    def test_corrupt_preset_is_backed_up_before_repair_and_accepts_visual_edits(self):
        self.app._switch_platform("amazon")
        broken = '{"standard_price":"15", broken data'
        self.cfg.set("amazon_collect_overrides", broken)
        self.app._switch(18)
        price = next(c for c in self.controls() if getattr(c, "label", "") == "统一售价（可选）")
        price.value = "20"
        price.on_change(SimpleNamespace(control=price))
        self.assertEqual(self.cfg.amazon_collect_overrides, broken)
        self.assertIn("修复", price.error_text)
        self.app._switch(17)
        save_attrs = next(c for c in self.controls() if getattr(c, "text", "") == "保存批量属性")
        save_attrs.on_click(None)
        self.assertEqual(self.cfg.amazon_collect_overrides, broken)
        repair = next(c for c in self.controls() if getattr(c, "text", "") == "备份并修复预设")
        repair.on_click(None)
        self.app.page.dialog.actions[0].on_click(None)
        self.assertEqual(self.cfg.amazon_collect_overrides, broken)
        repair.on_click(None)
        self.app.page.dialog.actions[1].on_click(None)
        backup, = (AppConfig.path().parent / "preset-backups").glob("amazon-*.bak")
        self.assertEqual(backup.read_text(), broken)
        self.assertEqual(json.loads(self.cfg.amazon_collect_overrides), {})
        self.assertFalse(self.app._collect_errors["amazon"])
        next(c for c in self.controls() if getattr(c, "text", "") == "添加属性").on_click(None)
        next(c for c in self.controls() if getattr(c, "label", "") == "属性名称").value = "material"
        next(c for c in self.controls() if getattr(c, "label", "") == "属性值").value = "Steel"
        next(c for c in self.controls() if getattr(c, "text", "") == "保存批量属性").on_click(None)
        self.assertEqual(json.loads(self.cfg.amazon_collect_overrides), {"attributes": {"material": "Steel"}})

    def test_repair_keeps_valid_fields_from_old_preset(self):
        self.app._switch_platform("amazon")
        self.cfg.set("amazon_collect_overrides", '{"price":19,"standard_price":"21","quantity":-3,"attributes":{"Material":["Steel"]}}')
        self.app._switch(18)
        next(c for c in self.controls() if getattr(c, "text", "") == "备份并修复预设").on_click(None)
        self.app.page.dialog.actions[1].on_click(None)
        self.assertEqual(json.loads(self.cfg.amazon_collect_overrides),
                         {"standard_price": "21", "attributes": {"Material": ["Steel"]}})

    def test_recovery_buttons_are_wired_to_readonly_and_write_modes(self):
        for platform, page in (("alibaba", 33), ("amazon", 21)):
            self.app._switch_platform(platform)
            self.app._switch(page)
            for label, dry_run in (("分析发布失败", True), ("修复发布失败商品", False)):
                button = next(c for c in self.controls() if getattr(c, "text", "") == label)
                with patch("aeclone.ui.collect_workflow.launch") as start:
                    button.on_click(SimpleNamespace(control=button))
                self.assertEqual(start.call_args.args, (self.app, platform))
                self.assertTrue(start.call_args.kwargs["failures"])
                self.assertEqual(start.call_args.kwargs.get("dry_run", False), dry_run)

    def test_all_prompt_editors_have_nonempty_defaults_and_restore(self):
        from aeclone.ui.collect_workflow import SECTIONS
        from aeclone.services.platform_presets import prompt_specs
        for platform, pages in SECTIONS.items():
            self.app._switch_platform(platform)
            for page, section in pages.items():
                specs = prompt_specs(platform, section)
                if not specs:
                    continue
                self.app._switch(page)
                tabs = next(c for c in self.controls() if isinstance(c, ft.Tabs))
                controls = list(walk([tabs.tabs[0].content]))
                for key, label, default in specs:
                    editor = next(c for c in controls if isinstance(c, ft.TextField) and c.label == label + "提示词")
                    self.assertTrue(editor.value.strip())
                    editor.value = "test " + key
                    editor.on_change(SimpleNamespace(control=editor))
                    self.assertEqual(getattr(self.cfg, key), editor.value)
                restore = next(c for c in controls if getattr(c, "text", "") == "恢复本页提示词预设")
                restore.on_click(SimpleNamespace(control=restore))
                for key, label, default in specs:
                    self.assertEqual(getattr(self.cfg, key), default)

    def test_worker_uses_confirmed_snapshot_and_returns_to_idle(self):
        from aeclone.ui.collect_workflow import launch
        self.app._switch_platform("amazon")
        self.cfg.set("amazon_shop_id", "shop-test")
        self.cfg.set("amazon_site", "A2EUQ1WTGCTBG2")
        self.app._switch(21)
        self.cfg.set("amazon_collect_keyword", "pool")
        with patch.object(self.app, "_confirm") as confirm:
            launch(self.app, "amazon", failures=True)
        confirmed = confirm.call_args.args[2]
        self.cfg.set("amazon_collect_keyword", "changed later")
        with patch("aeclone.services.listing_recovery.run_failed_recovery", return_value=[]) as run, \
             patch("threading.Thread.start", lambda thread: thread.run()):
            confirmed()
        self.assertEqual(run.call_args.args[0].amazon_collect_keyword, "pool")
        self.assertFalse(self.app._running)
        self.assertEqual(self.app._page_idx, 21)


if __name__ == "__main__":
    unittest.main(verbosity=2)
