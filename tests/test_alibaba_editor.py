"""当前商品编辑、阿里提示词隔离与过程日志。"""
from copy import deepcopy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import flet as ft
from ui_sandbox import sandbox, build_app, walk
from aeclone.config import AppConfig
from aeclone.services.alibaba_payload import build_alibaba_save
from aeclone.services.alibaba_pipeline import run_alibaba_product
from aeclone.ui.alibaba_editor import current_overrides, remember_product
from aeclone.services.progress import Event
from test_alibaba_pipeline import FakeClient, FakeTextPipeline, make_product, make_cfg


class EditorTests(unittest.TestCase):
    def test_native_trade_patch_preserves_other_fields_and_allows_explicit_edit(self):
        source = make_product()
        source["wholesaleTrade"].update(weight="5", custom="keep")
        saved = build_alibaba_save(source, make_cfg(), overrides={
            "wholesale_trade": {"weight": "8", "deliver_periods": [
                {"quantity": 1, "process_period": 5}, {"quantity": 10, "process_period": 12}]},
            "editor_fields": {"marketSample": "1", "marketSamplingQuantity": "2", "groupId": "123"}})
        trade = json.loads(saved["wholesaleTrade"])
        self.assertEqual(trade["weight"], "8")
        self.assertEqual(trade["custom"], "keep")
        self.assertEqual(len(trade["deliver_periods"]), 2)
        self.assertEqual(saved["marketSample"], "1")
        self.assertEqual(saved["groupId"], "123")
        self.assertEqual(source["wholesaleTrade"]["weight"], "5")

    def test_explicit_clear_does_not_restore_old_title_or_images(self):
        saved = build_alibaba_save(make_product(), make_cfg(), overrides={
            "subject": "", "text_desc": "", "main_images": [], "detail_images": []})
        self.assertEqual(saved["subject"], "")
        self.assertEqual(saved["textDesc"], "")
        self.assertEqual(saved["mainImage"], "")
        self.assertEqual(json.loads(saved["detailImage"]), [])

    def test_media_limit_is_not_silently_truncated(self):
        with self.assertRaisesRegex(ValueError, "最多 6"):
            build_alibaba_save(make_product(), make_cfg(), {"main_images": ["https://example.invalid/a"] * 7})

    def test_ai_local_config_and_custom_prompt_do_not_mutate_aliexpress(self):
        cfg = make_cfg()
        cfg.set("alibaba_title_prompt", "Alibaba custom title prompt")
        before = deepcopy(cfg.as_dict())
        fake = FakeTextPipeline()
        with patch("aeclone.services.text_pipeline.TextPipeline", return_value=fake) as factory:
            run_alibaba_product("123", cfg, dry_run=True, use_ai=True, client=FakeClient())
        local_cfg = factory.call_args.args[0]
        self.assertEqual(local_cfg.title_prompt, "Alibaba custom title prompt")
        self.assertEqual(cfg.as_dict(), before)

    def test_product_edits_are_separate_and_logs_persist_without_overview(self):
        with sandbox():
            app = build_app()
            app.cfg.set("alibaba_test_product_id", "A")
            remember_product(app, dict(make_product(), id="A"))
            app._switch(12)
            field = next(c for c in walk(app.content.controls) if isinstance(c, ft.TextField)
                         and c.label == "商品标题（最多 128 字符）")
            field.value = "Edited title A"
            field.on_change(SimpleNamespace(control=field))
            app._switch(32)
            self.assertEqual(current_overrides(app)["subject"], "Edited title A")
            app.cfg.set("alibaba_test_product_id", "B")
            self.assertNotIn("subject", current_overrides(app))
            app.cfg.set("alibaba_test_product_id", "A")
            self.assertEqual(current_overrides(app)["subject"], "Edited title A")
            app.ov_progress = None
            event = Event(ts=1, stage="Alibaba · 校验", action="完成", product="A")
            app._on_progress(event)
            self.assertIn("Alibaba · 校验", app.log_rows[-1][2])
            self.assertIn("Alibaba · 校验", app._log_file().read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
