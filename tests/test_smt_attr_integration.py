#!/usr/bin/env python3
"""Publisher 类目属性自动补全接入回归（纯离线）。

验证 `Publisher._smt_attr_overrides`：
- 默认开启时，用类目属性定义 + 模板系统属性把缺失必填补成带 attrValueId 的 JSON；
- `cfg.smt_attr_autofill=False` 时完全不介入；
- 类目属性接口异常/空定义时不阻断保存（记录失败步骤、返回 {}）；
- 只返回 `aeopAeProductPropertys` 覆盖项，不改其它字段。
"""
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aeclone.services import publisher as PB  # noqa: E402

CATEGORY_ATTRS = [
    {"arrtNameId": "2", "nameZh": "品牌", "nameEn": "Brand Name", "required": 1,
     "values": json.dumps([{"names": {"en": "NONE", "zh": "NONE"}, "id": "201512802"}])},
    {"arrtNameId": "219", "nameZh": "产地（国家或地区）", "nameEn": "Origin", "required": 1,
     "values": json.dumps([{"names": {"en": "Mainland China", "zh": "中国大陆"},
                            "id": "9441741844"}])},
    {"arrtNameId": "400074001", "nameZh": "是否带电", "nameEn": "Charge Status", "required": 1,
     "values": json.dumps([{"names": {"en": "Yes", "zh": "是"}, "id": "24097551176"},
                           {"names": {"en": "No", "zh": "否"}, "id": "23399554847"}])},
    {"arrtNameId": "400000603", "nameZh": "高关注化学品", "nameEn": "High-concerned chemical",
     "required": 1,
     "values": json.dumps([{"names": {"en": "None", "zh": "无"}, "id": "23399591357"},
                           {"names": {"en": "Acetone", "zh": "丙酮"}, "id": "1981197668"}])},
]

TEMPLATE = {
    "aeopAeProductPropertys": json.dumps([
        {"attrName": "Brand Name", "attrNameId": 2, "attrValue": "NONE",
         "attrValueId": 201512802},
        {"attrName": "Origin", "attrNameId": 219, "attrValue": "Mainland China",
         "attrValueId": 9441741844},
        {"attrName": "Charge Status", "attrNameId": 400074001, "attrValue": "No",
         "attrValueId": 23399554847},
        {"attrName": "High-concerned chemical", "attrNameId": 400000603,
         "attrValue": "None", "attrValueId": 23399591357},
    ], ensure_ascii=False),
}

DRAFT = {
    "id": "220000000000000001",
    "categoryId": "100007204",
    "subject": "Underground horizontal directional drilling rig",
    "aeopAeProductPropertys": json.dumps([
        {"attrName": "Warranty", "attrValue": "1 Year"},
        {"attrName": "Place of Origin", "attrValue": "Henan, China"},
    ], ensure_ascii=False),
}


class _Dxm:
    def __init__(self, rows=None, raise_exc=False):
        self._rows = rows if rows is not None else CATEGORY_ATTRS
        self._raise = raise_exc
        self.calls = []

    def category_attributes(self, cid):
        self.calls.append(cid)
        if self._raise:
            raise RuntimeError("attributeList 500")
        return self._rows


class _Res:
    def __init__(self):
        self.steps = []


def _publisher(cfg=None, dxm=None):
    pub = PB.Publisher.__new__(PB.Publisher)
    pub.cfg = cfg or SimpleNamespace(smt_attr_autofill=True)
    pub.dxm = dxm if dxm is not None else _Dxm()
    return pub


class SmtAttrOverridesTests(unittest.TestCase):
    def setUp(self):
        self._orig_add_step = PB.add_step
        self.steps = []
        PB.add_step = lambda res, name, ok, detail="": self.steps.append(
            (name, bool(ok), str(detail)))

    def tearDown(self):
        PB.add_step = self._orig_add_step

    def test_autofill_fills_missing_required_with_ids(self):
        pub = _publisher()
        res = _Res()
        ov = pub._smt_attr_overrides(dict(DRAFT), TEMPLATE, res)
        self.assertIn("aeopAeProductPropertys", ov)
        props = json.loads(ov["aeopAeProductPropertys"])
        by_name = {str(p.get("attrName")): p for p in props}
        for name in ("Brand Name", "Origin", "Charge Status", "High-concerned chemical"):
            self.assertIn(name, by_name, f"缺少 {name}")
            self.assertTrue(str(by_name[name].get("attrValueId") or "").isdigit(),
                            f"{name} 未解析出 attrValueId：{by_name[name]}")
        self.assertEqual(ov["aeopAeProductPropertys"], json.dumps(props, ensure_ascii=False))
        self.assertTrue(any(n == "属性补全" and ok for n, ok, _ in self.steps))

    def test_disabled_by_cfg(self):
        pub = _publisher(SimpleNamespace(smt_attr_autofill=False))
        self.assertEqual(pub._smt_attr_overrides(dict(DRAFT), TEMPLATE, _Res()), {})
        self.assertEqual(pub.dxm.calls, [], "关闭时不应请求类目属性")

    def test_attribute_api_error_does_not_block(self):
        pub = _publisher(dxm=_Dxm(raise_exc=True))
        self.assertEqual(pub._smt_attr_overrides(dict(DRAFT), TEMPLATE, _Res()), {})
        self.assertTrue(any(n == "属性补全" and not ok for n, ok, _ in self.steps))

    def test_empty_definitions_no_override(self):
        pub = _publisher(dxm=_Dxm(rows=[]))
        self.assertEqual(pub._smt_attr_overrides(dict(DRAFT), TEMPLATE, _Res()), {})

    def test_missing_category_no_network(self):
        pub = _publisher()
        self.assertEqual(pub._smt_attr_overrides({"id": "x", "categoryId": "0"},
                                                 TEMPLATE, _Res()), {})
        self.assertEqual(pub.dxm.calls, [])

    def test_only_returns_attribute_override_key(self):
        ov = _publisher()._smt_attr_overrides(dict(DRAFT), None, _Res())
        self.assertTrue(set(ov) <= {"aeopAeProductPropertys"}, ov.keys())


if __name__ == "__main__":
    unittest.main(verbosity=2)
