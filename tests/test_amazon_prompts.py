"""Amazon 提示词/解析/清洗离线单测，不联网。

跑法：``.venv/bin/python tests/test_amazon_prompts.py``
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.services.amazon_prompts import (
    BULLET_DEFAULT_COUNT, DESCRIPTION_MAX_BYTES, SITE_META, TITLE_MAX,
    build_bullets_prompt, build_description_prompt, build_search_terms_prompt,
    build_title_prompt, clean_bullet, clean_for_site, clean_title, has_chinese,
    is_chinese_site, language_for_site, parse_bullets, parse_search_terms,
    remove_emoji, sanitize_description, site_meta, truncate_bytes, truncate_words,
)

SAMPLE_PRODUCT = {
    "id": "220114595363182310",
    "title": "5 Ton Diesel Wheeled Backhoe Loader 4x4 Heavy Duty",
    "sku": "WY-QHCA-LTM-1",
    "brand": "Generic",
    "manufacturer": "Generic",
    "categoryBrowsePath": "6299024011/6256963011/6256963011/13553005011",
    "bulletPoint": "Color:Blue±Power Type:Diesel",
    "description": "<p>Heavy duty backhoe loader for construction.</p>",
    "productAttr": '{"power_source_type":[{"value":"Diesel"}]}',
    "searchTerms": "",
    "sourceUrl": "https://www.alibaba.com/product-detail/x_1.html",
}


class SiteMetaTests(unittest.TestCase):
    def test_site_meta_table(self):
        self.assertGreaterEqual(len(SITE_META), 20)
        for mp in ("ATVPDKIKX0DER", "A2EUQ1WTGCTBG2", "A1AM78C64UM0Y8",
                   "A2Q3Y263D00KWC", "AAHKV2X7AFYLW"):
            self.assertIn(mp, SITE_META)
        self.assertEqual(SITE_META["A2EUQ1WTGCTBG2"]["code"], "CA")
        self.assertEqual(SITE_META["A2EUQ1WTGCTBG2"]["currency"], "CAD")
        self.assertEqual(SITE_META["AAHKV2X7AFYLW"]["lang"], "zh")

    def test_language_for_site(self):
        self.assertEqual(language_for_site("ATVPDKIKX0DER"), "en")
        self.assertEqual(language_for_site("A1AM78C64UM0Y8"), "es")
        self.assertEqual(language_for_site("A2Q3Y263D00KWC"), "pt")
        self.assertEqual(language_for_site("A1VC38T7YXB528"), "ja")
        self.assertEqual(language_for_site("UNKNOWN_MP"), "en")
        self.assertTrue(is_chinese_site("AAHKV2X7AFYLW"))
        self.assertFalse(is_chinese_site("ATVPDKIKX0DER"))

    def test_site_meta_copy_has_marketplace_id(self):
        meta = site_meta("A2EUQ1WTGCTBG2")
        self.assertEqual(meta["marketplace_id"], "A2EUQ1WTGCTBG2")
        meta["code"] = "XX"
        # 修改副本不会污染 SITE_META
        self.assertEqual(SITE_META["A2EUQ1WTGCTBG2"]["code"], "CA")


class PromptBuilderTests(unittest.TestCase):
    def test_title_prompt(self):
        text = build_title_prompt(SAMPLE_PRODUCT, None, "A2EUQ1WTGCTBG2")
        self.assertIn("加拿大", text)
        self.assertIn("CA", text)
        self.assertIn("英语", text)
        self.assertIn(str(TITLE_MAX), text)          # 200 硬上限
        self.assertIn(SAMPLE_PRODUCT["title"], text)
        self.assertIn("SKU", text)

    def test_bullets_prompt(self):
        text = build_bullets_prompt(SAMPLE_PRODUCT, None, "A2EUQ1WTGCTBG2", count=5)
        self.assertIn("恰好输出 5 条", text)
        self.assertIn("700", text)                   # schema 硬上限
        self.assertIn("JSON", text)
        text3 = build_bullets_prompt(SAMPLE_PRODUCT, None, "A1AM78C64UM0Y8", count=3)
        self.assertIn("西班牙语", text3)
        self.assertIn("恰好输出 3 条", text3)

    def test_description_prompt(self):
        text = build_description_prompt(SAMPLE_PRODUCT, None, "A1PA6795UKMFR9")
        self.assertIn("德语", text)
        self.assertIn("20000", text)
        self.assertIn("HTML", text)

    def test_search_terms_prompt(self):
        text = build_search_terms_prompt(SAMPLE_PRODUCT, None, "A1VC38T7YXB528")
        self.assertIn("日语", text)
        self.assertIn("250", text)
        self.assertIn("小写", text)

    def test_bullets_prompt_benefit_guidance(self):
        text = build_bullets_prompt(SAMPLE_PRODUCT, None, "ATVPDKIKX0DER", count=5)
        self.assertIn("利益点短语", text)
        self.assertIn("禁止把 `Benefit:`", text)
        self.assertIn("Compact 4x4 Chassis", text)

    def test_cfg_prompt_override(self):
        cfg = {"amazon_title_prompt": "CUSTOM TITLE INSTRUCTION {title_limit}"}
        text = build_title_prompt(SAMPLE_PRODUCT, cfg, "ATVPDKIKX0DER")
        self.assertIn("CUSTOM TITLE INSTRUCTION 200", text)
        self.assertNotIn("资深 listing 运营专家", text)

    def test_empty_product_does_not_crash(self):
        for builder in (build_title_prompt, build_description_prompt,
                        build_search_terms_prompt):
            text = builder({}, None, "ATVPDKIKX0DER")
            self.assertIsInstance(text, str)
            self.assertGreater(len(text), 50)
        self.assertIsInstance(build_bullets_prompt({}, None, ""), str)


class ParseBulletsTests(unittest.TestCase):
    def test_json_array(self):
        out = parse_bullets('["A durable frame", "B diesel engine", "C easy use"]')
        self.assertEqual(len(out), BULLET_DEFAULT_COUNT)
        self.assertEqual(out[0], "A durable frame")
        self.assertEqual(out[2], "C easy use")
        self.assertEqual(out[3:], ["", ""])

    def test_code_fence_json(self):
        out = parse_bullets('```json\n["one","two","three","four","five"]\n```')
        self.assertEqual(out, ["one", "two", "three", "four", "five"])

    def test_numbered_lines(self):
        text = "1. Strong frame\n2) Diesel engine\n3、Low fuel cost\n- Wide use\n• Easy service"
        out = parse_bullets(text, 5)
        self.assertEqual(out, ["Strong frame", "Diesel engine", "Low fuel cost",
                               "Wide use", "Easy service"])

    def test_bullet_dot_separated(self):
        out = parse_bullets("Alpha feature ● Beta feature ● Gamma feature", 3)
        self.assertEqual(out, ["Alpha feature", "Beta feature", "Gamma feature"])

    def test_newline_separated_and_truncated(self):
        out = parse_bullets("\n".join([f"feature {i}" for i in range(8)]), 5)
        self.assertEqual(len(out), 5)
        self.assertEqual(out[0], "feature 0")

    def test_cleaning_and_dedupe(self):
        out = parse_bullets('["<b>Bold</b> feature 😀", "Bold feature", "x±y"]', 3)
        self.assertEqual(out[0], "Bold feature")
        self.assertEqual(out[1], "x y")          # 去 ±
        # 重复项被去掉后补空
        self.assertEqual(out[2], "")

    def test_count_padding_and_limits(self):
        out = parse_bullets("only one", 4)
        self.assertEqual(len(out), 4)
        self.assertEqual(out, ["only one", "", "", ""])
        long_bullet = "x" * 900
        out = parse_bullets(long_bullet, 1)
        self.assertLessEqual(len(out[0]), 700)

    def test_junk_returns_empties(self):
        for raw in ("", "null", "[]"):
            out = parse_bullets(raw, 5)
            self.assertEqual(len(out), 5)
            self.assertTrue(all(x == "" for x in out), out)


class ParseSearchTermsTests(unittest.TestCase):
    def test_basic_normalization(self):
        out = parse_search_terms("Backhoe Loader, DIESEL; backhoe loader")
        self.assertEqual(out, "backhoe loader diesel")

    def test_stopwords_and_punctuation(self):
        out = parse_search_terms("The best for with and the backhoe loader!!!")
        self.assertEqual(out, "best backhoe loader")
        self.assertNotIn(",", out)
        self.assertNotIn("  ", out)

    def test_plural_dedupe(self):
        out = parse_search_terms("loader loaders diesel")
        self.assertEqual(out, "loader diesel")

    def test_code_fence_and_prefix(self):
        out = parse_search_terms("```\nSearch Terms: wheel loader 4x4\n```")
        self.assertEqual(out, "wheel loader 4x4")

    def test_byte_limit(self):
        out = parse_search_terms("keyword " * 200)
        self.assertLessEqual(len(out.encode("utf-8")), 250)
        self.assertLessEqual(len(out.split()), 36)

    def test_chinese_removed(self):
        out = parse_search_terms("反铲 backhoe 装载机 loader")
        self.assertEqual(out, "backhoe loader")


class CleanTests(unittest.TestCase):
    def test_clean_title(self):
        title = "<b>New</b> Backhoe 😀 Loader " + "x" * 400
        out = clean_title(title)
        self.assertLessEqual(len(out), TITLE_MAX)
        self.assertNotIn("<b>", out)
        self.assertNotIn("😀", out)
        # 词边界截断：不应以半个词结尾
        self.assertFalse(out.endswith("x") and len(out) > 100 and " " not in out[-30:])

    def test_clean_bullet(self):
        self.assertEqual(clean_bullet("<p>Diesel engine±high torque</p>"),
                         "Diesel engine high torque")
        self.assertEqual(clean_bullet("x" * 800)[:1], "x")
        self.assertLessEqual(len(clean_bullet("x" * 800)), 700)

    def test_clean_bullet_strips_placeholder_prefix(self):
        self.assertEqual(clean_bullet("Benefit: 75 KW DIESEL ENGINE: powerful"),
                         "75 KW DIESEL ENGINE: powerful")
        self.assertEqual(clean_bullet("Feature - durable steel frame"),
                         "durable steel frame")
        self.assertEqual(clean_bullet("FEATURE: low fuel cost"), "low fuel cost")
        self.assertEqual(clean_bullet("benefit - easy maintenance"), "easy maintenance")
        # 正常卖点短语不能被误删
        self.assertEqual(clean_bullet("POWERFUL ENGINE: strong torque"),
                         "POWERFUL ENGINE: strong torque")
        self.assertEqual(clean_bullet("Benefit:"), "")
        self.assertEqual(clean_bullet("Feature: Feature: real content"), "real content")

    def test_parse_bullets_strips_placeholder(self):
        out = parse_bullets('["Benefit: durable frame", "Feature: diesel engine", '
                            '"POWERFUL ENGINE: strong"]', 3)
        self.assertEqual(out, ["durable frame", "diesel engine",
                               "POWERFUL ENGINE: strong"])

    def test_sanitize_description(self):
        html = ("<p>Good <b>product</b></p><script>bad()</script>"
                "<style>p{}</style><img src=x><ul><li>one</li></ul>")
        out = sanitize_description(html)
        self.assertNotIn("<script", out)
        self.assertNotIn("<style", out)
        self.assertNotIn("<img", out)
        self.assertIn("<p>", out)
        self.assertIn("<b>product</b>", out)
        self.assertIn("<li>one</li>", out)
        self.assertNotIn("bad", out)  # script 整段被删

    def test_sanitize_plain_text_wraps_paragraphs(self):
        out = sanitize_description("line one\nline two")
        self.assertEqual(out, "<p>line one</p><p>line two</p>")

    def test_sanitize_byte_limit(self):
        out = sanitize_description("<p>" + ("x" * 30000) + "</p>", 20000)
        self.assertLessEqual(len(out.encode("utf-8")), 20000)

    def test_clean_for_site(self):
        self.assertEqual(clean_for_site("反铲 backhoe", "A2EUQ1WTGCTBG2").strip(),
                         "backhoe")
        self.assertEqual(clean_for_site("反铲 backhoe", "AAHKV2X7AFYLW"),
                         "反铲 backhoe")

    def test_helpers(self):
        self.assertEqual(remove_emoji("a😀b"), "ab")
        self.assertTrue(has_chinese("反铲"))
        self.assertFalse(has_chinese("backhoe"))
        self.assertEqual(truncate_words("hello world foo", 11), "hello world")
        self.assertEqual(truncate_bytes("aaa bbb ccc", 7), "aaa bbb")


if __name__ == "__main__":
    unittest.main(verbosity=2)
