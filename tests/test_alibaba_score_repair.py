"""Exercise the full score, repair, re-score and save path using simulated clients."""
import json
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from aeclone.services import alibaba_pipeline
from aeclone.services.collect_automation import run_collect
from aeclone.services.collect_store import get
from test_alibaba_pipeline import FakeClient, make_cfg
from test_alibaba_score import good_product


def source_product(**overrides):
    product = good_product()
    product.update(
        shopId="store-one", draftImgUrl=product["mainImage"].replace(",", "|"),
        description=product["textDesc"] + "<p>Color: White</p>",
        textDesc="Fiberglass container pool.", keywords="游泳池", mainImage=product["mainImage"].split(",")[0],
    )
    product.update(overrides)
    return product


class AlibabaScoreRepairTests(unittest.TestCase):
    def test_uses_exact_original_attribute_and_keeps_unverified_values_unset(self):
        product = source_product(attributes=[], textDesc="Color: White\nWeight: 50 kg")
        required = [
            {"nameEn": "Color", "attrNameId": "10", "required": 1, "values": [{"id": "11", "name_en": "White"}]},
            {"nameEn": "Weight", "attrNameId": "12", "required": 1, "values": [{"id": "13", "name_en": "50 kg"}]},
        ]
        client = FakeClient(product, required=required)
        result = alibaba_pipeline.run_alibaba_product(product["id"], make_cfg(), client=client, use_ai=False,
            dry_run=False, op="2", repair_score=True)
        self.assertIn("按原描述和类目候选补齐属性：Color", result.score_actions)
        self.assertTrue(any("Weight" in issue for issue in result.issues))
        self.assertFalse(client.saved)

    def test_batch_repairs_low_score_from_original_and_publishes_once(self):
        product = source_product()
        client = FakeClient(product, required=[])
        original_save = client.save_product
        calls = []

        def save(original, payload, op):
            calls.append((payload, op))
            result = original_save(original, payload, op)
            client.product["dxmState"] = "online"
            return result

        client.save_product = save
        cfg = make_cfg(alibaba_shop_id="store-one", alibaba_collect_op="2")
        with TemporaryDirectory() as folder, patch.dict("os.environ", {"AURORA_CONFIG_DIR": folder}), \
             patch.object(alibaba_pipeline, "forced_preview", return_value=False), \
             patch("aeclone.core.guard.require"), patch.object(alibaba_pipeline.time, "sleep"), \
             patch("aeclone.services.collect_ai._generate_field") as generate:
            result, = run_collect(cfg, "alibaba", client=client, rows=[product], dry_run=False)
            stored = get(result["run_id"])
        generate.assert_not_called()
        self.assertLess(result["initial_score"], 4.5)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertGreaterEqual(result["score"], 4.5)
        self.assertTrue(any("主图" in action for action in result["actions"]))
        self.assertTrue(any("详情文字" in action for action in result["actions"]))
        self.assertEqual(len(calls), 1)
        payload, op = calls[0]
        self.assertEqual(op, "2")
        self.assertEqual(len(payload["mainImage"].split(",")), 6)
        self.assertEqual(stored["items"][0]["result"]["score"], result["score"])
        self.assertTrue(result["initial_score_problems"])

    def test_remaining_missing_warehouse_and_video_are_reported_without_publication(self):
        product = source_product(description="", detailImage=[], mainVideoId=None, draftImgUrl="")
        client = FakeClient(product, required=[])
        callback = Mock()
        with patch.object(alibaba_pipeline, "forced_preview", return_value=False), patch("aeclone.core.guard.require"):
            result = alibaba_pipeline.run_alibaba_product(product["id"], make_cfg(), client=client, dry_run=False,
                use_ai=False, op="2", repair_score=True, before_submit=callback)
        self.assertLess(result.score, 4.5)
        self.assertTrue(any("视频" in issue for issue in result.score_problems))
        self.assertTrue(any("详情" in issue for issue in result.score_problems))
        self.assertFalse(result.saved)
        callback.assert_not_called()
        self.assertFalse(client.saved)

    def test_preview_scores_without_repair_or_paid_ai(self):
        product = source_product()
        client = FakeClient(product, required=[])
        cfg = make_cfg(alibaba_collect_ai=True, api_key="test-api-key")
        with patch("aeclone.services.alibaba_score_repair.repair") as repair:
            result = alibaba_pipeline.run_alibaba_product(product["id"], cfg, client=client, use_ai=False,
                dry_run=True, op="2", repair_score=True)
        repair.assert_not_called()
        self.assertLess(result.initial_score, 4.5)
        self.assertEqual(result.initial_score, result.score)
        self.assertFalse(client.saved)

    def test_repairs_only_selected_ai_fields_and_rejects_invented_specifications(self):
        product = source_product(subject="Pool", description="", textDesc="Steel pool.",
                                 keywords="pool, steel, outdoor")
        client = FakeClient(product, required=[])
        cfg = make_cfg(alibaba_collect_ai=True, alibaba_collect_ai_fields=json.dumps(["title"]), api_key="test-api-key")
        with patch("aeclone.services.collect_ai._generate_field",
                   return_value=("Solar 4000W Pool Made of Titanium", [])) as generate:
            result = alibaba_pipeline.run_alibaba_product(product["id"], cfg, client=client, use_ai=False,
                dry_run=False, op="2", repair_score=True)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(generate.call_args.args[-1], "title")
        self.assertIn("本次改进", generate.call_args.args[-2].alibaba_title_prompt)
        self.assertEqual(result.subject, "Pool")
        self.assertFalse(result.saved)


if __name__ == "__main__":
    unittest.main()
