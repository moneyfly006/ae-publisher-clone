from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
import json
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.services.product_data import hydrate_product
from aeclone.services.sku_recovery import recover_skus, apply_sku_repair
from aeclone.services.api_save import (build_ai_overrides, build_save_payload,
                                       validate_sku_combinations)


def detail(broken=False):
    return dict(id="product", shopId="shop", categoryId="123", aeopAeProductSKUs=[
        dict(skuCode=code, id=f"id-{value}", skuPrice=str(10 + i), ipmSkuStock=i + 3,
             aeopSKUProperty=[dict(skuPropertyId=1, propertyValueId=value)])
        for i, (code, value) in enumerate((("A", 1), ("B", 1 if broken else 2)))])


class RecoveryTests(unittest.TestCase):
    def test_complete_same_product_backup_restores_specs_without_losing_stock_or_prices(self):
        raw = detail(True)
        raw["smtProductSkuList"] = list(reversed(detail()["aeopAeProductSKUs"]))
        before = deepcopy(raw)
        with tempfile.TemporaryDirectory() as folder:
            product, note = recover_skus(hydrate_product({"id":"product"}, raw), directory=folder)
        self.assertIn("恢复 2 个", note)
        self.assertEqual(raw, before)
        rows = product["_source_sku_rows"]
        self.assertEqual([r["aeopSKUProperty"][0]["propertyValueId"] for r in rows], [1, 2])
        self.assertEqual([r["skuPrice"] for r in rows], ["10", "11"])
        self.assertEqual([r["ipmSkuStock"] for r in rows], [3, 4])

    def test_saved_verified_snapshot_is_reapplied_after_target_refetch(self):
        with tempfile.TemporaryDirectory() as folder:
            recover_skus(hydrate_product({"id":"product"}, detail()), directory=folder)
            product, _ = recover_skus(hydrate_product({"id":"product"}, detail(True)), directory=folder)
        target = detail(True)
        target["aeopAeProductSKUs"][1].update(skuPrice="90", ipmSkuStock=8)
        repaired = apply_sku_repair(target, product)
        cfg = NS(stock_quantity="", write_price_to_product=False)
        output = build_ai_overrides(repaired, product, cfg)
        payload = build_save_payload(repaired, None, overrides=output)
        rows = json.loads(payload["aeopAeProductSKUs"])
        self.assertEqual([r["aeopSKUProperty"][0]["propertyValueId"] for r in rows], [1, 2])
        self.assertEqual(rows[1]["skuPrice"], "90")
        self.assertEqual(rows[1]["ipmSkuStock"], 8)

    def test_refreshed_authoritative_detail_is_used(self):
        client = NS(_get=lambda *a: {"data":{"product":detail()}})
        with tempfile.TemporaryDirectory() as folder:
            product, note = recover_skus(hydrate_product({"id":"product"}, detail(True)), client, folder)
        self.assertIn("重新读取", note)
        self.assertEqual(product["_source_sku_rows"][1]["id"], "id-2")

    def test_no_source_merges_duplicate_variants_as_last_resort(self):
        raw = detail(True); before = deepcopy(raw)
        with tempfile.TemporaryDirectory() as folder:
            product, note = recover_skus(hydrate_product({"id":"product"}, raw), directory=folder)
        self.assertIn("自动合并", note)
        rows = product["_source_sku_rows"]
        self.assertLess(len(rows), len(before["aeopAeProductSKUs"]))
        self.assertEqual(raw, before)                    # 原始数据不被原地修改
        validate_sku_combinations(rows)                  # 合并后组合必须唯一

    def test_partial_wrong_product_or_ambiguous_sources_are_rejected(self):
        for change in (lambda p:p.update(id="other"), lambda p:p.update(categoryId="other"),
                       lambda p:p["aeopAeProductSKUs"].pop(),
                       lambda p:p["aeopAeProductSKUs"][1].update(skuCode="A")):
            valid = detail(); change(valid)
            client = NS(_get=lambda *a: {"data":{"product":valid}})
            with tempfile.TemporaryDirectory() as folder:
                product, note = recover_skus(hydrate_product({"id":"product"}, detail(True)), client, folder)
            # 错误来源必须被拒绝；最终只能走“合并重复变种”的兜底
            self.assertIn("自动合并", note)
            validate_sku_combinations(product["_source_sku_rows"])

    def test_cross_source_spec_difference_is_repaired_not_rejected(self):
        """task-33：采集箱 raw 与详情 target 来源不同，不得误报「规格发生变化」。"""
        raw = detail(True)
        raw["shopId"] = ""          # 采集箱 raw 常常没有店铺/类目
        raw["categoryId"] = ""
        raw["smtProductSkuList"] = detail()["aeopAeProductSKUs"]
        with tempfile.TemporaryDirectory() as folder:
            product, _ = recover_skus(hydrate_product({"id": "product"}, raw),
                                      directory=folder)
        target = detail(True)
        for row in target["aeopAeProductSKUs"]:
            row["aeopSKUProperty"][0]["propertyValueId"] = 3   # 与 raw 不同的规格值
        repaired = apply_sku_repair(target, deepcopy(product))
        rows = repaired["aeopAeProductSKUs"]
        validate_sku_combinations(rows)
        self.assertEqual([r["aeopSKUProperty"][0]["propertyValueId"] for r in rows], [1, 2])

    def test_real_context_mismatch_is_still_rejected(self):
        raw = detail(True)
        raw["smtProductSkuList"] = detail()["aeopAeProductSKUs"]
        with tempfile.TemporaryDirectory() as folder:
            product, _ = recover_skus(hydrate_product({"id": "product"}, raw),
                                      directory=folder)
        other = detail(True)
        other["id"] = "other-product"
        with self.assertRaisesRegex(ValueError, "不一致"):
            apply_sku_repair(other, deepcopy(product))

    def test_broken_repair_is_cleared_with_actionable_error(self):
        raw = detail(True)
        raw["smtProductSkuList"] = detail()["aeopAeProductSKUs"]
        with tempfile.TemporaryDirectory() as folder:
            product, _ = recover_skus(hydrate_product({"id": "product"}, raw),
                                      directory=folder)
        broken = deepcopy(product)
        broken["_sku_spec_repair"] = {
            "before": {"id": "product", "aeopAeProductSKUs": detail(True)["aeopAeProductSKUs"]},
            "source": {**detail(True), "aeopAeProductSKUs": [
                dict(detail(True)["aeopAeProductSKUs"][0], skuCode="X"),
                dict(detail(True)["aeopAeProductSKUs"][1], skuCode="Y")]},
        }
        with self.assertRaisesRegex(ValueError, "已清除"):
            apply_sku_repair(detail(True), broken)
        self.assertNotIn("_sku_spec_repair", broken)

    def test_merged_repair_applies_verified_rows(self):
        raw = detail(True)
        raw["smtProductSkuList"] = detail()["aeopAeProductSKUs"]
        with tempfile.TemporaryDirectory() as folder:
            product, _ = recover_skus(hydrate_product({"id": "product"}, raw),
                                      directory=folder)
        merged = deepcopy(product)
        source_rows = [dict(detail()["aeopAeProductSKUs"][0])]
        merged["_sku_spec_repair"] = {
            "before": {"id": "product", "aeopAeProductSKUs": detail(True)["aeopAeProductSKUs"]},
            "source": {"id": "product", "shopId": "shop", "categoryId": "123",
                       "aeopAeProductSKUs": source_rows},
            "merged": True,
        }
        fixed = apply_sku_repair(detail(True), merged)
        self.assertEqual([r["skuCode"] for r in fixed["aeopAeProductSKUs"]], ["A"])
        validate_sku_combinations(fixed["aeopAeProductSKUs"])


if __name__ == "__main__": unittest.main(verbosity=2)
