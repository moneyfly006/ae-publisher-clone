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


if __name__ == "__main__":
    unittest.main(verbosity=2)
