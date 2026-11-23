"""Functional pages share one rule editor without losing item-specific edits."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import flet as ft
from ui_sandbox import sandbox, build_app, walk
from aeclone.ui.collect_workflow import SECTIONS
from aeclone.services.platform_presets import prompt_specs


class FunctionDedupTests(unittest.TestCase):
    def setUp(self):
        context = sandbox()
        self.cfg = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.app = build_app()

    def controls(self):
        return list(walk(self.app.content.controls))

    def test_rules_have_exactly_one_editor_and_one_functional_owner(self):
        for platform, pages in SECTIONS.items():
            self.app._switch_platform(platform)
            locations = {}
            for page, section in pages.items():
                self.app._switch(page)
                for control in self.controls():
                    if isinstance(control, ft.TextField) and "提示词" in str(control.label):
                        locations.setdefault(control.label, []).append(page)
                if section == "text":
                    for _, label, _ in prompt_specs(platform):
                        self.assertEqual(locations[label + "提示词"], [page])
                if section == "media":
                    buttons = [getattr(c, "text", "") for c in self.controls()]
                    self.assertEqual(buttons.count("打开图片工作台"), 1)
            self.assertEqual(locations["自动SKU提示词"],
                             [32 if platform == "alibaba" else {"amazon": 34, "tiktok": 35, "shopee": 40}[platform]])
            self.assertEqual(locations["自动属性建议提示词"],
                             [{"alibaba": 30, "amazon": 17, "tiktok": 24, "shopee": 38}[platform]])
            self.assertTrue(all(len(pages) == 1 for pages in locations.values()), locations)
            self.assertNotIn("本次图片提示词", locations)
            self.assertNotIn("SKU 编码提示词", locations)

    def test_advanced_editors_do_not_offer_another_set_of_rules(self):
        for platform, pages in SECTIONS.items():
            if platform == "alibaba":
                continue  # Alibaba's optional editor has a single workspace instead of inline tabs.
            self.app._switch_platform(platform)
            for page in pages:
                self.app._switch(page)
                tabs = next(c for c in self.controls() if isinstance(c, ft.Tabs))
                advanced = list(walk([tabs.tabs[1].content]))
                self.assertFalse(any(isinstance(c, ft.Tabs) for c in advanced), (platform, page))
                self.assertFalse(any(isinstance(c, ft.TextField) and "提示词" in str(c.label)
                                     for c in advanced), (platform, page))
                self.assertFalse(any(isinstance(c, ft.Switch) and "AI" in str(c.label)
                                     for c in advanced), (platform, page))

    def test_alibaba_queue_redirects_to_one_publishing_page(self):
        self.app._switch_platform("alibaba")
        self.assertNotIn(8, [p.index for p in self.app.active_platform.pages])
        self.app._switch(8)
        self.assertEqual(self.app._page_idx, 33)
        self.assertEqual(self.app._current, 33)
        self.app._switch_platform("aliexpress")
        self.app._switch(8)
        self.assertEqual(self.app._page_idx, 8)

    def test_alibaba_has_no_second_company_or_trade_defaults(self):
        for page in (31, 32):
            self.app._switch(page)
            labels = [str(getattr(c, "label", "")) for c in self.controls()]
            self.assertNotIn("公司介绍（英文，会提交 companyDesc）", labels)
            self.assertNotIn("FOB 最低价", labels)
            self.assertNotIn("默认关键词（Alibaba 必填 3 个，逗号分隔，最多 5 个）", labels)
        self.assertNotIn("最低价", labels)
        from aeclone.ui.alibaba_editor import remember_product
        self.cfg.set("alibaba_test_product_id", "A")
        remember_product(self.app, {"id": "A", "sourcingTrade": {"fob_min_price": 20}})
        self.app._switch(12)
        self.assertIn("最低价", [getattr(c, "label", "") for c in self.controls()])

    def test_manual_images_read_latest_shared_prompt_and_indices(self):
        from aeclone.ui.media_actions import controls
        from aeclone.services.image_pipeline import ImagePipelineResult, ImageStep
        self.app._collect_editor_only = True
        urls = ["https://example.invalid/first.jpg", "https://example.invalid/second.jpg"]
        applied = Mock()
        box = controls(self.app, lambda: urls, applied, platform="amazon")
        self.app._collect_editor_only = False
        # Change the main settings after constructing the item action.
        self.cfg.set("amazon_collect_image_prompt", "Shared current rule")
        self.cfg.set("amazon_collect_image_indices", "2")
        button = next(c for c in walk([box]) if getattr(c, "text", "") == "处理并回填图片")
        button.on_click(None)
        pipeline = Mock()
        pipeline.run.return_value = ImagePipelineResult([ImageStep("AI", True, "https://example.invalid/temp.jpg")])
        pipeline._download.return_value = Path("/private/tmp/test-image.jpg")
        with patch("aeclone.services.image_pipeline.ImagePipeline", return_value=pipeline), \
             patch("aeclone.services.hosting.upload", return_value="https://example.invalid/stable.jpg"), \
             patch("threading.Thread.start", lambda thread: thread.run()):
            self.app.page.dialog.actions[1].on_click(None)
        self.assertEqual(pipeline.run.call_args.args[0], urls[1])
        self.assertIn("Shared current rule", pipeline.run.call_args.kwargs["prompt"])
        applied.assert_called_once_with([urls[0], "https://example.invalid/stable.jpg"])

    def test_amazon_manual_ai_runs_only_five_points_without_enabling_batch_ai(self):
        from test_amazon_ui_page import fake_backend, sync_threads, find_button, click
        self.app._switch_platform("amazon")
        self.app._amz_detail = {"product": {"id": "123", "title": "Original product"}}
        self.app._amz_form = {"id": "123", "title": "Original product", "bullets": []}
        self.app._switch(19)
        button = find_button(self.app, "AI 生成五点", exact=True)
        self.cfg.set("amazon_collect_ai", False)
        with fake_backend() as backend, sync_threads():
            click(button)
            click(self.app.page.dialog.actions[1])
        self.assertEqual(backend.calls["ai"], [{"field": "bullets", "count": 5}])
        self.assertFalse(self.cfg.amazon_collect_ai)

    def test_amazon_item_sku_uses_shared_rule_without_mutating_old_settings(self):
        from test_amazon_ui_page import fake_backend, sync_threads, find_button, click
        self.app._switch_platform("amazon")
        self.app._amz_detail = {"product": {"id": "123", "title": "Original product"}}
        self.cfg.set("amazon_sku_prompt", "Legacy saved rule")
        self.app._switch(34)
        # A change made on this same page must reach its existing callback.
        self.cfg.set("amazon_collect_sku_prompt", "Shared SKU rule")
        with fake_backend() as backend, sync_threads(), \
             patch.object(backend.pipeline, "ai_generate_field", return_value=("TOOL", [])) as generate:
            click(find_button(self.app, "AI 生成 SKU", exact=True))
            click(self.app.page.dialog.actions[1])
        self.assertEqual(generate.call_args.args[1].amazon_sku_prompt, "Shared SKU rule")
        self.assertEqual(self.app._amz_form["sku"], "TOOL-123")
        self.assertEqual(self.cfg.amazon_sku_prompt, "Legacy saved rule")


if __name__ == "__main__":
    unittest.main(verbosity=2)
