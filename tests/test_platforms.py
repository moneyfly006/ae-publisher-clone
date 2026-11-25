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
        # Alibaba 六步：价格与贸易并入 31「商品资料与价格」；旧 32 不再单独成页。
        labels = [p.label for p in ALIBABA.pages]
        self.assertIn("商品资料与价格", labels)
        self.assertTrue(ALIBABA.page(31).matches("贸易 价格"))
        self.assertIn("类目与属性", labels)
        self.assertNotIn("变种与库存", labels)
        self.assertNotIn("价格与贸易", labels)
        indices = {p.index for p in ALIBABA.pages}
        self.assertTrue({1, 29, 30, 31, 3, 33}.issubset(indices),
                        "六步流程页必须存在：1/29/30/31/3/33")
        self.assertTrue({12, 32, 5, 44}.isdisjoint(indices),
                        "旧路由页只做重定向，不应继续出现在导航定义中")

    def test_default_and_fallback(self):
        self.assertEqual(get_platform("").id, "alibaba")
        self.assertEqual(get_platform("aliexpress").id, "aliexpress")
        self.assertEqual(get_platform("unknown").id, "alibaba")

    def test_features_are_known(self):
        from aeclone.licensing import ALL_FEATURES
        for p in PLATFORMS.values():
            for page in p.pages:
                self.assertIn(page.feature, ALL_FEATURES)

    def test_page_feature_invariants(self):
        """不变量：每个 PageItem.feature 必须属于该平台的 PLATFORM_FEATURES。"""
        from aeclone.licensing import PLATFORM_FEATURES
        issues = []
        for pid, p in PLATFORMS.items():
            allowed = set(PLATFORM_FEATURES.get(pid, []))
            for page in p.pages:
                if page.feature not in allowed:
                    issues.append(f"{pid}#{page.index} {page.label} feature={page.feature}")
        self.assertEqual(issues, [], "页面 feature 不在平台功能包内：" + "; ".join(issues))

    def test_key_page_feature_mapping(self):
        """关键页面映射：类目→category / 价格→pricing / 变种、SKU→variants（有该功能时）。"""
        from aeclone.licensing import PLATFORM_FEATURES
        # 类目与属性 → category（仅限平台功能包里有 category 的平台）
        for pid in ("alibaba", "aliexpress", "amazon"):
            self.assertIn("category", PLATFORM_FEATURES[pid], pid)
            if pid == "alibaba":
                self.assertEqual(ALIBABA.page(30).feature, "category")
            elif pid == "aliexpress":
                self.assertEqual(ALIEXPRESS.page(2).feature, "category")
            else:
                self.assertEqual(PLATFORMS["amazon"].page(17).feature, "category",
                                 "Amazon 类目与属性必须是 category（修复：原来错为 listing）")
        # 价格页 → pricing（有 pricing 的平台）
        self.assertIn("pricing", PLATFORM_FEATURES["aliexpress"])
        self.assertEqual(ALIEXPRESS.page(6).feature, "pricing")
        # 变种/SKU → variants（有 variants 的平台）
        self.assertIn("variants", PLATFORM_FEATURES["aliexpress"])
        self.assertEqual(ALIEXPRESS.page(7).feature, "variants")
        # 没有对应功能包的平台保持 listing，避免出现永远无法解锁的页面
        for pid, index in (("amazon", 18), ("tiktok", 24), ("tiktok", 25),
                           ("tiktok", 35), ("shopee", 38), ("shopee", 39),
                           ("shopee", 40)):
            self.assertEqual(PLATFORMS[pid].page(index).feature, "listing",
                             f"{pid}#{index} 无对应功能包应保持 listing")

    def test_feature_audit_table(self):
        """输出页面→功能包审计表（测试日志里可直接看）。"""
        from aeclone.licensing import PLATFORM_FEATURES
        rows = ["平台 | 页面 | feature | 平台功能包包含"]
        for pid, p in PLATFORMS.items():
            allowed = set(PLATFORM_FEATURES.get(pid, []))
            for page in p.pages:
                rows.append(f"{pid} | #{page.index} {page.label} | {page.feature} | "
                            f"{'OK' if page.feature in allowed else 'MISSING'}")
        table = "\n".join(rows)
        print("\n===== 页面 feature 审计表 =====")
        print(table)
        self.assertNotIn("MISSING", table)


if __name__ == "__main__":
    unittest.main(verbosity=2)
