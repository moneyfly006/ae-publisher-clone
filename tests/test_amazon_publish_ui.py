"""Amazon 发布 UI / 逻辑矩阵离线测试（task-11；不联网、不真实写）。

覆盖：op=1/2/3、发布状态映射、A/B/C/D 失败分类、必填属性自动补齐
（只补缺失/不覆盖用户值）、confirm_write 门禁、AURORA 拒绝真实写、
发布结果 UI 文案（正式版）与重试路径 confirm_write。

运行：AURORA_TEST=1 .venv/bin/python tests/test_amazon_publish_ui.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("AURORA_TEST", "1")

from aeclone.services import amazon_pipeline as PL  # noqa: E402
from aeclone.services import amazon_publish as PUB  # noqa: E402

PRODUCT_ID = "220114595363182310"


def _load_state_audit():
    spec = importlib.util.spec_from_file_location(
        "verify_amazon_publish_state", ROOT / "tools" / "verify_amazon_publish_state.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakePublishClient:
    def __init__(self, raw: dict):
        self.raw = raw
        self.saved = []

    def publish_state(self, product_id, shop_id="-1"):
        return dict(self.raw)

    def save_product(self, payload, op="1"):
        self.saved.append((payload, op))
        return {"code": 0, "msg": "", "data": str(payload.get("id"))}


class OpAndPublishStateTests(unittest.TestCase):
    def test_op_labels(self):
        self.assertEqual(PL.OP_LABELS["1"], "保存草稿")
        self.assertEqual(PL.OP_LABELS["3"], "保存并移入待发布")
        self.assertEqual(PL.OP_LABELS["2"], "保存并发布")
        self.assertEqual((PUB.OP_SAVE, PUB.OP_MOVE_PENDING, PUB.OP_PUBLISH),
                         ("1", "3", "2"))

    def test_publish_status_mapping(self):
        cases = [
            ({"state": "online", "err_msg": "", "asin": "B0TEST"}, "online", True),
            ({"state": "publishing", "err_msg": ""}, "processing", True),
            ({"state": "waitPublish", "err_msg": ""}, "processing", True),
            ({"state": "publishFail",
              "err_msg": "code:100521,我们正在审查这个商品信息"}, "reviewing", True),
            ({"state": "publishFail",
              "err_msg": "code:90220,属性:model_number"}, "failed", False),
            ({"state": "draft", "err_msg": ""}, "not_submitted", False),
            ({"state": "weird", "err_msg": ""}, "unknown", False),
        ]
        for raw, status, ok in cases:
            result = PL.verify_publish(PRODUCT_ID, None,
                                       client=FakePublishClient(raw), wait_seconds=0)
            self.assertEqual(result["status"], status, raw)
            self.assertEqual(bool(result["ok"]), ok, raw)


class FailureClassifierTests(unittest.TestCase):
    def test_abcd(self):
        module = _load_state_audit()
        self.assertEqual(module.classify_failure("code:100521,审查中")[0], "A")
        self.assertEqual(module.classify_failure(
            "发布失败:code:90220,“型号” 为必填项，但缺失。,属性:model_number")[0], "B")
        self.assertEqual(module.classify_failure(
            "描述含促销词 sale / guaranteed / 100% / 电话")[0], "C")
        self.assertEqual(module.classify_failure("HTTP 500 网关错误")[0], "D")
        self.assertTrue(module.classifier_selftest()["ok"])

    def test_required_attrs_parse(self):
        module = _load_state_audit()
        msg = ("code:90220,属性:model_number;属性:part_number;"
               "属性:model_number;")
        self.assertEqual(module.parse_required_attrs(msg),
                         ["model_number", "part_number"])


class AutofillTests(unittest.TestCase):
    def test_only_missing_not_user_values(self):
        product = {
            "id": PRODUCT_ID, "title": "5 Ton Backhoe Loader HW20-28 Diesel",
            "sku": "WY-QHCA-LTM-1", "brand": "Generic", "manufacturer": "Generic",
            "description": "<p>China factory</p>",
            "productAttr": '{"part_number":[{"value":"EXISTING-PN"}]}',
        }
        filled = PL.autofill_attributes(
            product, None, "A2EUQ1WTGCTBG2",
            overrides={"attributes": {"model_number": "USER-MODEL"}},
            required=["model_number", "model_name", "part_number",
                      "manufacturer_contact_information"]) or {}
        self.assertNotIn("model_number", filled)   # 用户值优先，不覆盖
        self.assertNotIn("part_number", filled)   # 已有值不覆盖
        self.assertTrue(filled.get("model_name"))
        self.assertNotIn("manufacturer_contact_information", filled)  # 商品未提供真实联系方式

    def test_confirm_write_gate(self):
        calls = []
        with patch.object(PUB, "save_to_amazon", lambda *a, **kw: calls.append(1)):
            result = PL.run_amazon_product(PRODUCT_ID, None, dry_run=False,
                                           confirm_write=False, client=object())
        self.assertFalse(result.ok)
        self.assertIn("confirm_write", str(result.error or ""))
        self.assertFalse(calls)


class WriteGuardTests(unittest.TestCase):
    def test_aurora_rejects_real_write(self):
        for env_name in ("AURORA_TEST", "AURORA_NO_SAVE"):
            with patch.dict(os.environ, {env_name: "1"}):
                os.environ.pop("AURORA_TEST" if env_name != "AURORA_TEST"
                               else "AURORA_NO_SAVE", None)
                try:
                    with self.assertRaises(RuntimeError) as ctx:
                        PUB.save_to_amazon(PRODUCT_ID, None)
                    self.assertTrue("拒绝" in str(ctx.exception)
                                    or "预演" in str(ctx.exception))
                finally:
                    os.environ["AURORA_TEST"] = "1"


class ReleaseUiSourceTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "src" / "aeclone" / "ui" / "app.py").read_text(
            encoding="utf-8")

    def test_release_labels_present(self):
        for needle in ("检测连接", "发布前检查", "保存草稿",
                       "查询发布结果", "自动补齐并重试发布",
                       "发布必填属性", "model_number", "model_name",
                       "part_number", "manufacturer_contact_information"):
            self.assertIn(needle, self.source)

    def test_retry_confirm_write_and_query_readonly(self):
        self.assertIn("confirm_write=True", self.source)
        start = self.source.find("def _retry_run(")
        self.assertGreater(start, 0)
        self.assertIn("confirm_write=True", self.source[start:start + 6000])
        self.assertIn("verify_publish", self.source)

    def test_five_state_mapping(self):
        for state, label in (("draft", "采集箱"), ("waitPublish", "待发布"),
                             ("publishing", "发布中"),
                             ("publishFail", "发布失败"), ("online", "在线")):
            self.assertIn(f'"{state}"', self.source)
            self.assertIn(label, self.source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
