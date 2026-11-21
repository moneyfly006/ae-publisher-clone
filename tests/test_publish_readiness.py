#!/usr/bin/env python3
"""Alibaba 刊登链路离线验证：保存协议契约 + 类目/FOB 修复 + dry-run 安全。

不联网、不写店铺、不调 AI。退出码非 0 即失败（可被 verify_regressions.py 收录）。
真实只读核验由 tools/verify_publish_readiness.py 负责；本文件只验证代码契约。
"""
import ast
import copy
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
# 测试隔离：绝不读写真实用户配置
os.environ.setdefault("AURORA_CONFIG_DIR", tempfile.mkdtemp(prefix="aurora-pub-ready-"))
os.environ.setdefault("AURORA_SKIP_LICENSE", "1")

import verify_publish_readiness as V  # noqa: E402
from aeclone.config import AppConfig  # noqa: E402
from aeclone.integrations.alibaba import AlibabaClient, SAVE_NAME  # noqa: E402
from aeclone.services import alibaba_publish as AP  # noqa: E402
from aeclone.services import alibaba_payload as PAYLOAD  # noqa: E402
from aeclone.services.publisher import Publisher, PublishResult  # noqa: E402


def cfg(**overrides) -> AppConfig:
    c = AppConfig()
    for key, value in overrides.items():
        c.set(key, value)
    return c


def detail_with(**overrides) -> dict:
    """以工具内置合成商品为底，按需覆盖字段。"""
    base = copy.deepcopy(V._synthetic_detail())
    base.update(overrides)
    return base


class _StubDxm:
    """一旦被调用 save_product_via_api 就失败：用来证明 dry-run 不写店铺。"""

    def __init__(self):
        self.calls = []
        self._get = self._get_recorded

    def _get_recorded(self, *a, **kw):
        self.calls.append(("_get", a, kw))
        raise AssertionError("dry-run/无模板路径不应读取商品详情")

    def save_product_via_api(self, payload):
        self.calls.append(("save_product_via_api", payload))
        raise AssertionError("dry-run/无模板路径不应调用保存接口")


class SaveProtocolTests(unittest.TestCase):
    def test_op_mapping_matches_rules(self):
        """ALIBABA_RULES.md §3：op 1=保存 2=发布 3=待发布 5=定时。"""
        self.assertEqual(AP.OP_SAVE, "1")
        self.assertEqual(AP.OP_PUBLISH, "2")
        self.assertEqual(AP.OP_MOVE_PENDING, "3")
        self.assertEqual(AP.OP_SCHEDULE, "5")
        self.assertEqual(SAVE_NAME, "alibabaSave.txt")

    def test_save_transport_is_icbu_zip_without_network(self):
        """保存 = multipart file=zip(alibabaSave.txt) + op，打到 ICBU add.json。"""
        captured = {}

        def fake_post(path, data=None, files=None):
            captured["path"] = path
            captured["data"] = dict(data or {})
            captured["files"] = files
            return {"code": 0, "data": {"msg": "ok"}}

        client = AlibabaClient({})
        client._post = fake_post  # 拦截网络，只验证请求构造
        save = AlibabaClient.build_save_dict(detail_with())
        client.save_product(detail_with(), save, op="2")

        self.assertTrue(captured["path"].endswith("/alibabaIcbuProduct/add.json"),
                        captured["path"])
        self.assertEqual(captured["data"].get("op"), "2")
        files = captured["files"] or {}
        self.assertIn("file", files)
        blob = files["file"][1]
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            self.assertEqual(zf.namelist(), [SAVE_NAME])
            body = json.loads(zf.read(SAVE_NAME).decode("utf-8"))
        self.assertEqual(str(body.get("op")), "2")

    def test_payload_contains_all_documented_fields(self):
        """§3 字段清单必须全部出现在保存 JSON 里，数组/对象以字符串传递。"""
        save = AlibabaClient.build_save_dict({})
        missing = [key for key in V.RULE_SAVE_FIELDS if key not in save]
        self.assertEqual(missing, [])
        for key in ("attributes", "detailImage", "bulkDiscountPrices",
                    "wholesaleTrade", "productSku"):
            self.assertIsInstance(save[key], str, key)
            self.assertIsInstance(json.loads(save[key]), (list, dict))


