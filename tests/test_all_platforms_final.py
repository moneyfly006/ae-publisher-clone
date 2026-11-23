"""最终全平台验收证据结构测试（task-18；离线，仅校验证据）。"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("AURORA_TEST", "1")

EVIDENCE = ROOT / "docs" / "all-platforms-final-verification.json"
SHOTS = ROOT / "docs" / "design" / "ui-verify" / "all-platforms"


class FinalEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(EVIDENCE.read_text(encoding="utf-8"))
        cls.summary = cls.data["summary"]

    def test_summary_all_pass(self):
        self.assertEqual(self.summary["failed"], [])
        self.assertEqual(self.summary["passed"], self.summary["total"])
        self.assertGreaterEqual(self.summary["total"], 20)

    def test_platform_pages(self):
        platforms = self.data["platforms"]
        self.assertEqual(set(platforms), {"alibaba", "aliexpress", "amazon",
                                          "tiktok", "shopee"})
        for pid, row in platforms.items():
            self.assertFalse(row["problems"], pid)
            self.assertTrue(row["pages"], pid)
        self.assertFalse(platforms["shopee"]["implemented"])
        self.assertEqual(len(platforms["amazon"]["pages"]), 4)
        self.assertEqual(len(platforms["aliexpress"]["pages"]), 12)

    def test_screenshots(self):
        manifest = json.loads((SHOTS / "manifest-final.json").read_text(
            encoding="utf-8"))
        self.assertEqual(manifest["total"], 34)
        self.assertEqual(manifest["coverage"],
                         {"alibaba": 14, "aliexpress": 12, "amazon": 4,
                          "tiktok": 4})
        hashes = {row["sha256_16"] for row in manifest["files"]}
        self.assertEqual(len(hashes), manifest["total"])
        for row in manifest["files"]:
            path = SHOTS / row["file"]
            self.assertTrue(path.exists())
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest()[:16],
                             row["sha256_16"])

    def test_images_objective(self):
        checks = {row["name"]: row["ok"] for row in
                  self.data["images"]["checks"]}
        self.assertTrue(all(checks.values()), checks)
        dims = self.data["images"]["dims"]
        self.assertEqual(dims["square"], [1024, 1024])
        self.assertEqual(dims["four"], [1200, 1200])

    def test_video(self):
        video = self.data["video"]
        self.assertTrue(video["decode_ok"])
        self.assertTrue(all(video["probe"].values()))
        self.assertGreater(video["bytes"], 0)

    def test_build(self):
        build = self.data["build"]
        self.assertTrue(build["exists"])
        self.assertTrue(build["codesign_ok"])
        self.assertTrue(build["sha_matches_expected"])
        self.assertEqual(len(build["sha256"]), 64)

    def test_ai_matrix(self):
        lead = self.data["lead_evidence"]
        self.assertTrue(all(row["ok"] for row in lead["ai_text"].values()))
        self.assertFalse(lead["ai_shopee"]["implemented"])
        self.assertEqual(lead["button_audit"]["counts"]["ERROR"], 0)
        self.assertEqual(lead["button_audit"]["callbacks"], 627)
        self.assertEqual(lead["connections"]["items"], 10)
        self.assertTrue(self.data["amazon_ai_supplement"]["ok"])
        self.assertEqual(len(self.data["amazon_ai_supplement"]["cases"]), 8)

    def test_no_platform_write(self):
        self.assertTrue(self.data["real_vs_offline"]["NO_WRITE"])
        self.assertIn("未执行任何平台写操作",
                      self.data["real_vs_offline"]["NO_WRITE"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
