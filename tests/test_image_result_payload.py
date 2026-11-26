"""图片接口返回兼容：url 或 b64_json。"""
import base64
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from aeclone.api import _image_item_url  # noqa: E402


class ImageResultPayloadTests(unittest.TestCase):
    def test_url_passthrough(self):
        self.assertEqual(_image_item_url({"url": "https://x/y.png"}),
                         "https://x/y.png")

    def test_b64_uploaded_to_host(self):
        root = Path(tempfile.mkdtemp())
        path = root / "x.png"
        Image.new("RGB", (8, 8), "red").save(path)
        b64 = base64.b64encode(path.read_bytes()).decode()
        with patch("aeclone.services.hosting.upload",
                   return_value="https://img.example/x.png") as upload:
            out = _image_item_url({"b64_json": b64})
        self.assertEqual(out, "https://img.example/x.png")
        upload.assert_called_once()

    def test_empty_payload(self):
        self.assertEqual(_image_item_url({}), "")


if __name__ == "__main__":
    unittest.main()
