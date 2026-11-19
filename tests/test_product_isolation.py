"""Offline behavioral regressions; no customer config or network imports."""
import os as _os
_os.environ.setdefault("AURORA_SKIP_LICENSE", "1")
from copy import deepcopy
from types import SimpleNamespace
import unittest

from test_audit_safety import extract


class ProductIsolationTests(unittest.TestCase):
    def test_normalization_and_image_edits_keep_original_snapshot(self):
        normalize = extract("src/aeclone/services/publisher.py", "_normalize", method=True)
        original = {"title": " Product ", "images": ["original"],
                    "skus": [{"sku": "A", "stock": 3, "attributes": {"Color": "Red"}}]}
        snapshot = deepcopy(original)
        cfg = SimpleNamespace(custom_sku_prefix="NEW", stock_quantity="9", max_sku_count=256)
        result = normalize(SimpleNamespace(cfg=cfg), original)
        result["images"][0] = "edited"
        result["skus"][0]["attributes"]["Color"] = "Blue"
        self.assertEqual(original, snapshot)
        self.assertNotEqual(result["images"], original["images"])
        self.assertEqual(result["skus"][0]["sku"], "NEW-A")
        self.assertEqual(result["skus"][0]["stock"], "9")

    def test_unsupported_multi_sku_fails_before_service_access(self):
        from unittest.mock import patch
        from dataclasses import dataclass, field
        @dataclass
        class Result:
            product_id: str
            ok: bool
            steps: list = field(default_factory=list)
            error: str = ""
        run = extract("src/aeclone/services/publisher.py", "run", method=True,
                      namespace={"PublishResult": Result, "Step": lambda *args: args})
        fake = SimpleNamespace(cfg=SimpleNamespace(write_sku_codes_to_product=True))
        with patch.dict("os.environ", {}, clear=True):
            result = run(fake, {"id": "offline", "skus": [{"sku": "A"}, {"sku": "B"}]}, dry_run=False)
        self.assertFalse(result.ok)
        self.assertIn("未调用 AI", result.error)

    def test_long_prompts_do_not_share_cached_result(self):
        edit = extract("src/aeclone/services/publisher.py", "_edit_with_prompt", method=True)
        cache, calls = {}, []
        def generate(path, prompt):
            calls.append(prompt)
            return "result-" + str(len(calls))
        fake = SimpleNamespace(
            _cache_get=lambda url, action, model: cache.get((url, action, model)),
            _cache_put=lambda url, action, out, model: cache.update({(url, action, model): out}),
            _img_model=lambda: "model", _fetch=lambda url: url,
            _img_service=lambda: SimpleNamespace(edit=generate))
        first, second = "x" * 120 + " red", "x" * 120 + " blue"
        self.assertNotEqual(edit(fake, "source", first), edit(fake, "source", second))
        self.assertEqual(edit(fake, "source", first), "result-1")
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
