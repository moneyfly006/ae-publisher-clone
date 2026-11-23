"""Selective editing through platform forms and the native listing payloads."""
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import flet as ft

from aeclone.config import AppConfig
from aeclone.services import alibaba_payload, amazon_payload, shopee, tiktok_payload
from aeclone.services.collect_automation import process_images
from aeclone.services.image_pipeline import ImagePipelineResult, ImageStep
from aeclone.services.platform_media import batch_rules, read_groups, write_group
from aeclone.ui.alibaba_editor import panel, remember_product, current_overrides
from aeclone.ui.collect_workflow import SECTIONS
from ui_sandbox import build_app, sandbox, walk

A, B, C, D = (f"https://example.invalid/{ch}.jpg" for ch in "abcd")
HOSTED = "https://example.invalid/processed.jpg"


class SelectiveMediaTests(unittest.TestCase):
    def test_all_media_pages_expose_batch_rules_and_single_product_picker(self):
        with sandbox():
            app = build_app()
            for platform in ("alibaba", "amazon", "tiktok", "shopee"):
                with self.subTest(platform=platform):
                    app._switch_platform(platform)
                    page = next(number for number, section in SECTIONS[platform].items() if section == "media")
                    app._switch(page)
                    controls = list(walk(app.content.controls))
                    labels = {getattr(c, "label", None) for c in controls}
                    buttons = {getattr(c, "text", None) for c in controls}
                    self.assertIn("添加自动图片规则", buttons)
                    if platform == "alibaba":
                        self.assertNotIn("为勾选图片添加任务", buttons)
                        app.cfg.set("alibaba_test_product_id", "A")
                        remember_product(app, {"id": "A", "mainImage": A})
                        app._switch(12)
                        buttons = {getattr(c, "text", None) for c in walk(app.content.controls)}
                    self.assertIn("为勾选图片添加任务", buttons)
                    self.assertIn("处理并回填图片", buttons)
                    self.assertNotIn("处理第几张（如 1 或 1-3,5）", labels)

    def test_alibaba_checkboxes_build_editable_tasks_across_groups(self):
        with sandbox():
            app = build_app()
            app.cfg.set("alibaba_test_product_id", "7")
            remember_product(app, {"id": "7", "mainImage": f"{A},{B}", "detailImage": [{"url": C}, {"url": D}]})
            controls = [panel(app, "media")]

            def get(attr, value, kind=ft.Control):
                return next(c for c in walk(controls) if isinstance(c, kind) and getattr(c, attr, None) == value)

            def check(label):
                field = get("label", label, ft.Checkbox)
                field.value = True
                field.on_change(SimpleNamespace(control=field))
                get("text", "为勾选图片添加任务").on_click(None)

            check("第 2 张")
            group = get("label", "图片分组")
            group.value = "detail"
            group.on_change(None)
            check("第 2 张")
            check("第 1 张")
            tasks = [c for c in walk(controls) if getattr(c, "tooltip", "") == "删除图片任务"]
            self.assertEqual(len(tasks), 3)
            tasks[-1].on_click(None)
            prompt = [c for c in walk(controls) if getattr(c, "label", "") == "本任务提示词"][-1]
            prompt.value = "翻译详情图文字"
            prompt.on_change(SimpleNamespace(control=prompt))
            get("text", "处理并回填图片").on_click(None)
            pipeline = Mock()
            pipeline.run.return_value = ImagePipelineResult([ImageStep("AI", True, HOSTED)])
            with patch("aeclone.services.image_pipeline.ImagePipeline", return_value=pipeline), \
                 patch("aeclone.services.hosting.upload", return_value=HOSTED), \
                 patch("threading.Thread.start", lambda thread: thread.run()):
                app.page.dialog.actions[1].on_click(None)
            self.assertEqual({call.args[0] for call in pipeline.run.call_args_list}, {B, D})
            self.assertIn("翻译详情图文字", pipeline.run.call_args_list[1].kwargs["prompt"])
            self.assertEqual(current_overrides(app), {"main_images": [A, HOSTED], "detail_images": [C, HOSTED]})
            get("label", "新增图片 URL").value = B
            get("text", "添加图片").on_click(None)
            self.assertEqual(current_overrides(app)["detail_images"], [C, HOSTED, B])
            get("tooltip", "移除商品图片", ft.IconButton).on_click(None)
            self.assertEqual(current_overrides(app)["detail_images"], [HOSTED, B])

    def test_batch_rules_target_only_selected_groups_and_skip_missing_positions(self):
        product = {"id": "7", "mainImage": f"{A},{B}", "detailImage": [{"url": C}, {"url": D}]}
        client = SimpleNamespace(product_detail=lambda pid: {"product": product})
        rules = [{"group": "main", "position": "5", "operation": "white", "prompt": "主图"},
                 {"group": "detail", "position": "2", "operation": "translate", "prompt": "只翻译详情图"}]
        cfg = AppConfig({"alibaba_collect_image_rules": json.dumps(rules)})
        overrides = {}
        with patch("aeclone.services.platform_media.edit_and_host", return_value=HOSTED) as edit:
            process_images("alibaba", "7", cfg, client, overrides)
        self.assertEqual(overrides, {"detail_images": [C, HOSTED]})
        self.assertEqual(edit.call_args.args[1], D)
        self.assertIn("只翻译详情图", edit.call_args.args[2])
        payload = alibaba_payload.build_alibaba_save(product, cfg, overrides)
        self.assertEqual(payload["mainImage"], f"{A},{B}")
        self.assertEqual([r["url"] for r in json.loads(payload["detailImage"])], [C, HOSTED])

    def test_tiktok_and_shopee_keep_images_in_native_fields(self):
        desc = f'<p>Original facts</p><img src="{B}" onload="invalid()"><img src="{C}">'
        tt = {"id": "7", "mainImage": A, "description": desc, "sizeChart": D, "skuImgs": json.dumps({"red": C})}
        ov = {}
        write_group("tiktok", tt, ov, "detail", [HOSTED, C])
        write_group("tiktok", tt, ov, "size", [])
        write_group("tiktok", tt, ov, "sku", [HOSTED])
        pack = tiktok_payload.build_tiktok_payload({"product": tt}, overrides=ov)
        self.assertIn(f'<img src="{HOSTED}">', pack["description"])
        self.assertIn("Original facts", pack["description"])
        self.assertNotIn("onload", pack["description"])
        self.assertEqual(pack["sizeChart"], "")
        self.assertEqual(json.loads(pack["skuImgs"]), {"red": HOSTED})
        self.assertFalse(tiktok_payload._desc_tag_issues(pack["description"]))
        self.assertEqual(pack["mainImage"], A)

        sp = {"id": "8", "mainImage": f"{A};{B}", "descriptionInfoStr": desc, "descriptionType": "extended",
              "sizeChart": C, "videoThumbnail": B, "videoUrl": "https://example.invalid/video.mp4",
              "variations": [{"id": "v1", "variationSku": "One", "imageUrl": D}, {"id": "v2", "variationSku": "Two"}]}
        ov = {"variant_edits": {"v1": {"variationSku": "One-new"}}}
        self.assertEqual(read_groups("shopee", sp, ov, "site")["sku"], [D])
        write_group("shopee", sp, ov, "sku", [HOSTED], "site")
        write_group("shopee", sp, ov, "cover", [D], "site")
        pack = shopee.build_payload(sp, ov, mode="site")
        self.assertEqual(pack["videoThumbnail"], D)
        self.assertEqual(pack["videoUrl"], sp["videoUrl"])
        self.assertEqual(pack["variations"][0], {"id": "v1", "variationSku": "One-new", "imageUrl": HOSTED})
        self.assertEqual(pack["variations"][1], sp["variations"][1])

    def test_amazon_updates_only_gallery_and_rejects_inline_images(self):
        p = {"id": "77", "marketPlaceId": "A2EUQ1WTGCTBG2", "mainImage": A, "extraImagesStr": f"{B};{C}",
             "description": f'<p>Text</p><img src="{D}">',
             "productAttr": json.dumps({"main_product_image_locator": [{"media_location": A}],
                 "other_product_image_locator_1": [{"media_location": B}], "other_product_image_locator_2": [{"media_location": C}]})}
        ov = {}
        write_group("amazon", p, ov, "main", [A, HOSTED])
        self.assertNotIn("detail", read_groups("amazon", p, ov))
        with self.assertRaisesRegex(ValueError, "不支持"):
            write_group("amazon", p, ov, "detail", [C])
        payload = amazon_payload.build_amazon_payload(p, overrides=ov)
        attrs = json.loads(payload["productAttr"])
        self.assertEqual(attrs["other_product_image_locator_1"][0]["media_location"], HOSTED)
        self.assertNotIn("other_product_image_locator_2", attrs)
        self.assertEqual(payload["description"], p["description"])
        self.assertTrue(any("请将图片移到商品图片栏" in issue for issue in amazon_payload.validate_amazon_payload(payload)))

    def test_amazon_discards_only_legacy_description_image_rules(self):
        first = dict(group="detail", position="all", operation="white", prompt="legacy")
        second = dict(group="main", position="2", operation="white", prompt="keep")
        cfg = AppConfig({"amazon_collect_image_rules": json.dumps([first, second])})
        self.assertEqual(batch_rules(cfg, "amazon"), [second])

    def test_shopee_description_images_require_extended_description(self):
        old = {"id": "8", "description": f'<img src="{A}">'}
        self.assertFalse(read_groups("shopee", old, {})["detail"])
        with self.assertRaisesRegex(ValueError, "普通描述"):
            write_group("shopee", old, {}, "detail", [A])
        self.assertTrue(any("普通描述" in issue for issue in shopee.validate(old)))

        new = {"id": "8", "descriptionType": "extended", "descriptionInfoStr": f'<img src="{A}"><img src="{B}">'}
        issues = shopee.validate(new, limits={"descImageNumMax": 1})
        self.assertTrue(any("最多支持 1 张" in issue for issue in issues))
        self.assertFalse(any("描述需" in issue for issue in issues))

    def test_alibaba_generates_only_requested_text_field(self):
        with sandbox():
            app = build_app()
            app.cfg.set("alibaba_test_product_id", "7")
            remember_product(app, {"id": "7", "subject": "Old", "textDesc": "Keep", "keywords": "keep, old, steel"})
            with patch("aeclone.services.collect_ai._generate_field", return_value=("New title", [])) as generate, \
                 patch("threading.Thread.start", lambda thread: thread.run()):
                controls = list(walk([panel(app, "copy")]))
                title = next(c for c in controls if getattr(c, "text", "") == "AI 生成标题")
                title.on_click(None)
                app.page.dialog.actions[1].on_click(None)
            self.assertEqual(generate.call_args.args[-1], "title")
            self.assertEqual(current_overrides(app), {"subject": "New title"})
