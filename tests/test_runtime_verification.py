"""Exercise actual local outputs through the UI, with no paid or shop operations."""
from pathlib import Path
from types import SimpleNamespace as NS
import json
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from ui_sandbox import build_app, sandbox, walk
import flet as ft
from PIL import Image, ImageChops
from aeclone.ui import app as ui
from aeclone.services.product_data import hydrate_product
from aeclone.services.api_save import match_processed_skus


class RuntimeVerification(unittest.TestCase):
    def test_blank_image_range_includes_every_image(self):
        from aeclone.services.image_index import pick
        self.assertEqual(pick(["first", "second", "last"], ""), ["first", "second", "last"])

    def test_image_operations_respect_independent_ranges(self):
        from aeclone.config import AppConfig
        from aeclone.services.publisher import Publisher, PublishResult
        with sandbox():
            cfg = AppConfig(dict(white_bg_enable=True, white_background_pic_1="1",
                                 white_background_pic_2="2", watermark_enable=True,
                                 remove_watermark_pic_1="2", remove_watermark_pic_2="3",
                                 translate_main_pic_enable=True, translate_main_pic_indices="4",
                                 image_edit_mode="fine", image_ai_jobs="[]", image_edit_plans="[]"))
            product = {"images": ["one", "two", "three", "four"]}
            pub = Publisher.__new__(Publisher)
            pub.cfg = cfg
            pub._img_service = Mock()
            pub._pmap = lambda fn, rows: [fn(row) for row in rows]
            pub._apply_simple_ops = Mock(side_effect=lambda url, ops: "edited-" + url)
            pub._vet = lambda new, old: new
            pub._apply_image_jobs(product, PublishResult("qa", True))
            calls = {c.args[0]: c.args[1] for c in pub._apply_simple_ops.call_args_list}
            self.assertEqual(calls, {"one": ["white"], "two": ["white", "watermark"],
                                     "three": ["watermark"], "four": ["translate"]})
            self.assertEqual(len(pub._apply_simple_ops.call_args_list), 4)

    def test_workbench_local_watermark_outputs_selected_image_and_keeps_comparison(self):
        with sandbox(threads=True), tempfile.TemporaryDirectory() as root:
            root = Path(root)
            images = [root / f"source-{i}.png" for i in range(3)]
            for path in images:
                Image.new("RGB", (256, 256), "black").save(path)
            wm = root / "mark.png"
            Image.new("RGBA", (32, 32), (255, 0, 0, 255)).save(wm)
            app = build_app()
            app._switch(9)
            app.cfg.watermark = str(wm)
            app.cfg.overlay_position = "top-left"
            app.cfg.overlay_opacity = 1
            app.picked = images
            app.wb_sel_paths = {str(p) for p in images}
            app.wb_index.value = "2"
            with patch.object(ui, "ImageService", side_effect=AssertionError("local task used AI")):
                app._wb_process("add_watermark")
            self.assertIn("成功 1/1", app.wb_status.value)
            self.assertEqual(app._wb_before, {0: images[1]})
            result = Path(app.wb_results.controls[0].controls[1].value)
            self.assertTrue(result.is_file())
            with Image.open(result) as output:
                self.assertEqual(output.getpixel((25, 25)), (255, 0, 0))
                self.assertEqual(output.getpixel((150, 150)), (0, 0, 0))
            self.assertEqual(Image.open(images[1]).getpixel((25, 25)), (0, 0, 0))
            compare = app.wb_results.controls[0].controls[2]
            compare.on_click(NS(control=compare))
            self.assertTrue(app.page.dialog.open)
            self.assertEqual(app.page.dialog.title.value, "处理前后对比")

    def test_invalid_image_indices_do_not_run_jobs(self):
        with sandbox():
            app = build_app()
            app._switch(9)
            app.picked = [Path("one.png")]
            app.wb_sel_paths = {"one.png"}
            for value in ("0", "-1", "abc", "2"):
                app.wb_index.value = value
                with patch.object(ui, "ImageService"), patch.object(ui, "BatchRunner") as runner:
                    app._wb_process("white")
                    runner.return_value.run.assert_not_called()
                self.assertFalse(app.wb_busy.visible)

    def test_watermark_picker_saves_path_and_preserves_page(self):
        with sandbox(), tempfile.TemporaryDirectory() as root:
            wm = Path(root) / "watermark.png"
            Image.new("RGBA", (20, 20), "red").save(wm)
            app = build_app()
            app._switch_platform("aliexpress")
            app._switch(5)
            app._on_watermark_file(NS(files=[NS(path=str(wm))]))
            self.assertEqual(app._current, 5)
            self.assertEqual(app.wm_preview.src, str(wm))
            self.assertTrue(app.wm_preview.visible)
            from aeclone.config import AppConfig
            self.assertEqual(json.loads(AppConfig.path().read_text())["watermark"], str(wm))

    def test_result_upload_includes_local_outputs_and_remote_urls(self):
        with sandbox(), tempfile.TemporaryDirectory() as root:
            local = Path(root) / "local.png"
            Image.new("RGB", (10, 10), "red").save(local)
            app = build_app()
            app._switch(9)
            app.wb_results.controls = [ft.Row([ft.Icon(), ft.Text(value)]) for value in
                                      (str(local), "https://example.invalid/result.png", "failed")]
            with patch.object(ui, "DianxiaomiClient") as client, \
                 patch("aeclone.integrations.cos_upload.upload_image") as upload:
                app._cookies = lambda: {}
                app._upload_results()
                upload.assert_called_once_with(client.return_value, local)
                client.return_value.upload_image_from_url.assert_called_once_with(
                    "https://example.invalid/result.png")
            self.assertIn("2/2", app.page.snack_bar.content.value)

    def test_ai_headers_have_consistent_unoutlined_restore_action(self):
        with sandbox():
            app = build_app()
            app._switch_platform("aliexpress")
            for index in (3, 4, 5):
                app._switch(index)
                restore = [c for c in walk(app.content.controls)
                           if getattr(c, "text", "") == "恢复默认"]
                self.assertEqual(len(restore), 1)
                self.assertIsInstance(restore[0], ft.TextButton)

    def test_qa_instance_blocks_direct_publish_entries(self):
        import os
        with sandbox(), patch.dict(os.environ, {"AURORA_NO_SAVE":"1"}):
            app=build_app()
            with patch.object(app,"_cookies",side_effect=AssertionError("preview accessed shop")):
                app._batch_publish("waitPublish","批量发布")
                self.assertIn("未提交真实批量发布",app.page.snack_bar.content.value)
                with self.assertRaisesRegex(RuntimeError,"未执行真实定时发布"):
                    app._run_scheduled(NS(product_id="qa",title="Test"))

    def test_detail_hydration_preserves_source_identity_and_variant_data(self):
        product = {"id": "qa", "images": []}
        source = [{"id": "a", "skuCode": "RED", "ipmSkuStock": 3, "skuPrice": "9"},
                  {"id": "b", "skuCode": "BLUE", "ipmSkuStock": 8, "skuPrice": "12"}]
        detail = {"subject": "Test", "imageURLs": "one;two",
                  "detail": '<img src="detail-image">', "aeopAeProductSKUs": json.dumps(source)}
        hydrated = hydrate_product(product, detail)
        self.assertEqual(hydrated["images"], ["one", "two"])
        self.assertEqual(hydrated["detail_images"], ["detail-image"])
        self.assertEqual([row["stock"] for row in hydrated["skus"]], [3, 8])
        matched = match_processed_skus(source, list(reversed(hydrated["skus"])))
        self.assertEqual([row["_source_id"] for row in matched], ["a", "b"])
        hydrated["raw"]["subject"] = "Changed"
        self.assertEqual(detail["subject"], "Test")
        self.assertEqual(product, {"id": "qa", "images": []})


if __name__ == "__main__":
    unittest.main(verbosity=2)
