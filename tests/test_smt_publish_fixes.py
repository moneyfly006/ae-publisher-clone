#!/usr/bin/env python3
"""速卖通真实发布失败修复回归（离线）。

已定位的 15 条真实发布失败：
1. 11 条「主图处理出错：*.webp」→ 平台主图不接受 webp，且 _fix_webp_images 曾是孤岛函数；
2. 1 条 isv.7011012 自定义属性含 ㎡ 等特殊字符；
3. nationalPriceMode / 运费模板类为历史数据/配置问题（不在本文件）。

本文件验证 1/2 的修复：webp→JPG 真正被调用、特殊字符清洗。
"""
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image  # noqa: E402

from aeclone.services import api_save  # noqa: E402
from aeclone.services import publisher as PB  # noqa: E402


def _webp_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 30, 30)).save(buf, format="WEBP")
    return buf.getvalue()


class _FakeResp:
    status_code = 200
    def __init__(self, content): self.content = content


class _Res:
    def __init__(self): self.steps = []


class WebpPublishFixTests(unittest.TestCase):
    def test_fix_webp_images_converts_and_replaces(self):
        res = _Res()
        steps = []
        with patch.object(PB, "add_step",
                          lambda r, n, ok, d="": steps.append((n, ok, d))), \
             patch("requests.get", return_value=_FakeResp(_webp_bytes())), \
             patch("aeclone.services.hosting.upload", return_value="https://img.example/new.jpg"):
            p = {"images": ["https://x.example/a.webp"],
                 "detail": '<p><img src="https://x.example/d.webp"></p>'}
            PB.Publisher._fix_webp_images(PB.Publisher.__new__(PB.Publisher), p, res)
        self.assertEqual(p["images"], ["https://img.example/new.jpg"])
        self.assertIn("https://img.example/new.jpg", p["detail"])
        self.assertTrue(any(n == "主图转JPG" for n, _ok, _d in steps))

    def test_fix_webp_images_keeps_non_webp(self):
        res = _Res()
        with patch.object(PB, "add_step", lambda *a, **k: None), \
             patch("aeclone.services.hosting.upload", return_value="https://img.example/x.jpg"):
            p = {"images": ["https://x.example/a.jpg"]}
            PB.Publisher._fix_webp_images(PB.Publisher.__new__(PB.Publisher), p, res)
        self.assertEqual(p["images"], ["https://x.example/a.jpg"])

    def test_run_wires_webp_fix_before_save(self):
        src = (Path(__file__).resolve().parents[1] / "src" / "aeclone" /
               "services" / "publisher.py").read_text(encoding="utf-8")
        self.assertIn("self._fix_webp_images(p, res)", src,
                      "run() 必须调用 _fix_webp_images（曾是孤岛函数，11 条发布失败根因）")
        self.assertIn("_fix_webp_images(_wt, res)", src,
                      "publish_only 重试发布也要修 webp 主图")


class _ImgResp:
    def __init__(self, status=200, ct="image/png", body=b"\x89PNG\r\n\x1a\n"):
        self.status_code = status
        self.headers = {"content-type": ct}
        class _Raw:
            def __init__(self, b): self._b = b
            def read(self, n=-1): return self._b
        self.raw = _Raw(body)


class PublishImagePrecheckTests(unittest.TestCase):
    def test_missing_image_blocks_publish(self):
        pub = PB.Publisher.__new__(PB.Publisher)
        with patch("requests.get", return_value=_ImgResp(status=404, ct="application/xml", body=b"")):
            err = pub._check_main_image_for_publish(["https://x/expired.webp"])
        self.assertIn("主图链接失效", err)
        self.assertIn("HTTP 404", err)

    def test_valid_image_passes(self):
        pub = PB.Publisher.__new__(PB.Publisher)
        with patch("requests.get", return_value=_ImgResp()):
            self.assertEqual(pub._check_main_image_for_publish(["https://x/a.png"]), "")

    def test_non_image_content_blocked(self):
        pub = PB.Publisher.__new__(PB.Publisher)
        with patch("requests.get", return_value=_ImgResp(ct="text/html", body=b"<html>")):
            self.assertIn("不是有效图片", pub._check_main_image_for_publish(["https://x/a.jpg"]))

    def test_publish_only_has_precheck_wiring(self):
        src = (Path(__file__).resolve().parents[1] / "src" / "aeclone" /
               "services" / "publisher.py").read_text(encoding="utf-8")
        self.assertIn("_check_main_image_for_publish", src)


