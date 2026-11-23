import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.platforms import PLATFORMS, get_platform, ALIBABA, ALIEXPRESS
from aeclone.ui.navigation import DESTINATIONS


class PlatformTests(unittest.TestCase):
    def test_all_page_indices_exist(self):
        valid = {d.index for d in DESTINATIONS}
        for pid, p in PLATFORMS.items():
            for page in p.pages:
                self.assertIn(page.index, valid, f"{pid}:{page.index}")

    def test_alibaba_has_trade_and_no_aliexpress_pages(self):
        labels = [p.label for p in ALIBABA.pages]
        self.assertIn("价格与贸易", labels)
        self.assertTrue(ALIBABA.page(32).matches("FOB"))
        self.assertIn("类目与属性", labels)
        self.assertNotIn("变种与库存", labels)

    def test_default_and_fallback(self):
        self.assertEqual(get_platform("").id, "alibaba")
        self.assertEqual(get_platform("aliexpress").id, "aliexpress")
        self.assertEqual(get_platform("unknown").id, "alibaba")

    def test_features_are_known(self):
        from aeclone.licensing import ALL_FEATURES
        for p in PLATFORMS.values():
            for page in p.pages:
                self.assertIn(page.feature, ALL_FEATURES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
