"""速卖通发布失败自动修复：属性重名 + SKU 规格不在类目候选。

真实店铺（信息已脱敏）2026-11-27 的 16 个发布失败商品只有两类平台错误：
1. ``isv.7CHK_CUSTOM_PROP_NAME_DUPLICATE_CAT_PROP``：采集商品把 Model Number /
   Weight / Material / Type 等系统属性当成「自定义属性」，与类目系统属性重名；
2. ``isv.7CHK_BASIC_ONEOF, sku::Input value not in candidate list``：SKU 规格值 id
   属于旧类目/其它类目（如 Color=Brown 350686 当前类目没有），或带着类目未开通的
   插件库规格（Voltage/Plug）。

本文件用真实接口结构的离线快照验证修复逻辑（不连网、不写平台）。
"""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone.services import smt_attributes as SA  # noqa: E402
from aeclone.services.publisher import Publisher  # noqa: E402


def _raw_attrs():
    return [
        {"arrtNameId": "2", "nameEn": "Brand Name", "required": True, "sku": False,
         "values": json.dumps([{"id": "201512802", "names": {"en": "NONE"}}])},
        {"arrtNameId": "219", "nameEn": "Origin", "required": True, "sku": False,
         "values": json.dumps([{"id": "9441741844", "names": {"en": "China"}}])},
        # 自由文本系统属性：采集数据里最常被当成自定义属性
        {"arrtNameId": "3", "nameEn": "Model Number", "required": False, "sku": False, "values": "[]"},
        {"arrtNameId": "140", "nameEn": "Weight", "required": False, "sku": False, "values": "[]"},
        {"arrtNameId": "14", "nameEn": "Color", "required": True, "sku": True,
         "values": json.dumps([
             {"id": "100018786", "names": {"en": "Clear", "zh": "透明色"}},
             {"id": "365458", "names": {"en": "Brown", "zh": "棕色"}},
             {"id": "10", "names": {"en": "Red", "zh": "红色"}},
         ])},
        {"arrtNameId": "200007763", "nameEn": "Ships From", "required": False, "sku": True,
         "values": json.dumps([{"id": "201336100", "names": {"en": "China Mainland"}}])},
    ]


def _product():
    return {
        "id": "220114600917617842",
        "categoryId": "200218144",
        "subject": "Inflatable Transparent Bubble House Tent – PVC Dome",
        "aeopAeProductPropertys": json.dumps([
            {"attrName": "Model Number", "attrValue": "BQ"},
            {"attrName": "Weight", "attrValue": "10000 KG2204623lb"},
            {"attrName": "Inflatable", "attrValue": "Yes"},
            {"attrName": "Brand Name", "attrValueId": "201512802", "attrNameId": "2"},
            {"attrName": "Origin", "attrValueId": "9441741844", "attrNameId": "219"},
        ], ensure_ascii=False),
        "aeopAeProductSKUs": json.dumps([{
            "id": "14:350686;400079803:1983474624",
            "skuPrice": "100",
            "ipmSkuStock": "10",
            "aeopSKUProperty": [
                {"skuPropertyId": 14, "propertyValueId": 350686,
                 "propertyValueDefinitionName": None},
                {"skuPropertyId": 400079803, "propertyValueId": 1983474624,
                 "propertyValueDefinitionName": None,
                 "plug_parent_property_id": 400081003,
                 "plug_parent_value_id": 24681606136},
            ],
        }], ensure_ascii=False),
    }


class AttributeRepairTests(unittest.TestCase):
    def test_category_duplicate_custom_attributes_are_dropped(self):
        rep = SA.repair_product_attributes(_product(), _raw_attrs())
        props = json.loads(rep["payload"]["aeopAeProductPropertys"])
        custom = [x for x in props if not x.get("attrNameId")]
        names = {SA.normalize_name(x.get("attrName")) for x in custom}
        self.assertNotIn("model number", names, custom)
        self.assertNotIn("weight", names, custom)
        self.assertIn("inflatable", names, "无关的自定义属性应保留")
        system_ids = {str(x.get("attrNameId")) for x in props if x.get("attrNameId")}
        self.assertIn("2", system_ids)
        self.assertIn("219", system_ids)


class SkuRepairTests(unittest.TestCase):
    def test_invalid_color_mapped_by_title_and_plug_dropped(self):
        skus = json.loads(_product()["aeopAeProductSKUs"])
        fixed, changes = SA.repair_sku_compatibility(
            _raw_attrs(), skus, title=_product()["subject"],
            attribute_values=["BQ"], has_plug=False)
        props = fixed[0]["aeopSKUProperty"]
        self.assertEqual(len(props), 1, "类目未开通插件库时插件规格应移除")
        color = props[0]
        self.assertEqual(str(color["skuPropertyId"]), "14")
        self.assertEqual(str(color["propertyValueId"]), "100018786", "透明标题应匹配 Clear")
        self.assertEqual(color.get("propertyValueDefinitionName"), "Clear")
        self.assertEqual(fixed[0]["id"], "14:100018786")
        self.assertTrue(any(c.get("action") == "replace" for c in changes), changes)
        self.assertTrue(any(c.get("action") == "drop" for c in changes), changes)

    def test_plug_kept_when_category_supports_plug_library(self):
        skus = json.loads(_product()["aeopAeProductSKUs"])
        fixed, _ = SA.repair_sku_compatibility(
            _raw_attrs(), skus, title=_product()["subject"],
            attribute_values=["BQ"], has_plug=True)
        self.assertEqual(len(fixed[0]["aeopSKUProperty"]), 2, "类目支持插件库时保留插件规格")

    def test_non_required_invalid_value_dropped(self):
        raw = _raw_attrs()
        skus = [{"aeopSKUProperty": [
            {"skuPropertyId": 200007763, "propertyValueId": 999999999},
            {"skuPropertyId": 14, "propertyValueId": 100018786},
        ]}]
        fixed, changes = SA.repair_sku_compatibility(raw, skus, has_plug=False)
        self.assertEqual(len(fixed[0]["aeopSKUProperty"]), 1, fixed)
        self.assertEqual(str(fixed[0]["aeopSKUProperty"][0]["skuPropertyId"]), "14")


