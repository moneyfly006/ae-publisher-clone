"""Amazon 专业 AI 提示词预设库离线单测（不联网、不真实写）。

跑法：``.venv/bin/python tests/test_amazon_presets.py``
覆盖：六类预设数量/字段/合规、占位符 render、apply/resolve/reset、
SKU prompt 与 parse_sku 边界、ai_generate_field("sku")。
"""
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.config import AppConfig
from aeclone.services import amazon_presets as AP
from aeclone.services import amazon_prompts as PR
from aeclone.services import amazon_pipeline as PL

# 预设 prompt 里不得出现的字面量（image 的「主图不新增文字/水印」说明除外，
# 这里扫描的是 SEO 违规/联系方式/站外链接等词）
BANNED_TOKENS = (
    "best", "100%", "free shipping", "sale", "guarantee",
    "电话", "http", "www.", "email", "whatsapp", "wechat", "@",
)

PRODUCT = {
    "id": "220114595363182310",
    "title": "5 Ton Diesel Wheeled Backhoe Loader 4x4 HW20-28 Heavy Duty",
    "sku": "WY-QHCA-LTM-1",
    "brand": "Generic",
    "manufacturer": "Generic",
    "description": "<p>Heavy duty backhoe loader.</p>",
    "productAttr": "{}",
}


