"""Planning failures must not be reported as saved products or successful runs."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from ui_sandbox import sandbox
from aeclone.config import AppConfig
from aeclone.services.publisher import Publisher

class ResultTests(unittest.TestCase):
    def setUp(self):
        self.context = sandbox()
        self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)
        self.pub = Publisher.__new__(Publisher)
        self.pub.cfg = AppConfig(dict(image_ai_jobs="[]", image_edit_plans="[]", four_in_one=False))
        self.product = {"id": "qa-only", "price": 100, "images": [], "skus": []}

    def test_price_failure_is_failed_preview(self):
        with patch.object(self.pub, "_price", side_effect=RuntimeError("broken module")):
            result = self.pub.run(self.product, dry_run=True)
        self.assertFalse(result.ok)
        self.assertTrue(result.preview)
        self.assertIn("价格计算", result.error)
        self.assertIn("broken module", result.error)

    def test_plan_import_failure_is_not_success(self):
        with patch("aeclone.services.image_task_plan.build_plan", side_effect=RuntimeError("decompress failed")):
            result = self.pub.run(self.product, dry_run=True)
        self.assertFalse(result.ok)
        self.assertIn("图片计划", result.error)

    def test_success_is_explicitly_preview_not_saved(self):
        result = self.pub.run(self.product, dry_run=True)
        self.assertTrue(result.ok, result.error)
        self.assertTrue(result.preview)
        self.assertIn("未写入", result.steps[-1].detail)

    def test_saved_with_failed_optional_step_has_explicit_incomplete_status(self):
        from aeclone.services.publisher import PublishResult, Step
        result = PublishResult("qa", True, [Step("AI 图片", False, "timeout"),Step("保存到店小秘", True)])
        self.assertEqual(result.completion_label,"已保存，1 项处理未完成")
        self.assertEqual(len(result.incomplete_steps),1)

    def test_preview_summary_does_not_claim_real_write(self):
        import os
        from aeclone.services.preflight import summary
        with patch.dict(os.environ,{"AURORA_NO_SAVE":"1"}):
            text=summary(self.pub.cfg)
        self.assertIn("预演模式",text)
        self.assertNotIn("真实写入模式",text)

if __name__ == "__main__": unittest.main(verbosity=2)