class RequiredAttributeTests(unittest.TestCase):
    def _attrs(self):
        raw = _raw_attrs()
        # 真实类目 200218144：Color / Ballon Size 为必填 SKU 规格
        raw.append({"arrtNameId": "200324261", "nameEn": "Ballon Size", "required": True, "sku": True,
                    "values": json.dumps([{"id": "4", "names": {"en": "Other", "zh": "其他"}},
                                          {"id": "201109510", "names": {"en": "10inch"}}])})
        raw.append({"arrtNameId": "10", "nameEn": "Material", "required": False, "sku": False,
                    "values": json.dumps([{"id": "439", "names": {"en": "PVC"}},
                                          {"id": "124", "names": {"en": "Plastic"}}])})
        return raw

    def test_required_system_attrs_filled_and_custom_material_removed(self):
        product = _product()
        props = json.loads(product["aeopAeProductPropertys"])
        props += [
            {"attrName": "Material", "attrValue": "PVC"},          # 自定义属性与系统属性重名
            {"attrNameId": "10", "attrValueId": "439", "attrName": "Material"},  # 系统 Material
        ]
        product["aeopAeProductPropertys"] = json.dumps(props, ensure_ascii=False)
        rep = SA.repair_product_attributes(product, self._attrs())
        out = json.loads(rep["payload"]["aeopAeProductPropertys"])
        custom_names = {SA.normalize_name(x.get("attrName")) for x in out if not x.get("attrNameId")}
        self.assertNotIn("material", custom_names, out)
        by_id = {str(x.get("attrNameId")): x for x in out if x.get("attrNameId")}
        self.assertIn("14", by_id, "必填 Color 系统属性应补上")
        self.assertIn("200324261", by_id, "必填 Ballon Size 系统属性应补上")
        self.assertEqual(str(by_id["14"].get("attrValueId")), "100018786", "透明标题应补 Clear")

    def test_required_sku_attrs_added_to_sku_rows(self):
        skus = json.loads(_product()["aeopAeProductSKUs"])
        fixed, changes = SA.repair_sku_compatibility(
            self._attrs(), skus, title=_product()["subject"], has_plug=False)
        props = {(str(p.get("skuPropertyId")), str(p.get("propertyValueId")))
                 for p in fixed[0]["aeopSKUProperty"]}
        self.assertIn(("14", "100018786"), props, fixed)
        self.assertIn(("200324261", "4"), props, "必填 Ballon Size 应补到 SKU")
        self.assertTrue(any(c.get("action") == "add" for c in changes), changes)


class PriceFallbackTests(unittest.TestCase):
    def test_missing_sku_price_filled_from_product_price(self):
        from aeclone.services.api_save import build_save_payload
        product = _product()
        product["productMinPrice"] = "2369.46"
        product["productMaxPrice"] = "2369.46"
        skus = json.loads(product["aeopAeProductSKUs"])
        for s_ in skus:
            s_.pop("skuPrice", None)
        product["aeopAeProductSKUs"] = json.dumps(skus, ensure_ascii=False)
        cfg = SimpleNamespace(auto_detect_category=True, custom_attr_drop="")
        payload = build_save_payload(product, None, action="save_only", cfg=cfg)
        out = json.loads(payload["aeopAeProductSKUs"])
        self.assertTrue(all(str(x.get("skuPrice") or "").strip() for x in out), out)
        self.assertEqual(str(out[0]["skuPrice"]), "2369.46")
        self.assertEqual(str(payload.get("productMinPrice")), "2369.46")


class _FakeDxm:
    def category_attributes(self, cid):
        return _raw_attrs()

    def category_detail(self, cid):
        return {"hasPlugAttribute": 0}


class PublisherRepairTests(unittest.TestCase):
    def test_repair_sku_compat_step_and_payload(self):
        p = object.__new__(Publisher)
        p.dxm = _FakeDxm()
        target = _product()
        res = SimpleNamespace(steps=[])
        out = p._repair_sku_compat(target, res)
        self.assertNotEqual(out.get("aeopAeProductSKUs"), target.get("aeopAeProductSKUs"))
        fixed = json.loads(out["aeopAeProductSKUs"])
        self.assertEqual(str(fixed[0]["aeopSKUProperty"][0]["propertyValueId"]), "100018786")
        self.assertTrue(any(getattr(s, "name", "") == "规格修复" for s in res.steps), res.steps)


if __name__ == "__main__":
    unittest.main()
