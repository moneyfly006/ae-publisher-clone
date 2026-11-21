"""Amazon 去预演 + 自动发布独立审计测试（task-17；离线 FakeClient，不真实写）。"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("AURORA_TEST", "1")

from aeclone.services import amazon_auto as A  # noqa: E402


def _audit():
    spec = importlib.util.spec_from_file_location(
        "verify_amazon_auto_publish", ROOT / "tools" / "verify_amazon_auto_publish.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


AUDIT = _audit()


class AutoPublishScenarioTests(unittest.TestCase):
    def test_first_round_success(self):
        res, saves, _ = AUDIT.run_auto_scenario(
            name="t", product=AUDIT.fake_product(),
            verify_sequence=[{"state": "reviewing", "status": "reviewing",
                              "ok": True, "detail": "审核中", "err_msg": "100521",
                              "required_attrs": []}])
        self.assertTrue(res["ok"])
        self.assertTrue(res["saved"])
        self.assertEqual(len(res["rounds"]), 1)
        self.assertEqual(len(saves), 1)
        self.assertTrue(saves[0]["client_injected"])

    def test_b_class_second_round(self):
        res, saves, _ = AUDIT.run_auto_scenario(
            name="b", product=AUDIT.fake_product(),
            verify_sequence=[
                {"state": "publishFail", "status": "failed", "ok": False,
                 "detail": "失败", "err_msg": "code:90220,属性:item_name",
                 "required_attrs": ["item_name"]},
                {"state": "reviewing", "status": "reviewing", "ok": True,
                 "detail": "审核中", "err_msg": "100521", "required_attrs": []}])
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["rounds"]), 2)
        self.assertEqual(len(saves), 2)

    def test_price_stock_block_no_write(self):
        res, saves, _ = AUDIT.run_auto_scenario(
            name="p", product=AUDIT.fake_product(), verify_sequence=[],
            overrides={"standard_price": 0, "quantity": 0})
        self.assertFalse(res["ok"])
        self.assertEqual(saves, [])
        blockers = " ".join(res["blockers"])
        self.assertIn("价格", blockers)
        self.assertIn("库存", blockers)

    def test_max_rounds_safe_stop(self):
        fail = {"state": "publishFail", "status": "failed", "ok": False,
                "detail": "失败", "err_msg": "code:100470,标题重复 [loader]",
                "required_attrs": []}
        res, saves, _ = AUDIT.run_auto_scenario(
            name="m", product=AUDIT.fake_product(AUDIT.base_payload(
                title="Backhoe Loader Loader Loader HW20-28")),
            verify_sequence=[dict(fail) for _ in range(5)], max_rounds=2)
        self.assertFalse(res["ok"])
        self.assertEqual(len(res["rounds"]), 2)
        self.assertLessEqual(len(saves), 2)

    def test_ci_guard(self):
        res, saves, _ = AUDIT.run_auto_scenario(
            name="g", product=AUDIT.fake_product(), verify_sequence=[],
            guard_env=True)
        self.assertFalse(res["ok"])
        self.assertEqual(saves, [])
        self.assertTrue(any("AURORA" in str(x) or "拒绝" in str(x)
                            for x in res["blockers"]))


class PolicyTests(unittest.TestCase):
    def test_policy_clean_and_fixes(self):
        pf = A.policy_preflight(AUDIT.base_payload(), AUDIT.fake_product(),
                                {})
        self.assertEqual(pf["blockers"], [])
        pf = A.policy_preflight(
            AUDIT.base_payload(title="Loader Loader Loader HW20-28"),
            AUDIT.fake_product(), {})
        self.assertIn("dedupe_title_words", pf["autofix_actions"])
        pf = A.policy_preflight(
            AUDIT.base_payload(standardPrice=0, quantity=0),
            AUDIT.fake_product(), {})
        self.assertIn("价格", " ".join(pf["blockers"]))
        self.assertIn("库存", " ".join(pf["blockers"]))

    def test_policy_no_fabrication(self):
        actions = set()
        for patch_payload in (
                {"title": "Best 100% http://x.com 电话"},
                {"description": "<p>Best 100% free shipping</p>"},
                {"searchTerms": "best " + "x" * 600}):
            actions.update(A.policy_preflight(
                AUDIT.base_payload(**patch_payload), AUDIT.fake_product(), {})
                ["autofix_actions"])
        self.assertFalse(actions & {"price", "quantity", "认证", "价格", "库存"})


class UiAndEvidenceTests(unittest.TestCase):
    def test_no_preview_content(self):
        from ui_sandbox import build_app, sandbox
        with sandbox():
            app = build_app()
            app._switch_platform("amazon")
            app._switch(13)
            values = AUDIT._values(app)
        for term in AUDIT.PREVIEW_TERMS:
            self.assertFalse(any(term in value for value in values), term)

    def test_auto_controls_present(self):
        from ui_sandbox import build_app, sandbox
        import flet as ft
        with sandbox():
            app = build_app()
            app._switch_platform("amazon")
            app._switch(13)
            labels = [str(getattr(c, "label", "") or "") for c, v in AUDIT._walk(app)
                      if v and isinstance(c, ft.Dropdown)]
            switches = [str(getattr(c, "label", "") or "") for c, v in AUDIT._walk(app)
                        if v and isinstance(c, ft.Switch)]
            button = AUDIT._find_button(app, "自动发布")
        self.assertIsNotNone(button)
        for label in ("自动发布动作", "最多自动修复轮数", "发布后等待（秒）"):
            self.assertIn(label, labels)
        self.assertTrue(any("自动补齐必填属性" in s for s in switches))
        self.assertTrue(any("AI 重写" in s for s in switches))

    def test_write_path_and_lead_evidence(self):
        source = (ROOT / "src" / "aeclone" / "services" / "amazon_auto.py").read_text(
            encoding="utf-8")
        self.assertIn("save_to_amazon(", source)
        self.assertNotIn("client.save_product(", source)
        path = ROOT / "docs" / "amazon-auto-publish-verification.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text(encoding="utf-8"))
        runs = data.get("runs") or []
        submitted = [r for r in runs
                     if (r.get("verify") or {}).get("status") == "reviewing"]
        self.assertTrue(submitted)
        self.assertTrue(any("blocked" in str(r.get("ui_done", "")).lower()
                            or (r.get("verify") or {}).get("status")
                            == "not_submitted" for r in runs))


if __name__ == "__main__":
    unittest.main(verbosity=2)