class BuilderTests(unittest.TestCase):
    def test_configured_category_is_written_into_payload(self):
        """UI 选中的类目必须进入 payload，避免「按新类目校验、按旧类目保存」。"""
        c = cfg(alibaba_category_id="100010756",
                alibaba_full_cid="18,100005657,100005663,100010756")
        save = V._build_save(c, detail_with())
        self.assertEqual(str(save.get("categoryId")), "100010756")
        self.assertTrue(str(save.get("fullCid")).endswith(",100010756"),
                        save.get("fullCid"))

    def test_fob_mode_clears_ladder_and_customs_fields(self):
        """§4：FOB 模式不写阶梯价、不写 tariffsHsCode。"""
        c = cfg(alibaba_price_type="fob_price",
                alibaba_fob_min_price="100", alibaba_fob_max_price="200")
        save = V._build_save(c, detail_with())
        self.assertEqual(save.get("priceType"), "fob_price")
        self.assertEqual(json.loads(save.get("bulkDiscountPrices") or "[]"), [])
        trade = json.loads(save.get("wholesaleTrade") or "{}")
        self.assertNotIn("tariffsHsCode", trade)
        fob = json.loads(save.get("sourcingTrade") or "{}")
        self.assertEqual(float(fob["fob_min_price"]), 100.0)
        self.assertEqual(float(fob["fob_max_price"]), 200.0)

    def test_required_attributes_autofilled_from_presets_then_generic(self):
        c = cfg(alibaba_attr_presets='{"Color": "Blue"}')
        attrs, filled, unresolved = PAYLOAD.autofill_required_attributes(
            {"attributes": V._json_list(V._synthetic_detail()["attributes"])[:1]},
            V.SYNTHETIC_REQUIRED_ATTRS, c)
        names = {a["attribute_name"]: a.get("value_name") for a in attrs}
        self.assertEqual(names.get("Color"), "Blue")
        self.assertEqual(names.get("Material"), "Other")
        self.assertTrue(any(x.startswith("Color=") for x in filled))
        self.assertEqual(unresolved, [])

    def test_validate_rejects_incomplete_payload(self):
        issues = PAYLOAD.validate_alibaba_save(
            {}, {"subject": "", "keywords": "", "mainImage": "",
                 "bulkDiscountPrices": "[]",
                 "wholesaleTrade": '{"min_order_quantity": 0, "package_size": ""}',
                 "attributes": "[]", "detailImage": "[]", "textDesc": ""},
            None, [{"nameEn": "Color"}],
            [{"fieldId": "textDesc"}, {"fieldId": "detailImage"}])
        text = "；".join(issues)
        for expected in ("产品名称", "关键词", "主图为空", "阶梯价", "MOQ",
                         "包装尺寸", "包装重量", "必填属性缺失：Color",
                         "textDesc", "detailImage"):
            self.assertIn(expected, text)


class DryRunSafetyTests(unittest.TestCase):
    def screen(self):
        product = {
            "id": "TEST-1", "title": "Swimming Pool Container",
            "price": 4300, "cost": 4300,
            "images": ["https://example.invalid/a.jpg"],
            "detail_images": [], "skus": [],
            "attributes": [], "target_attributes": [],
            "status": "draft", "status_type": "draft",
            "raw": {}, "_source_sku_rows": [],
        }
        return Publisher(cfg(), dxm=_StubDxm()), product

    def test_publisher_dry_run_never_calls_save_or_network(self):
        publisher, product = self.screen()
        result = publisher.run(product, dry_run=True, manage_progress=False)
        self.assertTrue(result.preview)
        self.assertTrue(result.ok, result.error)
        self.assertTrue(result.completion_label.startswith("预演完成"))
        names = [step.name for step in result.steps]
        for expected in ("预演", "标准化", "价格计算", "保存到店小秘"):
            self.assertIn(expected, names)
        self.assertEqual(publisher.dxm.calls, [])

    def test_real_save_path_refuses_without_template_without_network(self):
        publisher, product = self.screen()
        result = PublishResult(product_id="TEST-1", ok=True)
        save = publisher._save_via_api(product, result, "save_only", None, None)
        self.assertIsNone(save)
        self.assertEqual(publisher.dxm.calls, [])

    def test_publish_service_refuses_without_cookie_source(self):
        # 临时配置目录 + 默认配置：没有 alibaba_cookies/manual_cookies/浏览器 Cookie
        from unittest.mock import patch
        with patch.object(AP, "_browser_cookies", return_value={}):
            with self.assertRaises(RuntimeError) as ctx:
                AP.make_client(cfg())
        self.assertIn("Cookie", str(ctx.exception))


class ToolContractTests(unittest.TestCase):
    def test_tool_report_offline_has_no_secrets_and_gate(self):
        secret_api = "SECRET-API-KEY-123456"
        secret_cookie = "SECRET-COOKIE-abcdef"
        c = cfg(api_key=secret_api, manual_cookies={"dxm_i": secret_cookie},
                alibaba_cookies="")
        report = V.collect(c, offline=True)
        dump = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(secret_api, dump)
        self.assertNotIn(secret_cookie, dump)
        self.assertEqual(report["mode"], "offline-fixture")
        self.assertEqual(report["writes_performed"], 0)
        self.assertIn("readiness_gate", report)
        self.assertTrue(report["readiness_gate"]["criteria"])
        self.assertIsInstance(
            report["readiness_gate"]["hypothetical_correct_config"]
            ["can_enter_real_save_stage"], bool)
        self.assertIn("not_verified_real_write", report["verification_levels"])
        self.assertEqual(report["verification_levels"]["read_only_verified"], [])

    def test_tool_only_calls_read_only_client_methods(self):
        """AST 校验：脚本不得调用保存/发布/删除类接口。"""
        tree = ast.parse((ROOT / "tools/verify_publish_readiness.py").read_text(
            encoding="utf-8"))
        forbidden = {"save_product", "save_product_file", "batch_publish",
                     "publish", "delete", "save_to_alibaba", "upload_image_from_url"}
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute):
                    used.add(func.attr)
                elif isinstance(func, ast.Name):
                    used.add(func.id)
        self.assertEqual(used & forbidden, set(),
                         f"只读核验脚本出现写入类调用：{used & forbidden}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
