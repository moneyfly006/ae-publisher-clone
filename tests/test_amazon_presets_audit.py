"""Amazon AI 提示词预设库独立审计测试（task-14；离线/沙箱，不联网不写）。"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("AURORA_TEST", "1")

from aeclone.services import amazon_presets as AP  # noqa: E402
from aeclone.services import amazon_prompts as PROM  # noqa: E402


def _audit_module():
    spec = importlib.util.spec_from_file_location(
        "verify_amazon_presets", ROOT / "tools" / "verify_amazon_presets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUDIT = _audit_module()


def _cfg():
    data = {}
    for kind in AP.PRESET_KINDS:
        data[AP.PROMPT_CFG_KEYS[kind]] = ""
        data[AP.SELECTED_CFG_KEYS[kind]] = ""
    return SimpleNamespace(**data)


class ContentAuditTests(unittest.TestCase):
    def test_counts(self):
        expected = {"title": 10, "bullets": 7, "description": 7,
                    "search_terms": 6, "sku": 6, "image": 14}
        actual = {kind: len(AP.PRESETS[kind]) for kind in AP.PRESET_KINDS}
        self.assertEqual(actual, expected)
        self.assertEqual(sum(actual.values()), 50)

    def test_names_desc_prompt_quality(self):
        for kind, presets in AP.PRESETS.items():
            for key, item in presets.items():
                self.assertGreaterEqual(len(str(item.get("name") or "")), 2, key)
                self.assertGreaterEqual(len(str(item.get("desc") or "")), 6, key)
                self.assertGreaterEqual(len(str(item.get("prompt") or "")), 120, key)

    def test_placeholders_whitelist_and_unknown_preserved(self):
        for kind, presets in AP.PRESETS.items():
            for key, item in presets.items():
                for placeholder in re.findall(r"\{([a-z_]+)\}", item["prompt"]):
                    self.assertIn(placeholder, AUDIT.PLACEHOLDER_WHITELIST, key)
        rendered = AP.render("Hi {country} {unknown_ph}", {"country": "美国"})
        self.assertIn("{unknown_ph}", rendered)

    def test_compliance_scan_allow_negation(self):
        self.assertEqual(AUDIT.scan_banned_terms("禁止 best、100%、free shipping"), [])
        self.assertTrue(AUDIT.scan_banned_terms("Best product 100% free shipping"))
        for kind, presets in AP.PRESETS.items():
            if kind == "image":
                continue
            for key, item in presets.items():
                self.assertEqual(AUDIT.scan_banned_terms(item["prompt"]), [], key)

    def test_sku_and_image_constraints(self):
        for key, item in AP.PRESETS["sku"].items():
            prompt = item["prompt"]
            for needle in ("40", "字母", "空格", "只输出"):
                self.assertIn(needle, prompt, key)
        main = AP.PRESETS["image"]["white_bg_main"]["prompt"]
        for needle in ("纯白", "文字", "水印", "产品本体", "Logo"):
            self.assertIn(needle, main)


class FunctionalTests(unittest.TestCase):
    def test_apply_resolve_reset(self):
        cfg = _cfg()
        for kind in AP.PRESET_KINDS:
            key = AP.preset_options(kind)[0][0]
            prompt = AP.apply_to_cfg(cfg, kind, key)
            self.assertEqual(getattr(cfg, AP.PROMPT_CFG_KEYS[kind]), prompt)
            self.assertEqual(AP.resolve_selected(cfg, kind), key)
            default_key = AP.reset_to_default(cfg, kind)
            self.assertEqual(getattr(cfg, AP.SELECTED_CFG_KEYS[kind]), default_key)
        self.assertEqual(cfg.amazon_presets_version, AP.PRESET_VERSION)

    def test_builders_use_preset(self):
        cfg = _cfg()
        product = {"title": "5 Ton Backhoe HW20-28", "sku": "SKU-1",
                   "brand": "Generic", "manufacturer": "Generic",
                   "marketPlaceId": "A2EUQ1WTGCTBG2"}
        builders = {"title": PROM.build_title_prompt,
                    "bullets": PROM.build_bullets_prompt,
                    "description": PROM.build_description_prompt,
                    "search_terms": PROM.build_search_terms_prompt,
                    "sku": PROM.build_sku_prompt}
        for kind, builder in builders.items():
            key = AP.default_preset(kind)
            AP.apply_to_cfg(cfg, kind, key)
            text = builder(product, cfg, "A2EUQ1WTGCTBG2")
            distinctive = AP.PRESETS[kind][key]["prompt"].split("{")[0].strip()[:10]
            self.assertIn(distinctive, text, kind)

    def test_ai_generate_sku_with_fake_api(self):
        from aeclone.services import amazon_pipeline as PL

        class FakeApi:
            def chat(self, model, messages, max_tokens=120):
                return "Seller SKU: hw20 28/CA_01"

        cfg = _cfg()
        with patch.object(PL, "_ai_client", lambda c: FakeApi()), \
             patch.object(PL, "_ai_model", lambda c: "fake"):
            value, notes = PL.ai_generate_field(
                {"title": "T", "marketPlaceId": "A2EUQ1WTGCTBG2"}, cfg, "sku",
                marketplace_id="A2EUQ1WTGCTBG2")
        self.assertRegex(str(value), r"^[A-Za-z0-9_-]{1,40}$")
        self.assertTrue(notes)

    def test_parse_sku_boundaries(self):
        self.assertEqual(PROM.parse_sku(""), "")
        self.assertLessEqual(len(PROM.parse_sku("A" * 60)), 40)
        for raw in ("```\nSKU: ab 12/中文_3\n```", "ab@cd 中文!", "  x y  "):
            value = PROM.parse_sku(raw)
            self.assertRegex(value, r"^[A-Za-z0-9_-]{0,40}$", raw)


class UiAndIsolationTests(unittest.TestCase):
    def test_ui_controls(self):
        from ui_sandbox import build_app, sandbox
        with sandbox():
            app = build_app()
            app._switch_platform("amazon")
            app._switch(13)
            widgets = getattr(app, "_amz_preset_widgets", {}) or {}
        self.assertEqual(set(widgets), set(AP.PRESET_KINDS))
        for kind, widget in widgets.items():
            self.assertEqual(len(widget["dd"].options), len(AP.PRESETS[kind]), kind)
            self.assertTrue(callable(getattr(widget["tf"], "on_change", None)))

    def test_cfg_isolation(self):
        cfg = _cfg()
        cfg.alibaba_title_prompt = "ALI"
        cfg.ali_prompt = "ALIEXPRESS"
        for kind in AP.PRESET_KINDS:
            AP.apply_to_cfg(cfg, kind, AP.default_preset(kind))
        self.assertEqual(cfg.alibaba_title_prompt, "ALI")
        self.assertEqual(cfg.ali_prompt, "ALIEXPRESS")
        self.assertTrue(all(hasattr(cfg, AP.PROMPT_CFG_KEYS[k]) for k in AP.PRESET_KINDS))


if __name__ == "__main__":
    unittest.main(verbosity=2)
