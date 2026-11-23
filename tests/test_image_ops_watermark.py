#!/usr/bin/env python3
"""图片本地操作回归：水印可见性（白底/深底）、营销图尺寸、方图/四合一。

2026-11-22 实测问题：
- 文字水印固定白字 + 打在白底方图上像素零变化（旧实现白字白底不可见）；
- macOS 无 arial.ttf → 回退 10px 位图字体，水印小到看不见；
- 3:4 营销图生成 750×1000，短边低于平台 ≥800 主图门槛。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image, ImageChops  # noqa: E402

from aeclone.services.image_ops import (  # noqa: E402
    add_watermark, four_in_one, make_marketing, pad_square,
)


class WatermarkVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="imgops-"))

    def _bg(self, color) -> Path:
        p = self.tmp / f"bg-{color[0]}.png"
        Image.new("RGB", (1024, 1024), color).save(p)
        return p

    def _diff_bbox(self, src: Path, out: Path):
        a = Image.open(src).convert("RGB")
        b = Image.open(out).convert("RGB")
        return ImageChops.difference(a, b).getbbox()

    def test_text_watermark_visible_on_white_background(self):
        src = self._bg((255, 255, 255))
        out = self.tmp / "w-white.jpg"
        add_watermark(src, "AURORA TEST", out, position="bottom-right")
        bbox = self._diff_bbox(src, out)
        self.assertIsNotNone(bbox, "白底图上文字水印不可见（像素零变化）")
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        self.assertGreater(w, 120, f"水印过小：宽 {w}px")
        self.assertGreater(h, 20, f"水印字体过小：高 {h}px")

    def test_text_watermark_visible_on_dark_background(self):
        src = self._bg((20, 20, 20))
        out = self.tmp / "w-dark.jpg"
        add_watermark(src, "AURORA TEST", out)
        self.assertIsNotNone(self._diff_bbox(src, out), "深底图上文字水印不可见")

    def test_watermark_scales_with_image_width(self):
        big = self.tmp / "big.png"
        Image.new("RGB", (2000, 2000), (255, 255, 255)).save(big)
        out = self.tmp / "big-w.jpg"
        add_watermark(big, "AURORA", out)
        bbox = self._diff_bbox(big, out)
        self.assertIsNotNone(bbox)
        self.assertGreater(bbox[3] - bbox[1], 30, "大图水印未随宽度放大")


class MarketingSizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="imgops-mk-"))
        self.src = self.tmp / "src.png"
        Image.new("RGB", (1200, 900), (200, 200, 200)).save(self.src)

    def test_marketing_3x4_at_least_800_px(self):
        out = make_marketing(self.src, self.tmp / "m34.jpg", (800, 1067))
        img = Image.open(out)
        self.assertEqual(img.size, (800, 1067))
        self.assertGreaterEqual(min(img.size), 800,
                                "3:4 营销图短边必须 ≥800（平台主图门槛）")

    def test_marketing_1x1_is_800(self):
        out = make_marketing(self.src, self.tmp / "m11.jpg", (800, 800))
        self.assertEqual(Image.open(out).size, (800, 800))

    def test_pad_square_and_four_in_one(self):
        sq = pad_square(self.src, self.tmp / "sq.jpg")
        self.assertEqual(Image.open(sq).size, (1024, 1024))
        four = four_in_one([self.src, self.src, self.src, self.src],
                           self.tmp / "four.jpg")
        self.assertEqual(Image.open(four).size, (1200, 1200))


class PublisherMarketingSizeTests(unittest.TestCase):
    def test_publisher_generates_800_wide_3x4(self):
        """保存链路里 3:4 营销图的生成尺寸不得低于 800（回归 750x1000 旧值）。"""
        src = (Path(__file__).resolve().parents[1] /
               "src" / "aeclone" / "services" / "publisher.py").read_text()
        self.assertIn("(800, 1067), (800, 800)", src)
        self.assertNotIn("((750, 1000), (800, 800))", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