class NationalPriceModeTests(unittest.TestCase):
    def test_quote_without_mode_is_auto_cleared(self):
        product = {"id": "1", "subject": "T", "categoryId": "100007204",
                   "imageURLs": "https://x/a.jpg", "aeopAeProductPropertys": "[]",
                   "aeopNationalQuoteConfiguration": '{"configurationType": "ABSOLUTE"}'}
        p = api_save.build_save_payload(product, {"aeopAeProductPropertys": "[]"},
                                        action="publish")
        self.assertEqual(p.get("nationalPriceMode"), "")
        self.assertEqual(p.get("aeopNationalQuoteConfiguration"), "")

    def test_quote_with_mode_passes(self):
        product = {"id": "1", "subject": "T", "categoryId": "100007204",
                   "imageURLs": "https://x/a.jpg", "aeopAeProductPropertys": "[]",
                   "nationalPriceMode": "1",
                   "aeopNationalQuoteConfiguration": '{"configurationType": "ABSOLUTE"}'}
        p = api_save.build_save_payload(product, {"aeopAeProductPropertys": "[]"},
                                        action="publish")
        self.assertEqual(p["nationalPriceMode"], "1")


class AttrSanitizeTests(unittest.TestCase):
    def test_sanitize_common_special_chars(self):
        self.assertEqual(api_save.sanitize_attr_value("面积 12㎡ ≤ 20℃"),
                         "12m2 <= 20C")  # 面积 为中文，被移除
        self.assertEqual(api_save.sanitize_attr_value("Size ±2mm"), "Size +/-2mm")

    def test_sanitize_keeps_ascii_and_strips_cjk_emoji(self):
        out = api_save.sanitize_attr_value("Model X200 长度 30cm 👍")
        self.assertIn("Model X200", out)
        self.assertIn("30cm", out)
        self.assertNotIn("长度", out)
        self.assertNotIn("👍", out)

    def test_nbsp_and_zero_width_removed(self):
        out = api_save.sanitize_attr_value("Local Service\xa0Location\u200b")
        self.assertEqual(out, "Local Service Location")

    def test_custom_attrs_are_sanitized_in_payload(self):
        product = {
            "id": "1", "subject": "T", "categoryId": "100007204",
            "aeopAeProductPropertys": "[]",
            "imageURLs": "https://x.example/a.jpg",
        }
        template = {"aeopAeProductPropertys": "[]"}
        p = api_save.build_save_payload(product, template,
                                        custom_attributes=["Material=不锈钢 ㎡"])
        import json
        props = json.loads(p["aeopAeProductPropertys"])
        values = " ".join(str(x.get("attrValue")) for x in props if x.get("attrName") == "Material")
        self.assertIn("m2", values)
        self.assertNotIn("㎡", values)

    def test_custom_attr_preserves_word_spaces(self):
        product = {
            "id": "1", "subject": "T", "categoryId": "100007204",
            "aeopAeProductPropertys": '[]',
            "imageURLs": "https://x.example/a.jpg",
        }
        p = api_save.build_save_payload(product, {"aeopAeProductPropertys": "[]"},
                                        custom_attributes=["Power Voltage=380V three phase 50HZ"])
        import json
        props = json.loads(p["aeopAeProductPropertys"])
        value = next(x["attrValue"] for x in props if x.get("attrName") == "Power Voltage")
        self.assertEqual(value, "380V three phase 50HZ")


if __name__ == "__main__":
    unittest.main(verbosity=2)