class PresetCatalogTests(unittest.TestCase):
    def test_kinds_and_counts(self):
        self.assertEqual(AP.PRESET_VERSION[:4], "2026")
        self.assertEqual(AP.list_kinds(), list(AP.PRESET_KINDS))
        self.assertEqual(AP.PRESET_KINDS,
                         ("title", "highlight", "bullets", "description",
                          "search_terms", "sku", "image"))
        expected = {"title": 10, "highlight": 5, "bullets": 7, "description": 7,
                    "search_terms": 6, "sku": 6, "image": 15}
        for kind in AP.PRESET_KINDS:
            presets = AP.PRESETS[kind]
            self.assertEqual(len(presets), expected[kind], kind)
            self.assertGreaterEqual(len(presets), 5, kind)
            self.assertLessEqual(len(presets), 30, kind)
            self.assertEqual(len(set(presets)), len(presets), kind)
        self.assertEqual(sum(expected.values()), 56)

    def test_preset_fields(self):
        for kind in AP.PRESET_KINDS:
            for key, item in AP.PRESETS[kind].items():
                self.assertTrue(key, kind)
                self.assertIn("name", item)
                self.assertIn("desc", item)
                self.assertIn("tags", item)
                self.assertIn("prompt", item)
                self.assertTrue(str(item["name"]).strip(), f"{kind}.{key}.name")
                self.assertTrue(str(item["desc"]).strip(), f"{kind}.{key}.desc")
                self.assertIsInstance(item["tags"], list)
                self.assertTrue(item["tags"], f"{kind}.{key}.tags")
                self.assertTrue(all(str(t).strip() for t in item["tags"]))
                prompt = str(item["prompt"])
                self.assertGreaterEqual(len(prompt), 100, f"{kind}.{key} 太短")
                self.assertLessEqual(len(prompt), 4000, f"{kind}.{key} 太长")

    def test_prompts_are_compliant(self):
        for kind in AP.PRESET_KINDS:
            for key, item in AP.PRESETS[kind].items():
                low = str(item["prompt"]).lower()
                for token in BANNED_TOKENS:
                    self.assertNotIn(token, low, f"{kind}.{key} 含禁止词 {token!r}")

    def test_prompts_only_use_known_placeholders(self):
        used = set()
        for kind in AP.PRESET_KINDS:
            for key, item in AP.PRESETS[kind].items():
                for ph in re.findall(r"\{([a-zA-Z_]+)\}", item["prompt"]):
                    used.add(ph)
                    self.assertIn(ph, AP.PLACEHOLDERS,
                                  f"{kind}.{key} 未知占位符 {ph}")
        # 至少覆盖文本类关键占位符与 image 的 title/subject/language
        for ph in ("title_limit", "count", "bullet_suggest", "desc_bytes",
                   "st_bytes", "country", "code", "language", "title", "subject"):
            self.assertIn(ph, used)

    def test_kind_specific_constraints(self):
        for key, item in AP.PRESETS["title"].items():
            prompt = item["prompt"]
            if key == "short_title":
                self.assertIn("80", prompt, key)
                self.assertNotIn("{title_limit}", prompt, key)
            else:
                self.assertIn("{title_limit}", prompt, f"title.{key}")
            self.assertIn("行业要点", prompt, f"title.{key}")
            self.assertIn("示例仅示意结构", prompt, f"title.{key}")
            self.assertGreaterEqual(prompt.count("\n- "), 2, f"title.{key} 行业要点<2")
        for key, item in AP.PRESETS["bullets"].items():
            self.assertIn("{count}", item["prompt"], key)
            self.assertIn("{bullet_suggest}", item["prompt"], key)
        for key, item in AP.PRESETS["description"].items():
            self.assertIn("{desc_bytes}", item["prompt"], key)
            self.assertIn("<p>", item["prompt"], key)
        for key, item in AP.PRESETS["search_terms"].items():
            self.assertIn("{st_bytes}", item["prompt"], key)
        for key, item in AP.PRESETS["sku"].items():
            self.assertIn("40", item["prompt"], key)
        for key, item in AP.PRESETS["image"].items():
            prompt = item["prompt"]
            for ph in ("{title}", "{subject}", "{language}"):
                self.assertIn(ph, prompt, f"image.{key} 缺 {ph}")
            self.assertIn("形状", prompt, f"image.{key} 缺「不改本体」约束")
            self.assertIn("水印", prompt, f"image.{key} 缺「不新增水印」约束")

    def test_new_title_presets(self):
        for key in ("short_title", "spec_model_first"):
            self.assertIn(key, AP.PRESETS["title"])
        self.assertIn("80", AP.PRESETS["title"]["short_title"]["prompt"])
        self.assertIn("型号", AP.PRESETS["title"]["spec_model_first"]["name"])
        # 每个 title 预设的示例都是英文示意且互不相同
        examples = set()
        import re as _re
        for key, item in AP.PRESETS["title"].items():
            match = _re.search(r"`([^`]+)`", item["prompt"])
            self.assertIsNotNone(match, key)
            examples.add(match.group(1))
        self.assertEqual(len(examples), len(AP.PRESETS["title"]))

    def test_new_bullets_description_image_presets(self):
        self.assertIn("compliance_safe", AP.PRESETS["bullets"])
        self.assertIn("合规", AP.PRESETS["bullets"]["compliance_safe"]["name"])
        self.assertIn("comparison", AP.PRESETS["description"])
        self.assertIn("对比", AP.PRESETS["description"]["comparison"]["name"])
        for key in ("packaging_pallet", "detail_collage"):
            self.assertIn(key, AP.PRESETS["image"])
            prompt = AP.PRESETS["image"][key]["prompt"]
            self.assertIn("副图", prompt)
            self.assertIn("形状", prompt)

    def test_image_count_and_topics(self):
        keys = list(AP.PRESETS["image"])
        self.assertGreaterEqual(len(keys), 10)
        names = " ".join(AP.PRESETS["image"][k]["name"] for k in keys)
        for topic in ("主图", "场景", "细节", "尺寸", "配件", "水印", "翻译",
                      "修复", "A+", "包装", "拼合"):
            self.assertIn(topic, names)


