import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.integrations.amazon import normalize_product, extract_list, AmazonNotReady


class AmazonAdapterTests(unittest.TestCase):
    def test_normalize(self):
        p = normalize_product({"id": 1, "title": "Machine", "asin": "B01", "sku": "S1",
                               "price": "9.99", "quantity": 5, "dxmState": "draft",
                               "marketPlace": "US", "updateTime": 123})
        self.assertEqual(p["id"], "1")
        self.assertEqual(p["asin"], "B01")
        self.assertEqual(p["stock"], 5)
        self.assertEqual(p["status"], "draft")

    def test_extract_list(self):
        rows = extract_list({"page": {"list": [{"id": 2, "title": "T"}]}})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "T")

    def test_not_ready_errors(self):
        self.assertTrue(issubclass(AmazonNotReady, RuntimeError))


if __name__ == "__main__":
    unittest.main(verbosity=2)
