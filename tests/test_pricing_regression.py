"""Load the pure pricing module without importing application services."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

path = Path(__file__).resolve().parents[1] / "src/aeclone/services/pricing.py"
spec = importlib.util.spec_from_file_location("offline_pricing", path)
pricing = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pricing
spec.loader.exec_module(pricing)


class PricingTests(unittest.TestCase):
    def test_commission_recovers_cost_instead_of_lowering_price(self):
        zero = pricing.calc_with_commission(100, 1, 0, 0, 0)
        charged = pricing.calc_with_commission(100, 1, 0, .1, 0)
        self.assertEqual(zero.final, 100)
        self.assertEqual(charged.final, 111.11)
        self.assertAlmostEqual(charged.final * .9, 100, places=2)

    def test_invalid_commission_rejected(self):
        for rate in (-.01, 1, 1.1, float("nan"), float("inf")):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                pricing.calc_with_commission(100, 1, 0, rate, 0)

    def test_free_shipping_tiers_keep_profit(self):
        cfg = SimpleNamespace(price_mode="free_shipping", sales_profit=20,
                              default_express_price=10, tuoguan_price_tiered_enabled=False,
                              tuoguan_price_tiers=[{"min": 0, "max": 200, "multiplier": 1}])
        plain = pricing.compute_price(cfg, 100)
        cfg.tuoguan_price_tiered_enabled = True
        tiered = pricing.compute_price(cfg, 100)
        self.assertEqual(plain.final, 132)
        self.assertEqual(tiered.final, plain.final)
        cfg.tuoguan_price_tiers[0]["multiplier"] = 2
        self.assertEqual(pricing.compute_price(cfg, 100).final, 252)

    def test_multiplier_and_regional_wholesale_preserved(self):
        cfg = SimpleNamespace(price_mode="multiplier", price_multiplier=2, price_fixed_add=5,
                              regional_pricing=True, adjust_price_template_detail={"USD": .14},
                              wholesale_enable=True, wholesale_tiers=[{"min_qty": 10, "price": 180}])
        result = pricing.compute_price(cfg, 100)
        self.assertEqual(result.final, 205)
        self.assertEqual(result.regional, {"USD": 28.7})
        self.assertEqual(result.wholesale, [{"min_qty": 10, "price": 180}])


if __name__ == "__main__":
    unittest.main()