class ApiTests(unittest.TestCase):
    def test_list_and_options_are_stable(self):
        for kind in AP.PRESET_KINDS:
            rows = AP.list_presets(kind)
            self.assertEqual([r["key"] for r in rows], list(AP.PRESETS[kind]))
            options = AP.preset_options(kind)
            self.assertEqual(options, [(r["key"], r["name"]) for r in rows])
            self.assertEqual(AP.default_preset(kind), options[0][0])
        self.assertEqual(AP.list_presets("unknown"), [])
        self.assertEqual(AP.preset_options("unknown"), [])
        self.assertEqual(AP.default_preset("unknown"), "")

    def test_get_preset_returns_copy(self):
        preset = AP.get_preset("title", AP.default_preset("title"))
        self.assertIsNotNone(preset)
        self.assertEqual(preset["kind"], "title")
        preset["prompt"] = "MUTATED"
        preset["tags"].append("mutated")
        fresh = AP.get_preset("title", AP.default_preset("title"))
        self.assertNotEqual(fresh["prompt"], "MUTATED")
        self.assertNotIn("mutated", fresh["tags"])
        self.assertIsNone(AP.get_preset("title", "not-exist"))
        self.assertIsNone(AP.get_preset("unknown", "x"))

    def test_render(self):
        mapping = {"title_limit": 200, "country": "加拿大", "language": "英语",
                   "title": "HW20-28 Backhoe"}
        out = AP.render("≤{title_limit} 字符 · {country} · {language} · {title} "
                        "· {unknown}", mapping)
        self.assertEqual(out, "≤200 字符 · 加拿大 · 英语 · HW20-28 Backhoe · {unknown}")
        self.assertEqual(AP.render("{title}", {}), "{title}")
        self.assertEqual(AP.render("", {"title": "X"}), "")
        self.assertEqual(AP.render(None, None), "")


class ApplyResetTests(unittest.TestCase):
    def test_apply_to_cfg_writes_prompt_and_marker(self):
        cfg = AppConfig()
        for kind in AP.PRESET_KINDS:
            key = list(AP.PRESETS[kind])[1]
            prompt = AP.apply_to_cfg(cfg, kind, key)
            self.assertEqual(prompt, AP.PRESETS[kind][key]["prompt"])
            self.assertEqual(getattr(cfg, AP.PROMPT_CFG_KEYS[kind]), prompt)
            self.assertEqual(getattr(cfg, AP.SELECTED_CFG_KEYS[kind]), key)
            self.assertEqual(cfg.amazon_presets_version, AP.PRESET_VERSION)
            self.assertEqual(AP.resolve_selected(cfg, kind), key)

    def test_apply_unknown_raises(self):
        cfg = AppConfig()
        with self.assertRaises(ValueError):
            AP.apply_to_cfg(cfg, "title", "nope")
        with self.assertRaises(ValueError):
            AP.apply_to_cfg(cfg, "unknown", "x")

    def test_resolve_selected_falls_back(self):
        cfg = AppConfig()
        self.assertEqual(AP.resolve_selected(cfg, "title"),
                         AP.default_preset("title"))
        cfg.set(AP.SELECTED_CFG_KEYS["title"], "bogus")
        self.assertEqual(AP.resolve_selected(cfg, "title"),
                         AP.default_preset("title"))
        self.assertEqual(AP.resolve_selected(cfg, "unknown"), "")

    def test_reset_to_default(self):
        cfg = AppConfig()
        second = list(AP.PRESETS["bullets"])[2]
        AP.apply_to_cfg(cfg, "bullets", second)
        key = AP.reset_to_default(cfg, "bullets")
        self.assertEqual(key, AP.default_preset("bullets"))
        self.assertEqual(cfg.amazon_bullet_preset, key)
        self.assertEqual(cfg.amazon_bullet_prompt,
                         AP.PRESETS["bullets"][key]["prompt"])
        self.assertEqual(AP.reset_to_default(cfg, "unknown"), "")

    def test_cfg_defaults_present(self):
        cfg = AppConfig()
        for key in ("amazon_sku_prompt", "amazon_image_prompt",
                    "amazon_title_preset", "amazon_bullet_preset",
                    "amazon_description_preset", "amazon_search_terms_preset",
                    "amazon_sku_preset", "amazon_image_preset",
                    "amazon_presets_version"):
            self.assertTrue(hasattr(cfg, key), key)

    def test_mapping_tables_cover_all_kinds(self):
        self.assertEqual(set(AP.PROMPT_CFG_KEYS), set(AP.PRESET_KINDS))
        self.assertEqual(set(AP.SELECTED_CFG_KEYS), set(AP.PRESET_KINDS))
        self.assertEqual(AP.PROMPT_CFG_KEYS["bullets"], "amazon_bullet_prompt")
        self.assertEqual(AP.SELECTED_CFG_KEYS["search_terms"],
                         "amazon_search_terms_preset")


