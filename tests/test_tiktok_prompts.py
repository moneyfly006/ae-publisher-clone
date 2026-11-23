"""TikTok 提示词/解析/清洗离线单测（不联网、不写文件）。

跑法：``.venv/bin/python tests/test_tiktok_prompts.py``
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.services.tiktok_prompts import (
    DEFAULT_PROMPTS, DESCRIPTION_MAX_CHARS, KEY_FEATURES_MAX, KEY_FEATURES_TOTAL_MAX,
    PROMPT_KINDS, SEARCH_TERMS_MAX, SEARCH_TERMS_TOTAL_MAX, SITE_META, TITLE_MAX,
    TITLE_MIN, build_description_prompt, build_key_features_prompt, build_prompt,
    build_search_terms_prompt, build_title_prompt, clean_for_site, clean_title,
    has_chinese, html_description, is_us_site, language_for_site, normalize_site,
    parse_key_features, parse_search_terms, prompt_for, remove_emoji, site_meta,
    site_supported, strip_html, truncate_chars, truncate_words,
)

SAMPLE_PRODUCT = {
    "id": "1729000000000001",
    "productName": "Portable Wireless Bluetooth Speaker Waterproof Outdoor 20W",
    "sellerSku": "SPK-001",
    "brandInfo": "Generic",
    "categoryName": "Portable Speakers",
    "categoryId": "601234",
    "description": "<p>Portable speaker for outdoor use.</p>",
    "productAttributes": '[{"name":"Power","value":"20W"}]',
    "sourceUrl": "https://www.alibaba.com/product-detail/x_1.html",
    "variationList": [{"sellerSku": "SPK-001", "originalPrice": 19.99}],
}

SITE_LANGS = {
    "US": "en", "GB": "en", "IE": "en", "SG": "en", "PH": "en",
    "DE": "de", "AT": "de", "FR": "fr", "BE": "nl", "NL": "nl",
    "ES": "es", "IT": "it", "PT": "pt", "PL": "pl", "CZ": "cs",
    "GR": "el", "HU": "hu", "JP": "ja", "TH": "th", "VN": "vi",
    "MY": "ms", "ID": "id", "BR": "pt", "MX": "es",
}


class SiteMetaTests(unittest.TestCase):
    def test_site_meta_24_sites(self):
        self.assertEqual(len(SITE_META), 24)
        self.assertEqual(len(SITE_META), len(SITE_LANGS))
        for site in SITE_LANGS:
            self.assertIn(site, SITE_META)
        for meta in SITE_META.values():
            for key in ("code", "country", "currency", "lang", "lang_name"):
                self.assertTrue(meta.get(key), f"{meta} 缺少 {key}")

    def test_site_meta_client_key_aliases(self):
        # integrations/tiktok.py 用 language/language_name；两套键名必须一致
        for site, meta in SITE_META.items():
            self.assertEqual(meta["language"], meta["lang"], site)
            self.assertEqual(meta["language_name"], meta["lang_name"], site)

    def test_site_currencies(self):
        self.assertEqual(SITE_META["US"]["currency"], "USD")
        self.assertEqual(SITE_META["GB"]["currency"], "GBP")
        self.assertEqual(SITE_META["IE"]["currency"], "EUR")
        self.assertEqual(SITE_META["CZ"]["currency"], "CZK")
        self.assertEqual(SITE_META["JP"]["currency"], "JPY")
        self.assertEqual(SITE_META["TH"]["currency"], "THB")
        self.assertEqual(SITE_META["VN"]["currency"], "VND")
        self.assertEqual(SITE_META["MY"]["currency"], "MYR")
        self.assertEqual(SITE_META["SG"]["currency"], "SGD")
        self.assertEqual(SITE_META["PH"]["currency"], "PHP")
        self.assertEqual(SITE_META["ID"]["currency"], "IDR")
        self.assertEqual(SITE_META["BR"]["currency"], "BRL")
        self.assertEqual(SITE_META["MX"]["currency"], "MXN")

    def test_language_for_site(self):
        for site, lang in SITE_LANGS.items():
            self.assertEqual(language_for_site(site), lang, site)
        self.assertEqual(language_for_site("unknown"), "en")
        self.assertEqual(language_for_site(""), "en")
        self.assertEqual(language_for_site("de"), "de")  # 大小写不敏感

    def test_site_meta_returns_copy(self):
        meta = site_meta("de")
        self.assertTrue(meta["known"])
        self.assertEqual(meta["country"], "德国")
        self.assertEqual(meta["lang_name"], "德语")
        meta["country"] = "XX"
        self.assertEqual(SITE_META["DE"]["country"], "德国")

    def test_site_meta_unknown(self):
        meta = site_meta("ZZ")
        self.assertFalse(meta["known"])
        self.assertEqual(meta["lang"], "en")
        self.assertFalse(site_supported("ZZ"))
        self.assertTrue(site_supported("gb"))

    def test_is_us_site(self):
        self.assertTrue(is_us_site("US"))
        self.assertTrue(is_us_site("us"))
        self.assertTrue(is_us_site("4Seller"))
        self.assertFalse(is_us_site("GB"))
        self.assertFalse(is_us_site("DE"))
        self.assertFalse(is_us_site(""))
        self.assertFalse(is_us_site("unknown"))

    def test_normalize_site(self):
        self.assertEqual(normalize_site("gb"), "GB")
        self.assertEqual(normalize_site(" Gb "), "GB")
        self.assertEqual(normalize_site("4Seller"), "4SELLER")
        self.assertEqual(normalize_site(""), "")


class PromptBuilderTests(unittest.TestCase):
    def test_title_prompt_us(self):
        text = build_title_prompt(SAMPLE_PRODUCT, None, "US")
        self.assertIn("美国", text)
        self.assertIn("TikTok Shop US", text)
        self.assertIn("英语", text)
        self.assertIn("USD", text)
        self.assertIn(str(TITLE_MIN), text)
        self.assertIn(str(TITLE_MAX), text)
        self.assertIn(SAMPLE_PRODUCT["productName"], text)
        self.assertIn("SKU", text)
        self.assertIn("只输出", text)
        self.assertIn("禁止", text)

    def test_title_prompt_non_english_site(self):
        text = build_title_prompt(SAMPLE_PRODUCT, None, "DE")
        self.assertIn("德国", text)
        self.assertIn("德语", text)
        self.assertIn("EUR", text)
        self.assertIn("禁止出现任何中文字符", text)

    def test_description_prompt(self):
        text = build_description_prompt(SAMPLE_PRODUCT, None, "JP")
        self.assertIn("日本", text)
        self.assertIn("日语", text)
        self.assertIn("JPY", text)
        self.assertIn(str(DESCRIPTION_MAX_CHARS), text)
        self.assertIn("<p>", text)
        self.assertIn("<ul>", text)
        self.assertIn("HTML", text)

    def test_key_features_prompt_us(self):
        text = build_key_features_prompt(SAMPLE_PRODUCT, None, "US", count=5)
        self.assertIn("恰好输出 5 条", text)
        self.assertIn(str(KEY_FEATURES_TOTAL_MAX), text)
        self.assertIn("JSON", text)
        self.assertIn("美国", text)
        self.assertNotIn("不使用该字段", text)
        text3 = build_key_features_prompt(SAMPLE_PRODUCT, None, "US", count=3)
        self.assertIn("恰好输出 3 条", text3)

    def test_key_features_prompt_non_us_site_has_notice(self):
        text = build_key_features_prompt(SAMPLE_PRODUCT, None, "GB", count=5)
        self.assertIn("仅美国站", text)
        self.assertIn("不使用该字段", text)

    def test_search_terms_prompt(self):
        text = build_search_terms_prompt(SAMPLE_PRODUCT, None, "US")
        self.assertIn(str(SEARCH_TERMS_MAX), text)
        self.assertIn(str(SEARCH_TERMS_TOTAL_MAX), text)
        self.assertIn("小写", text)
        self.assertIn("空格分隔", text)
        self.assertIn("美国", text)

    def test_default_prompts_and_cfg_override(self):
        self.assertEqual(set(DEFAULT_PROMPTS), set(PROMPT_KINDS))
        for kind in PROMPT_KINDS:
            self.assertTrue(DEFAULT_PROMPTS[kind].strip())
        cfg = {"tiktok_title_prompt": "自定义标题提示词，输出英文标题"}
        self.assertIn("自定义标题提示词", prompt_for(cfg, "title"))
        text = build_title_prompt(SAMPLE_PRODUCT, cfg, "US")
        self.assertIn("自定义标题提示词", text)
        self.assertEqual(prompt_for(None, "title"), DEFAULT_PROMPTS["title"])

    def test_build_prompt_dispatch(self):
        self.assertIn("标题", build_prompt("title", SAMPLE_PRODUCT, None, "US"))
        self.assertIn("HTML", build_prompt("description", SAMPLE_PRODUCT, None, "US"))
        self.assertIn("JSON", build_prompt("key_features", SAMPLE_PRODUCT, None, "US"))
        self.assertIn("小写", build_prompt("search_terms", SAMPLE_PRODUCT, None, "US"))
        with self.assertRaises(ValueError):
            build_prompt("unknown", SAMPLE_PRODUCT, None, "US")


class ParseKeyFeaturesTests(unittest.TestCase):
    def test_json_array(self):
        items = parse_key_features('["Fast Charging: 30 min", "Deep Bass: rich sound"]')
        self.assertEqual(items, ["Fast Charging: 30 min", "Deep Bass: rich sound"])

    def test_json_dict_wrapper(self):
        items = parse_key_features('{"key_features": ["A: b", "C: d", "E: f"]}')
        self.assertEqual(items, ["A: b", "C: d", "E: f"])

    def test_code_fence_and_explanation(self):
        text = '好的，结果如下：\n```json\n["A: b", "C: d", "E: f"]\n```\n希望有帮助。'
        self.assertEqual(parse_key_features(text), ["A: b", "C: d", "E: f"])

    def test_numbered_lines(self):
        text = "1. Fast Charging: 30 min\n2) Deep Bass: rich sound\n3、Portable: 300g"
        items = parse_key_features(text)
        self.assertEqual(len(items), 3)
        self.assertEqual(items[0], "Fast Charging: 30 min")
        for item in items:
            self.assertFalse(item.startswith(("1", "2", "3")))

    def test_inline_numbers(self):
        text = "1. A: b 2. C: d 3. E: f"
        self.assertEqual(parse_key_features(text), ["A: b", "C: d", "E: f"])

    def test_bullet_separators(self):
        self.assertEqual(parse_key_features("A: b●C: d•E: f"), ["A: b", "C: d", "E: f"])
        self.assertEqual(parse_key_features("A: b|C: d|E: f"), ["A: b", "C: d", "E: f"])

    def test_count_cap(self):
        text = '["1: a", "2: b", "3: c", "4: d", "5: e", "6: f", "7: g"]'
        self.assertEqual(len(parse_key_features(text, count=5)), KEY_FEATURES_MAX)
        self.assertEqual(len(parse_key_features(text, count=3)), 3)
        self.assertEqual(len(parse_key_features(text, count=99)), KEY_FEATURES_MAX)

    def test_cleans_html_emoji_quotes(self):
        items = parse_key_features('["<b>Fast Charging</b>: 30 min 🚀", "Deep Bass: rich"]')
        self.assertNotIn("<b>", items[0])
        self.assertNotIn("🚀", items[0])
        self.assertTrue(items[0].startswith("Fast Charging"))
        self.assertEqual(items[1], "Deep Bass: rich")

    def test_dedupe(self):
        items = parse_key_features('["A: b", "a: b", "C: d"]')
        self.assertEqual(len(items), 2)

    def test_total_length_limit(self):
        long_item = "X: " + "a" * 400
        items = parse_key_features('["%s", "%s", "%s", "%s", "%s"]' % ((long_item,) * 5))
        self.assertLessEqual(sum(len(x) for x in items), KEY_FEATURES_TOTAL_MAX)
        self.assertGreaterEqual(len(items), 1)

    def test_empty_inputs(self):
        for raw in ("", "  ", "null", "none", "[]", "{}", None):
            self.assertEqual(parse_key_features(raw), [])

    def test_total_length_smaller_than_max(self):
        items = parse_key_features('["A: b", "C: d", "E: f", "G: h", "I: j"]')
        self.assertLessEqual(sum(len(x) for x in items), KEY_FEATURES_TOTAL_MAX)


class ParseSearchTermsTests(unittest.TestCase):
    def test_json_array(self):
        terms = parse_search_terms('["Bluetooth Speaker", "Portable Speaker"]')
        self.assertEqual(terms, ["bluetooth", "speaker", "portable"])

    def test_comma_and_semicolon(self):
        terms = parse_search_terms("bluetooth speaker, portable speaker; waterproof")
        self.assertEqual(terms, ["bluetooth", "speaker", "portable", "waterproof"])

    def test_lowercase_dedupe_stopwords(self):
        terms = parse_search_terms("the Bluetooth Speaker for bluetooth speaker")
        for term in terms:
            self.assertEqual(term, term.lower())
        self.assertNotIn("the", terms)
        self.assertNotIn("for", terms)
        self.assertEqual(terms.count("bluetooth"), 1)
        self.assertEqual(terms.count("speaker"), 1)

    def test_prefix_and_code_fence(self):
        terms = parse_search_terms("```\nSearch Terms: wireless speaker\n```")
        self.assertEqual(terms, ["wireless", "speaker"])

    def test_max_count(self):
        raw = " ".join(f"kw{i}" for i in range(30))
        self.assertEqual(len(parse_search_terms(raw)), SEARCH_TERMS_MAX)

    def test_total_length(self):
        raw = " ".join("x" * 40 for _ in range(20))
        self.assertLessEqual(len(" ".join(parse_search_terms(raw))), SEARCH_TERMS_TOTAL_MAX)

    def test_removes_punctuation_and_chinese(self):
        terms = parse_search_terms("蓝牙!!! speaker@@@, portable###")
        self.assertEqual(terms, ["speaker", "portable"])

    def test_empty(self):
        for raw in ("", "null", "[]", None):
            self.assertEqual(parse_search_terms(raw), [])


class HtmlDescriptionTests(unittest.TestCase):
    def test_plain_text_wrapped(self):
        html = html_description("Great speaker for travel.")
        self.assertEqual(html, "<p>Great speaker for travel.</p>")

    def test_paragraphs(self):
        html = html_description("First paragraph.\nSecond paragraph.")
        self.assertEqual(html, "<p>First paragraph.</p><p>Second paragraph.</p>")

    def test_bullet_lines_become_list(self):
        html = html_description("Features:\n- 20h battery\n- IPX7 waterproof")
        self.assertIn("<ul><li>20h battery</li><li>IPX7 waterproof</li></ul>", html)
        self.assertIn("<p>Features:</p>", html)

    def test_allowed_tags_kept(self):
        raw = "<p>Text<b>bold</b><br>line</p><ul><li>item</li></ul>"
        self.assertEqual(html_description(raw),
                         "<p>Text<b>bold</b><br/>line</p><ul><li>item</li></ul>")

    def test_dangerous_tags_removed(self):
        raw = '<p>ok</p><script>alert(1)</script><style>.a{}</style><img src="x">'
        html = html_description(raw)
        self.assertNotIn("<script", html)
        self.assertNotIn("<style", html)
        self.assertNotIn("<img", html)
        self.assertIn("<p>ok</p>", html)

    def test_unknown_tags_stripped_text_kept(self):
        html = html_description('<div class="x">Important <span>text</span></div>')
        self.assertNotIn("<div", html)
        self.assertNotIn("<span", html)
        self.assertIn("Important", html)
        self.assertIn("text", html)

    def test_tag_attributes_removed(self):
        html = html_description('<p style="color:red">Hello</p>')
        self.assertEqual(html, "<p>Hello</p>")

    def test_code_fence_stripped(self):
        html = html_description("```html\n<p>Hello</p>\n```")
        self.assertEqual(html, "<p>Hello</p>")

    def test_emoji_removed(self):
        html = html_description("<p>Great 🚀 speaker</p>")
        self.assertNotIn("🚀", html)

    def test_max_length(self):
        raw = "<p>" + "a" * (DESCRIPTION_MAX_CHARS + 500) + "</p>"
        html = html_description(raw)
        self.assertLessEqual(len(html), DESCRIPTION_MAX_CHARS)

    def test_empty(self):
        for raw in ("", "   ", None):
            self.assertEqual(html_description(raw), "")

    def test_br_normalized(self):
        self.assertIn("<br/>", html_description("<p>a<br>b</p>"))


class CleanTitleTests(unittest.TestCase):
    def test_basic_clean(self):
        self.assertEqual(clean_title("  Wireless   Speaker \n"), "Wireless Speaker")
        self.assertEqual(clean_title('"Wireless Speaker"'), "Wireless Speaker")
        self.assertEqual(clean_title("<b>Wireless Speaker</b>"), "Wireless Speaker")
        self.assertEqual(clean_title("Title: Wireless Speaker"), "Wireless Speaker")
        self.assertEqual(clean_title("```\nWireless Speaker\n```"), "Wireless Speaker")

    def test_truncate_to_max(self):
        title = clean_title("word " * 100)
        self.assertLessEqual(len(title), TITLE_MAX)

    def test_non_chinese_site_removes_chinese(self):
        cleaned = clean_title("无线蓝牙音箱 Wireless Bluetooth Speaker", site="DE")
        self.assertFalse(has_chinese(cleaned))
        self.assertIn("Wireless", cleaned)

    def test_emoji_removed(self):
        self.assertNotIn("🚀", clean_title("Wireless Speaker 🚀"))

    def test_clean_for_site(self):
        cleaned = clean_for_site("abc 中文 def", "GB")
        self.assertNotIn("中文", cleaned)
        self.assertIn("abc", cleaned)
        self.assertIn("def", cleaned)
        # TikTok 没有中文站点：未知站点按英语处理，同样去中文
        self.assertNotIn("中文", clean_for_site("abc 中文 def", ""))


class TextHelperTests(unittest.TestCase):
    def test_has_chinese(self):
        self.assertTrue(has_chinese("中文"))
        self.assertFalse(has_chinese("english"))

    def test_remove_emoji(self):
        self.assertEqual(remove_emoji("a🚀b"), "ab")

    def test_strip_html(self):
        self.assertEqual(strip_html("<p>a <b>b</b></p>"), "a b")

    def test_truncate(self):
        self.assertEqual(truncate_chars("abcdef", 3), "abc")
        self.assertEqual(truncate_words("ab cd ef", 8), "ab cd ef")
        self.assertEqual(truncate_words("ab cd ef", 5), "ab")
        self.assertEqual(truncate_words("abcdef", 3), "abc")


if __name__ == "__main__":
    unittest.main(verbosity=2)
