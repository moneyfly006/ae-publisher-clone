"""Alibaba 保存载荷（alibabaSave.txt zip）离线单测，不联网。"""
import io
import json
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.integrations.alibaba import AlibabaClient, SAVE_NAME


class AlibabaPayloadTests(unittest.TestCase):
    def test_build_save_dict_keeps_strings(self):
        product = {
            "id": "123", "shopId": "9", "categoryId": "202107201",
            "fullCid": "18,100005657,202107201", "subject": "Test Machine",
            "keywords": "machine, industrial", "attributes": [{"attribute_id": "1"}],
            "detailImage": [{"url": "https://x/1.jpg"}],
            "bulkDiscountPrices": [{"start_quantity": 1, "price": 10.5}],
            "wholesaleTrade": {"min_order_quantity": 1, "weight": "20"},
            "productSku": {"attributes": [], "exclude_skus": [], "special_skus": []},
            "mainImage": "https://x/a.jpg,https://x/b.jpg",
            "priceType": "ladder_price",
        }
        d = AlibabaClient.build_save_dict(product)
        self.assertEqual(d["subject"], "Test Machine")
        self.assertEqual(d["mainImage"], "https://x/a.jpg,https://x/b.jpg")
        self.assertEqual(d["draftImgUrl"], "https://x/a.jpg|https://x/b.jpg")
        for key in ("attributes", "detailImage", "bulkDiscountPrices",
                    "wholesaleTrade", "productSku"):
            self.assertIsInstance(d[key], str)
            json.loads(d[key])
        self.assertEqual(d["op"], "1")

    def test_zip_contains_alibaba_save_txt(self):
        d = AlibabaClient.build_save_dict({"id": "1", "subject": "s"})
        raw = AlibabaClient.zip_payload(d)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            self.assertIn(SAVE_NAME, z.namelist())
            data = json.loads(z.read(SAVE_NAME).decode("utf-8"))
        self.assertEqual(data["subject"], "s")




class AlibabaRequiredTests(unittest.TestCase):
    def test_autofill_uses_preset_then_other(self):
        from aeclone.services.alibaba_payload import autofill_required_attributes
        attrs, filled, unresolved = autofill_required_attributes(
            {"attributes": [{"attribute_name": "Place of Origin", "value_name": "China"}]},
            [{"attrNameId": "1", "nameEn": "Place of Origin", "required": 1, "values": []},
             {"attrNameId": "2", "nameEn": "Color", "required": 1,
              "values": [{"id": "1", "name_en": "White"}, {"id": "2", "name_en": "Other"}]},
             {"attrNameId": "3", "nameEn": "Material", "required": 1,
              "values": [{"id": "5", "name_en": "Other"}]},
             {"attrNameId": "4", "nameEn": "Feature", "required": 0, "values": []}],
            {"alibaba_attr_presets": '{"Color": "Blue"}'})
        names = {a["attribute_name"]: a.get("value_name") for a in attrs}
        self.assertEqual(names["Color"], "Blue")
        self.assertEqual(names["Material"], "Other")
        self.assertIn("Color=Blue", filled)
        self.assertIn("Material=Other", filled)
        self.assertEqual(unresolved, [])

    def test_validate_reports_missing_required_and_bad_tiers(self):
        from aeclone.services.alibaba_payload import validate_alibaba_save
        save = {"subject": "A", "keywords": "a, b", "mainImage": "",
                "bulkDiscountPrices": '[{"start_quantity": 5, "price": 1},{"start_quantity": 1, "price": 2}]',
                "wholesaleTrade": '{"min_order_quantity": 0, "package_size": ""}',
                "attributes": "[]", "detailImage": "[]", "textDesc": ""}
        issues = validate_alibaba_save({}, save, None,
                                       [{"nameEn": "Color"}],
                                       [{"fieldId": "textDesc"}, {"fieldId": "detailImage"}])
        text = "；".join(issues)
        self.assertIn("关键词", text)
        self.assertIn("主图为空", text)
        self.assertIn("阶梯价数量区间", text)
        self.assertIn("MOQ", text)
        self.assertIn("必填属性缺失：Color", text)
        self.assertIn("textDesc", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