class SkuPromptTests(unittest.TestCase):
    def test_build_sku_prompt(self):
        text = PR.build_sku_prompt(PRODUCT, None, "A2EUQ1WTGCTBG2")
        self.assertIn("40", text)
        self.assertIn("只允许英文字母", text)
        self.assertIn(PRODUCT["title"][:20], text)
        self.assertIn(PRODUCT["sku"], text)
        self.assertIn("加拿大", text)

    def test_build_sku_prompt_uses_cfg_override(self):
        cfg = {"amazon_sku_prompt": "CUSTOM SKU RULE {code}"}
        text = PR.build_sku_prompt(PRODUCT, cfg, "ATVPDKIKX0DER")
        self.assertIn("CUSTOM SKU RULE US", text)
        self.assertNotIn("卖家 SKU 命名专家", text)

    def test_parse_sku_edges(self):
        self.assertEqual(PR.parse_sku("HENGWANG-HW20-28-CA-4X4"),
                         "HENGWANG-HW20-28-CA-4X4")
        self.assertEqual(PR.parse_sku("SKU: WY QHCA/LTM 1!!!"),
                         "WY-QHCA-LTM-1")
        self.assertEqual(PR.parse_sku("```\nHWG-HW20-28\n```"), "HWG-HW20-28")
        self.assertEqual(PR.parse_sku("型号：反铲装载机"), "")
        self.assertEqual(PR.parse_sku(""), "")
        self.assertEqual(PR.clean_sku(None), "")
        long = "A" * 60
        out = PR.parse_sku(long)
        self.assertLessEqual(len(out), 40)
        self.assertRegex(out, r"^[A-Za-z0-9_-]+$")

    def test_clean_sku_allowed_chars(self):
        for text in ("abc def", "ABC@#$123", "中文ABC_123", "A--B__C",
                     "  leading/trailing  "):
            out = PR.clean_sku(text)
            self.assertRegex(out, r"^[A-Za-z0-9_-]{1,40}$")
            self.assertNotIn(" ", out)
            self.assertNotIn("中文", out)
        self.assertEqual(PR.clean_sku("中文ABC_123"), "ABC_123")


class AiSkuFieldTests(unittest.TestCase):
    class FakeApi:
        def __init__(self, answer="SKU: HENGWANG HW20-28 CA 4x4!!!"):
            self.answer = answer
            self.calls = []

        def chat(self, model, messages, max_tokens=2048):
            self.calls.append(messages[0]["content"])
            return self.answer

    def test_ai_generate_field_sku(self):
        fake = self.FakeApi()
        with patch.object(PL, "_ai_client", return_value=fake):
            value, notes = PL.ai_generate_field(
                PRODUCT, AppConfig(), "sku", "A2EUQ1WTGCTBG2")
        self.assertEqual(value, "HENGWANG-HW20-28-CA-4x4")
        self.assertRegex(value, r"^[A-Za-z0-9_-]{1,40}$")
        self.assertTrue(notes)
        self.assertIn("已生成", "".join(notes))
        self.assertTrue(fake.calls)
        self.assertIn("40", fake.calls[0])

    def test_ai_generate_field_sku_failure(self):
        class Boom:
            def chat(self, *a, **kw):
                raise RuntimeError("模型超时")
        with patch.object(PL, "_ai_client", return_value=Boom()):
            value, notes = PL.ai_generate_field(PRODUCT, AppConfig(), "sku",
                                                "A2EUQ1WTGCTBG2")
        self.assertEqual(value, "")
        self.assertTrue(any("失败" in n for n in notes))

    def test_ai_generate_all_stays_four_fields(self):
        self.assertNotIn("sku", PL.AI_FIELDS)
        self.assertIn("sku", PL.AI_EXTRA_FIELDS)

    def test_unknown_field_message_lists_sku(self):
        value, notes = PL.ai_generate_field(PRODUCT, AppConfig(), "bogus")
        self.assertIsNone(value)
        self.assertIn("sku", " ".join(notes))


if __name__ == "__main__":
    unittest.main(verbosity=2)
