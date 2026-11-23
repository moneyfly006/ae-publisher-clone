"""Pure-module regression coverage: no package startup, GUI, or configuration."""
import copy
import importlib.util
from pathlib import Path
import sys
import unittest


_SPEC = importlib.util.spec_from_file_location(
    "_variants_regression_target",
    Path(__file__).resolve().parents[1] / "src/aeclone/services/variants.py",
)
variants = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = variants
_SPEC.loader.exec_module(variants)


def grid(colors=2, sizes=2):
    return [
        {"sku": f"{c}-{s}", "attributes": {"color": str(c), "size": str(s)}}
        for c in range(colors) for s in range(sizes)
    ]


class VariantsRegressionTests(unittest.TestCase):
    def test_two_by_two_keeps_complete_two_rows(self):
        rows = grid()
        result = variants.limit_skus(rows, 3)
        self.assertEqual(result, rows[:2])
        self.assertEqual(variants.check_consistency(result, [], require_complete=True), [])
        self.assertTrue(all(a is b for a, b in zip(result, rows)))

    def test_twenty_by_forty_keeps_first_twelve_sizes(self):
        rows = grid(20, 40)
        before = copy.deepcopy(rows)
        result = variants.limit_skus(rows)
        self.assertEqual(len(result), 240)
        self.assertEqual(result, [row for row in rows if int(row["attributes"]["size"]) < 12])
        self.assertEqual(rows, before)
        self.assertEqual(variants.check_consistency(result, [], require_complete=True), [])

    def test_disabled_and_under_limit_are_identity_noops(self):
        rows = [{"sku": "unknown"}, {"attributes": "malformed"}]
        for limit in (0, -1, None, 2, 256):
            with self.subTest(limit=limit):
                self.assertIs(variants.limit_skus(rows, limit), rows)
        self.assertEqual(variants.limit_skus([], 1), [])

    def test_cascade_and_order_and_no_mutation(self):
        rows = list(reversed(grid(3, 3)))
        before = copy.deepcopy(rows)
        result = variants.limit_skus(rows, 1)
        self.assertEqual(result, [rows[0]])
        self.assertIs(result[0], rows[0])
        self.assertEqual(rows, before)

    def test_sparse_catalog_default_valid_strict_incomplete(self):
        for rows in (grid()[:3], [grid()[0], grid()[3]]):
            self.assertEqual(variants.check_consistency(rows, ["color", "size"]), [])
            issues = variants.check_consistency(rows, [], require_complete=True)
            self.assertTrue(any("组合不完整" in issue.reason for issue in issues))

    def test_duplicate_does_not_mask_missing_combination(self):
        rows = grid()[:3] + [grid()[0]]
        reasons = [i.reason for i in variants.check_consistency(rows, [], require_complete=True)]
        self.assertTrue(any("重复" in reason for reason in reasons))
        self.assertTrue(any("组合不完整" in reason for reason in reasons))

    def test_zero_value_and_required_attributes(self):
        self.assertEqual(variants.check_consistency([{"prop": {"size": 0}}], ["size"]), [])
        self.assertTrue(any("缺少属性 size" == i.reason for i in
                            variants.check_consistency([{"prop": {"color": "red"}}], ["size"])))

    def test_standard_format_and_positional_compatibility(self):
        for field, convert in (
            ("aeopSKUProperty", lambda attrs: [
                {"skuPropertyId": key, "propertyValueId": value}
                for key, value in attrs.items()]),
            ("prop", lambda attrs: attrs),
            ("attributes", lambda attrs: list(attrs.values())),
        ):
            rows = [{field: convert(row["attributes"])} for row in grid()]
            with self.subTest(field=field):
                result = variants.limit_skus(rows, 3)
                self.assertEqual(len(result), 2)
                self.assertEqual(variants.check_consistency(result, [], require_complete=True), [])

    def test_malformed_formats_report_issues_and_refuse_clipping(self):
        bad_rows = [None, "row", {"attributes": "red"}, {"attributes": [None]},
                    {"attributes": {"size": []}}, {"attributes": {"size": " "}},
                    {"attributes": {"size": float("nan")}},
                    {"aeopSKUProperty": "bad"}, {"aeopSKUProperty": [None]},
                    {"aeopSKUProperty": [{"skuPropertyId": 1}]},
                    {"aeopSKUProperty": [{"skuPropertyId": 1, "propertyValueId": 2},
                                         {"skuPropertyId": "1", "propertyValueId": 3}]},
                    {"attributes": {"size": 1}, "prop": {"size": 2}}]
        for bad in bad_rows:
            with self.subTest(bad=bad):
                self.assertTrue(variants.check_consistency([bad], []))
                with self.assertRaises(ValueError):
                    variants.limit_skus([bad, bad], 1)

    def test_unknown_mixed_and_irreducible_groups_refused(self):
        cases = [[{}, {}], [{"sku": "a"}, {"sku": "b"}],
                 [{"attributes": {"a": 1}}, {"attributes": {"b": 1}}],
                 [{"attributes": {"a": 1}}, {"attributes": [2]}],
                 [{"attributes": {"a": 1}}] * 2]
        for rows in cases:
            with self.subTest(rows=rows):
                before = copy.deepcopy(rows)
                with self.assertRaises(ValueError):
                    variants.limit_skus(rows, 1)
                self.assertEqual(rows, before)
        self.assertTrue(variants.check_consistency(cases[2], []))
        self.assertTrue(variants.check_consistency(cases[3], []))

    def test_aeop_named_property_metadata_is_ignored(self):
        """真实采集形态：aeopSKUProperty 用属性名做 key，并带 skuImage/extraImages 元数据。

        店小秘/速卖通采集数据（Yugong 钻机）曾报：
        SKU 一致性检查失败：SKU-001: 属性键和值必须是非空标量
        原因是把 skuImage=None 当成了属性值。
        """
        sku = {"sku": "SKU-001", "aeopSKUProperty": [
            {"Model Number": "YG-150", "Power (W)": "60KW(80.46hp)",
             "skuImage": None, "extraImages": ""}]}
        self.assertEqual(variants.check_consistency([sku], []), [])
        # 标准形态的缺值仍然要报错（不能把真实缺参静默放过）
        bad = {"sku": "SKU-002", "aeopSKUProperty": [{"skuPropertyId": 1}]}
        self.assertTrue(variants.check_consistency([bad], []))

    def test_real_sku_property_metadata_variants_are_ignored(self):
        """真实采集形态（Yugong 钻机 SKU-001）：元数据键/空值/列表都不是规格维度。"""
        # 标准形态：aeopAeProductSKUs 里的标准条目带大量 null/字符串元数据
        standard = {"sku": "SKU-001", "aeopSKUProperty": [
            {"imageUrlList": None, "propertyValueDefinitionName": None,
             "propertyValueId": 350686, "skuImage": None, "skuPropertyId": 14,
             "parentPropertyId": None, "parentValueId": None,
             "plug_parent_property_id": None, "plug_parent_value_id": None},
            {"imageUrlList": None, "propertyValueDefinitionName": None,
             "propertyValueId": 1983474624, "skuImage": None,
             "skuPropertyId": 400079803, "parentPropertyId": None,
             "parentValueId": None, "plug_parent_property_id": 400081003,
             "plug_parent_value_id": 24681606136}]}
        self.assertEqual(variants.check_consistency([standard], []), [])
        # 命名形态：元数据值可能是列表/空串/None，不能当属性值
        named = {"sku": "SKU-001", "aeopSKUProperty": [
            {"Model Number": "YG-150", "Power (W)": "60KW(80.46hp)",
             "imageUrlList": ["https://x/y.jpg"], "skuImage": None,
             "extraImages": "", "propertyValueDefinitionName": None,
             "parentPropertyId": None}]}
        self.assertEqual(variants.check_consistency([named], []), [])
        # smtProductSkuList 形态（skuPropertyList 是字符串元数据，不是维度）
        smt = {"sku": "SKU-001",
               "skuPropertyList": "[{\"sku_property_id_long\":\"14\","
                                  "\"property_value_id_long\":\"350686\"}]",
               "aeopSKUProperty": None, "name": "棕色/220V-240V IL Plug"}
        self.assertEqual(variants.check_consistency([smt], []), [])
        # 真正缺值仍必须报错
        bad = {"sku": "SKU-002", "aeopSKUProperty": [{"skuPropertyId": 14,
                                                      "propertyValueId": None}]}
        self.assertTrue(variants.check_consistency([bad], []))

    def test_invalid_limits_have_clear_errors(self):
        for limit in (True, 1.5, "3"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                variants.limit_skus(grid(), limit)

    def test_normalization_detects_colliding_dimension_keys(self):
        self.assertTrue(variants.check_consistency([{"attributes": {1: "a", "1": "b"}}], []))


if __name__ == "__main__":
    unittest.main()
