"""Real filesystem caching in temporary folders, fake HTTP only."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from test_audit_safety import extract


class DownloadCacheTests(unittest.TestCase):
    def test_same_basename_different_urls_never_mix_product_images(self):
        calls = []
        def get(url, timeout):
            calls.append(url)
            return SimpleNamespace(content=url.encode(), raise_for_status=lambda: None)
        download = extract("src/aeclone/services/image_pipeline.py", "_download", method=True,
                           namespace={"Path": Path, "requests": SimpleNamespace(get=get)})
        with tempfile.TemporaryDirectory() as root:
            fake = SimpleNamespace(workdir=Path(root))
            a = download(fake, "https://a.invalid/image.jpg?id=1")
            b = download(fake, "https://b.invalid/image.jpg?id=2")
            self.assertNotEqual(a, b)
            self.assertNotEqual(a.read_bytes(), b.read_bytes())
            self.assertEqual(download(fake, calls[0]), a)
            self.assertEqual(len(calls), 2)
            self.assertEqual(len(list(Path(root).iterdir())), 2)

    def test_empty_download_not_cached(self):
        download = extract("src/aeclone/services/image_pipeline.py", "_download", method=True,
                           namespace={"Path": Path, "requests": SimpleNamespace(
                               get=lambda *a, **kw: SimpleNamespace(content=b"", raise_for_status=lambda: None))})
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                download(SimpleNamespace(workdir=Path(root)), "https://a.invalid/image.png")
            self.assertEqual(list(Path(root).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
