"""验证图片序号、处理失败保留原图及稳定 URL 回填；不调用真实 AI。"""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import flet as ft
from ui_sandbox import build_app, sandbox, walk
from aeclone.ui.media_actions import controls
from aeclone.services.image_pipeline import ImagePipelineResult, ImageStep


class MediaActionTests(unittest.TestCase):
    def test_empty_hosted_url_never_replaces_original_image_or_reports_success(self):
        with sandbox():
            app = build_app()
            applied = Mock()
            box = controls(app, lambda: ["https://example.invalid/original.jpg"], applied, platform="amazon")
            button = next(c for c in walk([box]) if getattr(c, "text", "") == "处理并回填图片")
            pipeline = Mock()
            pipeline.run.return_value = ImagePipelineResult([ImageStep("AI", True, "https://example.invalid/temp.jpg")])
            pipeline._download.return_value = Path("/private/tmp/mock-image.jpg")
            button.on_click(None)
            with patch("aeclone.services.image_pipeline.ImagePipeline", return_value=pipeline), \
                 patch("aeclone.services.hosting.upload", return_value=""), \
                 patch("threading.Thread.start", lambda thread: thread.run()):
                app.page.dialog.actions[1].on_click(None)
            applied.assert_not_called()
            texts = [c.value for c in walk([box]) if isinstance(c, ft.Text)]
            self.assertTrue(any("失败" in text and "原商品图片保留" in text for text in texts))

    def test_confirming_same_image_action_twice_starts_only_one_worker(self):
        with sandbox():
            app = build_app()
            box = controls(app, lambda: ["https://example.invalid/original.jpg"], Mock(), platform="amazon")
            button = next(c for c in walk([box]) if getattr(c, "text", "") == "处理并回填图片")
            with patch.object(app, "_confirm") as confirm, patch("threading.Thread.start") as start:
                button.on_click(None)
                confirmed = confirm.call_args.args[2]
                confirmed()
                confirmed()
            start.assert_called_once()

    def test_rebuilding_page_does_not_start_another_image_worker_for_same_product(self):
        with sandbox():
            app = build_app()
            app.cfg.set("amazon_test_product_id", "same")
            with patch.object(app, "_confirm") as confirm, patch("threading.Thread.start") as start:
                for _ in range(2):
                    box = controls(app, lambda: ["https://example.invalid/original.jpg"], Mock(), platform="amazon")
                    button = next(c for c in walk([box]) if getattr(c, "text", "") == "处理并回填图片")
                    button.on_click(None)
                    confirm.call_args.args[2]()
            start.assert_called_once()

    def test_pending_image_confirmation_cannot_overlap_running_automatic_task(self):
        with sandbox():
            app = build_app()
            box = controls(app, lambda: ["https://example.invalid/original.jpg"], Mock(), platform="amazon")
            button = next(c for c in walk([box]) if getattr(c, "text", "") == "处理并回填图片")
            with patch.object(app, "_confirm") as confirm, patch("threading.Thread.start") as start:
                button.on_click(None)
                app._running = True
                confirm.call_args.args[2]()
            start.assert_not_called()
            self.assertFalse(app._media_jobs)

    def test_switching_product_before_confirmation_does_not_start_paid_work(self):
        with sandbox():
            app = build_app()
            app.cfg.set("amazon_test_product_id", "old")
            box = controls(app, lambda: ["https://example.invalid/original.jpg"], Mock(), platform="amazon")
            button = next(c for c in walk([box]) if getattr(c, "text", "") == "处理并回填图片")
            with patch.object(app, "_confirm") as confirm, patch("threading.Thread.start") as start:
                button.on_click(None)
                app.cfg.set("amazon_test_product_id", "new")
                confirm.call_args.args[2]()
            start.assert_not_called()
            self.assertFalse(app._media_jobs)

    def test_changing_product_while_images_run_never_updates_the_next_product(self):
        with sandbox():
            app = build_app()
            app._switch_platform("amazon")
            app.cfg.set("amazon_test_product_id", "old")
            applied = Mock()
            box = controls(app, lambda: ["https://example.invalid/original.jpg"], applied, platform="amazon")
            button = next(c for c in walk([box]) if getattr(c, "text", "") == "处理并回填图片")
            pipeline = Mock()
            def generate(*args, **kwargs):
                app.cfg.set("amazon_test_product_id", "new")
                return ImagePipelineResult([ImageStep("AI", True, "https://example.invalid/temp.jpg")])
            pipeline.run.side_effect = generate
            pipeline._download.return_value = Path("/private/tmp/mock-image.jpg")
            button.on_click(None)
            with patch("aeclone.services.image_pipeline.ImagePipeline", return_value=pipeline), \
                 patch("aeclone.services.hosting.upload", return_value="https://example.invalid/stable.jpg"), \
                 patch("threading.Thread.start", lambda thread: thread.run()):
                app.page.dialog.actions[1].on_click(None)
            applied.assert_not_called()
            self.assertTrue(any("未回填" in str(c.value) for c in walk([box]) if isinstance(c, ft.Text)))

    def test_first_image_is_processed_once_and_only_replaced_after_hosting(self):
        with sandbox():
            app = build_app()
            applied = []
            urls = ["https://example.invalid/first.jpg", "https://example.invalid/second.jpg"]
            box = controls(app, lambda: list(urls), lambda value: applied.append(value), platform="alibaba")
            button = next(c for c in walk([box]) if getattr(c, "text", "") == "处理并回填图片")
            pipeline = Mock()
            pipeline.run.return_value = ImagePipelineResult([ImageStep("AI", True, "https://example.invalid/temp.jpg")])
            pipeline._download.return_value = Path("/private/tmp/mock-image.jpg")
            button.on_click(None)
            self.assertEqual(applied, [])
            with patch("aeclone.services.image_pipeline.ImagePipeline", return_value=pipeline), \
                 patch("aeclone.services.hosting.upload", return_value="https://example.invalid/stable.jpg"), \
                 patch("threading.Thread.start", lambda thread: thread.run()):
                app.page.dialog.actions[1].on_click(None)
            self.assertEqual(pipeline.run.call_args.args[0], urls[0])
            self.assertEqual(pipeline.run.call_count, 1)
            self.assertEqual(applied, [["https://example.invalid/stable.jpg", urls[1]]])

    def test_failed_processing_does_not_replace_current_images(self):
        with sandbox():
            app = build_app()
            applied = Mock()
            box = controls(app, lambda: ["https://example.invalid/original.jpg"], applied, platform="amazon")
            button = next(c for c in walk([box]) if getattr(c, "text", "") == "处理并回填图片")
            pipeline = Mock()
            pipeline.run.return_value = ImagePipelineResult([ImageStep("AI", False, error="quota")])
            button.on_click(None)
            with patch("aeclone.services.image_pipeline.ImagePipeline", return_value=pipeline), \
                 patch("aeclone.services.hosting.upload") as upload, \
                 patch("threading.Thread.start", lambda thread: thread.run()):
                app.page.dialog.actions[1].on_click(None)
            upload.assert_not_called()
            applied.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
