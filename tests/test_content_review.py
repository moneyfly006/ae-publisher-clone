"""Content verdicts must never replace HTML or silently pass failed checks."""
from copy import deepcopy
from types import SimpleNamespace as NS
import unittest
from test_audit_safety import extract


class ContentReviewTests(unittest.TestCase):
    def review(self, product, *, block=True, main=False, detail=True, text=False,
               first=0, last=0, verdicts=None, text_result=None):
        calls, steps = [], []
        verdicts = verdicts or {}
        def image(url, group):
            calls.append((url, group))
            result = verdicts.get(url, "PASS")
            if isinstance(result, Exception):
                raise result
            return result
        method = extract("src/aeclone/services/publisher.py", "_review_content", method=True,
                         namespace={"ImagePipeline": lambda cfg: NS(review=image),
                                    "TextPipeline": lambda cfg: NS(review_text=lambda html: text_result),
                                    "add_step": lambda *args: steps.append(args[1:])})
        cfg = NS(review_block_on_fail=block, review_main_img_enable=main,
                 review_detail_img_enable=detail, review_text_enable=text,
                 detail_review_first_count=first, detail_review_last_count=last)
        result = NS(ok=True, error="")
        proceed = method(NS(cfg=cfg), product, result)
        return proceed, result, calls, steps

    def test_second_detail_image_violation_blocks(self):
        p = {"detail_images": ["one", "two", "three"], "detail_html": "<p>Original</p>"}
        original = deepcopy(p)
        proceed, result, calls, steps = self.review(p, verdicts={"two": "FAIL|QR"})
        self.assertFalse(proceed)
        self.assertFalse(result.ok)
        self.assertEqual(calls, [("one", "detail"), ("two", "detail")])
        self.assertEqual(p, original)

    def test_selected_ranges_deduplicate_overlap(self):
        _, _, calls, _ = self.review({"detail_images": ["a", "b", "c"]}, first=2, last=2)
        self.assertEqual(calls, [("a", "detail"), ("b", "detail"), ("c", "detail")])

    def test_detail_switch_never_activates_main_review(self):
        _, _, calls, _ = self.review({"images": ["main"], "detail_images": ["detail"]})
        self.assertEqual(calls, [("detail", "detail")])

    def test_all_main_images_reviewed(self):
        _, _, calls, _ = self.review({"images": ["a", "b"]}, main=True, detail=False)
        self.assertEqual(calls, [("a", "main"), ("b", "main")])

    def test_pass_metadata_does_not_replace_html(self):
        p = {"detail_html": "<p>Product specifications</p>"}
        original = deepcopy(p)
        proceed, _, _, _ = self.review(p, text=True, text_result=NS(ok=True, value="PASS"))
        self.assertTrue(proceed)
        self.assertEqual(p, original)

    def test_failed_empty_and_unknown_verdicts_fail_closed_when_requested(self):
        for verdict in ("", "probably fine", RuntimeError("timeout")):
            with self.subTest(verdict=verdict):
                proceed, result, _, _ = self.review({"detail_images": ["a"]}, verdicts={"a": verdict})
                self.assertFalse(proceed)
                self.assertFalse(result.ok)
        proceed, result, _, _ = self.review({"detail_html": "original"}, text=True,
                                            text_result=NS(ok=False, error="quota"))
        self.assertFalse(proceed)
        self.assertIn("quota", result.error)

    def test_advisory_mode_records_failure_without_deleting_content(self):
        p = {"detail_images": ["a", "b"]}
        proceed, result, calls, steps = self.review(p, block=False, verdicts={"a": "FAIL|QR"})
        self.assertTrue(proceed)
        self.assertFalse(steps[0][1])
        self.assertEqual(len(calls), 2)
        self.assertEqual(p["detail_images"], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
