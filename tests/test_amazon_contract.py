"""Amazon 刊登 · 离线契约测试（不 import 后端实现，不联网，绝不写）。

覆盖三类契约：
1. ``docs/AMAZON_DESIGN.md`` 是实现契约本身：39 个 SAVE_KEYS、op 语义、``±``
   卖点约定、ZIP(amazonSave.txt)+multipart(file→op)、站点表、校验与安全边界；
2. 独立实现一份「payload → ZIP → multipart」构造，用 zipfile 解压验证往返，
   并用假 requests session 断言字段顺序与传输形状（不发起真实请求）；
3. 防回退：只读核验脚本 ``tools/verify_amazon.py`` 不得出现唯一写入口的完整
   字面量 / 后端 import / 写开关；证据 JSON 结构必须可被机器断言。

跑法：``.venv/bin/python tests/test_amazon_contract.py``（失败退出码 1）。
"""
from __future__ import annotations

import ast
import base64
import io
import json
import re
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESIGN = ROOT / "docs" / "AMAZON_DESIGN.md"
CAPTURE = ROOT / ".tmp-probe" / "amazonSave_payload.json"
SAVE_CAPTURE = ROOT / ".tmp-probe" / "save_capture2.json"
READONLY_SCRIPT = ROOT / "tools" / "verify_amazon.py"
READONLY_EVIDENCE = ROOT / "docs" / "amazon-readonly-verification.json"
WRITE_EVIDENCE = ROOT / "docs" / "amazon-write-verification.json"

# 唯一写入口在测试里也用拼接表达，避免任何脚本/工具把完整写路径当常量抄走。
FORBIDDEN_WRITE_SUFFIX = "add" + ".json"
ZIP_ENTRY = "amazonSave.txt"
MULTIPART_BOUNDARY = "----AuroraContractBoundary"

EXPECTED_SAVE_KEYS = [
    "id", "shopId", "marketPlaceId", "currency", "title", "titleDifferentiation",
    "categoryId", "categoryBrowsePath", "audience", "isVariation", "brandSettledType",
    "fullCid", "sourceUrl", "standardProductType", "standardProductId", "sku", "brand",
    "manufacturer", "productAttr", "standardPrice", "saleSalePrice", "saleStartDateStr",
    "saleEndDateStr", "quantity", "conditionType", "conditionValue", "mainImage",
    "extraImagesStr", "draftImgUrl", "variationTheme", "skuTitle", "description",
    "bulletPoint", "gpsrManufacturerReference", "dsaResponsiblePartyAddress",
    "gpsrSafetyAttestation", "switchFulfillmentTo", "merchantShippingGroup",
    "searchTerms",
]

CORE_SITES = {
    # 站点代码 -> (marketPlaceId, 币种, 语言)
    "US": ("ATVPDKIKX0DER", "USD", "en"),
    "CA": ("A2EUQ1WTGCTBG2", "CAD", "en"),
    "MX": ("A1AM78C64UM0Y8", "MXN", "es"),
    "BR": ("A2Q3Y263D00KWC", "BRL", "pt"),
}


# ---------------------------------------------------------------------------
# 文档解析（契约即文档）
# ---------------------------------------------------------------------------
def design_text() -> str:
    return DESIGN.read_text(encoding="utf-8")


def extract_payload_block(text: str) -> dict:
    """解析契约里的 39 键保存包。

    文档里的 ``productAttr`` 值是可读占位符（形如 ``"{...JSON 字符串...}"``），
    不是合法 JSON；解析时仅把该占位符替换为 ``"{}"``，键与其它值保持原样。
    """
    marker = "**保存包（39 个键，实测抓包，必须原样结构）**"
    start = text.index(marker)
    start = text.index("```jsonc", start) + len("```jsonc")
    end = text.index("```", start)
    block = text[start:end].strip()
    block = re.sub(r'("productAttr"\s*:\s*)"\{[^"]*\}"', r'\1"{}"', block)
    return json.loads(block)


def extract_site_meta(text: str) -> dict[str, tuple[str, str, str]]:
    match = re.search(r"```python\nSITE_META = \{(.*?)\n\}\n```", text, re.S)
    assert match, "SITE_META 代码块缺失"
    rows = re.findall(
        r'"([A-Z0-9]+)": dict\(code="([^"]+)", country="([^"]+)", '
        r'currency="([^"]+)", lang="([^"]+)"',
        match.group(1),
    )
    return {marketplace: (code, country, currency) for
            marketplace, code, country, currency, _lang in rows}


def sample_payload() -> dict:
    """用契约里的 39 个键构造离线样例（不依赖抓到真实商品）。"""
    payload = {key: None for key in EXPECTED_SAVE_KEYS}
    payload.update({
        "id": "220114595363182310",
        "shopId": "9408005",
        "marketPlaceId": "A2EUQ1WTGCTBG2",
        "currency": "CAD",
        "title": "5 Ton Diesel Wheeled Backhoe Loader",
        "categoryId": "13553005011",
        "categoryBrowsePath": "6299024011/6256963011/6256964011/13553005011",
        "audience": "all",
        "isVariation": 0,
        "brandSettledType": "external_product_id",
        "fullCid": "5405448-",
        "sourceUrl": "https://www.alibaba.com/product-detail/example.html",
        "standardProductType": "upc",
        "sku": "WY-QHCA-LTM-1",
        "brand": "Generic",
        "manufacturer": "Generic",
        "productAttr": json.dumps({
            "is_assembly_required": [{"value": False,
                                      "marketplace_id": "A2EUQ1WTGCTBG2"}],
        }, ensure_ascii=False),
        "quantity": 0,
        "conditionValue": "",
        "mainImage": "https://sc04.alicdn.com/kf/main.png",
        "extraImagesStr": "https://a/1.png;https://a/2.png",
        "draftImgUrl": "https://a/1.png|https://a/2.png",
        "variationTheme": "",
        "skuTitle": 0,
        "description": "<p>Heavy duty backhoe loader.</p><br><p>Diesel engine.</p>",
        "bulletPoint": "卖点一±卖点二±卖点三±卖点四±卖点五",
        "gpsrManufacturerReference": "",
        "dsaResponsiblePartyAddress": "",
        "gpsrSafetyAttestation": 0,
        "switchFulfillmentTo": "MFN",
        "merchantShippingGroup": "legacy-template-id",
        "searchTerms": "",
    })
    assert len(payload) == 39
    return payload


# ---------------------------------------------------------------------------
# 独立实现：payload → ZIP → multipart（不 import 后端、不联网）
# ---------------------------------------------------------------------------
def build_payload_zip(payload: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(ZIP_ENTRY, json.dumps(payload, ensure_ascii=False))
    return buffer.getvalue()


def build_multipart(payload: dict, op: str = "1",
                    boundary: str = MULTIPART_BOUNDARY) -> tuple[str, bytes]:
    """契约 §1.4：file(ZIP) 在前，op 在后；zip 内单个 amazonSave.txt。"""
    zip_bytes = build_payload_zip(payload)
    file_part = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="blob"\r\n'
        "Content-Type: application/zip\r\n"
        "\r\n"
    ).encode("utf-8") + zip_bytes + b"\r\n"
    op_part = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="op"\r\n'
        "\r\n"
        f"{op}\r\n"
    ).encode("utf-8")
    closing = f"--{boundary}--\r\n".encode("utf-8")
    return f"multipart/form-data; boundary={boundary}", file_part + op_part + closing


def parse_multipart(content_type: str, body: bytes) -> list[dict]:
    boundary = content_type.split("boundary=", 1)[1].strip()
    separator = f"--{boundary}".encode("utf-8")
    parts: list[dict] = []
    for chunk in body.split(separator)[1:]:
        if chunk.startswith(b"--"):
            break
        chunk = chunk.lstrip(b"\r\n")
        if b"\r\n\r\n" not in chunk:
            continue
        raw_headers, payload = chunk.split(b"\r\n\r\n", 1)
        payload = payload[:-2] if payload.endswith(b"\r\n") else payload
        headers = {}
        for line in raw_headers.decode("utf-8", "replace").splitlines():
            if ":" in line:
                name, value = line.split(":", 1)
                headers[name.strip().lower()] = value.strip()
        disposition = headers.get("content-disposition", "")
        field = re.search(r'name="([^"]+)"', disposition)
        parts.append({"field": field.group(1) if field else "",
                      "filename": (re.search(r'filename="([^"]+)"', disposition) or
                                   [None, ""])[1],
                      "headers": headers, "payload": payload})
    return parts


class FakeResponse:
    status_code = 200

    def json(self) -> dict:
        return {"code": 0, "msg": "Successful", "data": "220114595363182310"}


class FakeSession:
    """假 requests.Session：只记录调用，绝不联网。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post(self, url: str, data=None, headers=None, **kwargs) -> FakeResponse:
        self.calls.append({"url": url, "data": data, "headers": dict(headers or {}),
                           "kwargs": kwargs})
        return FakeResponse()


def send_once(session: FakeSession, payload: dict, op: str = "1") -> dict:
    content_type, body = build_multipart(payload, op)
    url = f"https://www.dianxiaomi.com/api/amazonProductSp/{FORBIDDEN_WRITE_SUFFIX}"
    response = session.post(url, data=body, headers={
        "Content-Type": content_type,
        "X-Requested-With": "XMLHttpRequest",
    })
    return {"response": response, "call": session.calls[-1], "body": body,
            "content_type": content_type}


# ---------------------------------------------------------------------------
# 1) 设计文档契约
# ---------------------------------------------------------------------------
@unittest.skipUnless(DESIGN.exists(), "缺少 docs/AMAZON_DESIGN.md")
class DesignContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = design_text()

    def test_design_has_required_sections(self) -> None:
        for heading in ("## 1. 逆向结论", "## 6. 校验规则",
                        "## 7. 安全与真实验证边界", "## 8. 测试与验收标准"):
            self.assertIn(heading, self.text, f"契约缺少章节 {heading}")

    def test_payload_block_has_39_keys(self) -> None:
        payload = extract_payload_block(self.text)
        self.assertEqual(len(payload), 39)
        self.assertEqual(list(payload), EXPECTED_SAVE_KEYS)

    def test_payload_values_follow_real_capture_conventions(self) -> None:
        payload = extract_payload_block(self.text)
        self.assertEqual(payload["marketPlaceId"], "A2EUQ1WTGCTBG2")
        self.assertEqual(payload["currency"], "CAD")
        self.assertEqual(payload["categoryId"], "13553005011")
        self.assertEqual(payload["categoryBrowsePath"],
                         "6299024011/6256963011/6256964011/13553005011")
        self.assertIsNone(payload["standardPrice"])
        self.assertIsNone(payload["saleSalePrice"])
        self.assertIn("±", payload["bulletPoint"])
        self.assertIn("<p>", payload["description"])
        self.assertIn(";", payload["extraImagesStr"])
        self.assertIn("|", payload["draftImgUrl"])
        self.assertIsInstance(payload["productAttr"], str)
        self.assertTrue(payload["productAttr"].strip().startswith("{"))

    def test_op_semantics(self) -> None:
        for line in ("`1` = 保存", "`3` = 保存并移入「待发布」", "`2` = 保存并发布",
                     "`5` = 定时发布", "`6` = 多变种"):
            self.assertIn(line, self.text, f"op 语义缺失：{line}")

    def test_bullet_point_uses_plus_minus_join(self) -> None:
        self.assertIn('split("±")', self.text)
        self.assertIn('join("±")', self.text)
        self.assertIn("多个卖点用 `±` 连接成一个字符串", self.text)

    def test_zip_and_multipart_contract(self) -> None:
        self.assertIn(ZIP_ENTRY, self.text)
        self.assertIn("multipart", self.text)
        self.assertIn("字段顺序：`file` 在前，`op` 在后", self.text)
        self.assertIn("ZIP 文件", self.text)

    def test_site_meta_core_sites(self) -> None:
        site_meta = extract_site_meta(self.text)
        for code, (marketplace, currency, lang) in CORE_SITES.items():
            self.assertIn(marketplace, site_meta, f"SITE_META 缺 {code} 站点")
            meta_code, _country, meta_currency = site_meta[marketplace]
            self.assertEqual(meta_code, code)
            self.assertEqual(meta_currency, currency)
            self.assertIn(f'lang="{lang}"',
                          re.search(rf'"{marketplace}": dict\([^\n]+', self.text).group(0))

    def test_account_and_counts(self) -> None:
        for needle in ("QINGHEMAOYI", "2728813", "9408005",
                       "采集箱（draft） | **16**", "待发布（offline） | **5**",
                       "在线（online） | **142**", "leftStat.waitPublish=5",
                       "publishFail=21"):
            self.assertIn(needle, self.text, f"契约事实缺失：{needle}")

    def test_validation_and_safety_rules(self) -> None:
        for needle in ("`title` 为空", "> 200", "UTF-8",
                       "<script|style|iframe>", "`categoryId` 为空",
                       "AURORA_NO_SAVE", "core.guard.require",
                       "save_to_amazon", "默认一切操作 **dry-run**"):
            self.assertIn(needle, self.text, f"契约规则缺失：{needle}")

    @unittest.skipUnless(CAPTURE.exists(), "缺少真实抓包 amazonSave_payload.json")
    def test_real_capture_matches_doc_keys(self) -> None:
        capture = json.loads(CAPTURE.read_text(encoding="utf-8"))
        self.assertEqual(len(capture), 39)
        self.assertEqual(list(capture), EXPECTED_SAVE_KEYS)


# ---------------------------------------------------------------------------
# 2) ZIP + multipart 独立实现往返 / mock transport
# ---------------------------------------------------------------------------
class PayloadTransportContractTests(unittest.TestCase):
    def test_zip_roundtrip(self) -> None:
        payload = sample_payload()
        archive_bytes = build_payload_zip(payload)
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            self.assertEqual(archive.namelist(), [ZIP_ENTRY])
            restored = json.loads(archive.read(ZIP_ENTRY).decode("utf-8"))
        self.assertEqual(restored, payload)
        self.assertEqual(len(restored), 39)
        self.assertIn("±", restored["bulletPoint"])
        self.assertEqual(json.loads(restored["productAttr"]),
                         json.loads(payload["productAttr"]))

    def test_multipart_file_before_op_and_op_value(self) -> None:
        payload = sample_payload()
        content_type, body = build_multipart(payload, op="3")
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        self.assertLess(body.index(b'name="file"'), body.index(b'name="op"'))
        parts = parse_multipart(content_type, body)
        self.assertEqual([part["field"] for part in parts], ["file", "op"])
        self.assertEqual(parts[0]["filename"], "blob")
        self.assertEqual(parts[0]["headers"]["content-type"], "application/zip")
        self.assertEqual(parts[1]["payload"], b"3")
        with zipfile.ZipFile(io.BytesIO(parts[0]["payload"])) as archive:
            self.assertEqual(archive.namelist(), [ZIP_ENTRY])
            self.assertEqual(json.loads(archive.read(ZIP_ENTRY).decode("utf-8")), payload)

    def test_non_ascii_roundtrip(self) -> None:
        payload = sample_payload()
        payload["title"] = "5 吨柴油轮式挖掘装载机"
        payload["description"] = "<p>中文描述</p>"
        content_type, body = build_multipart(payload)
        parts = parse_multipart(content_type, body)
        with zipfile.ZipFile(io.BytesIO(parts[0]["payload"])) as archive:
            restored = json.loads(archive.read(ZIP_ENTRY).decode("utf-8"))
        self.assertEqual(restored["title"], payload["title"])
        self.assertEqual(restored["description"], payload["description"])

    def test_fake_requests_session_receives_built_request(self) -> None:
        """mock requests：只验证构造算法与请求形状，不产生真实网络写操作。"""
        payload = sample_payload()
        session = FakeSession()
        sent = send_once(session, payload, op="2")
        self.assertEqual(len(session.calls), 1)
        call = sent["call"]
        self.assertTrue(call["url"].endswith(FORBIDDEN_WRITE_SUFFIX))
        self.assertEqual(call["headers"]["X-Requested-With"], "XMLHttpRequest")
        self.assertIn(call["headers"]["Content-Type"], sent["content_type"])
        self.assertEqual(sent["response"].json()["code"], 0)
        parts = parse_multipart(sent["content_type"], call["data"])
        self.assertEqual([part["field"] for part in parts], ["file", "op"])
        self.assertEqual(parts[1]["payload"], b"2")

    @unittest.skipUnless(SAVE_CAPTURE.exists(), "缺少真实抓包 save_capture2.json")
    def test_real_save_capture_is_zip_multipart(self) -> None:
        """真实抓包必须仍是「file(ZIP, amazonSave.txt) 在前 + op 在后」。"""
        capture = json.loads(SAVE_CAPTURE.read_text(encoding="utf-8"))
        self.assertIsInstance(capture, list)
        self.assertTrue(capture)
        item = capture[0]
        self.assertTrue(str(item.get("url", "")).endswith(FORBIDDEN_WRITE_SUFFIX))
        self.assertIn("cookie", {key.lower() for key in item.get("headers", {})})
        # b64 是抓包时 multipart 原始字节（含 boundary + ZIP + op），用它做无损断言。
        body = base64.b64decode(item["b64"])
        self.assertIn(b'name="file"', body)
        self.assertIn(b'name="op"', body)
        self.assertLess(body.index(b'name="file"'), body.index(b'name="op"'))
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            self.assertEqual(archive.namelist(), [ZIP_ENTRY])
            restored = json.loads(archive.read(ZIP_ENTRY).decode("utf-8"))
        self.assertEqual(len(restored), 39)
        self.assertEqual(list(restored), EXPECTED_SAVE_KEYS)
        op_match = re.search(rb'name="op"\r\n\r\n([0-9]+)', body)
        self.assertIsNotNone(op_match)
        self.assertIn(op_match.group(1), (b"1", b"2", b"3"))


# ---------------------------------------------------------------------------
# 3) 只读脚本防回退
# ---------------------------------------------------------------------------
@unittest.skipUnless(READONLY_SCRIPT.exists(), "缺少 tools/verify_amazon.py")
class ReadOnlyScriptAntiRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = READONLY_SCRIPT.read_text(encoding="utf-8")

    def test_no_write_endpoint_literal(self) -> None:
        self.assertNotIn(FORBIDDEN_WRITE_SUFFIX, self.source,
                         "只读脚本不得出现唯一写入口的完整字面量")
        self.assertIn("FORBIDDEN_WRITE_SUFFIX", self.source,
                      "只读脚本必须保留写入口硬拦截断言")
        self.assertNotIn(f"/{FORBIDDEN_WRITE_SUFFIX}", self.source)

    def test_no_backend_import(self) -> None:
        """独立实现：不 import 后端 aeclone 模块。"""
        for needle in ("from aeclone", "import aeclone", "AmazonClient"):
            self.assertNotIn(needle, self.source)

    def test_no_write_switch_or_save_call(self) -> None:
        for needle in ("--allow-write", "save_product", "allow_write"):
            self.assertNotIn(needle, self.source)

    def test_allowlist_only_readonly_endpoints(self) -> None:
        tree = ast.parse(self.source)
        allow: dict | None = None
        for node in ast.walk(tree):
            is_assign = isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "ALLOWED_ENDPOINTS"
                for target in node.targets)
            is_ann_assign = (isinstance(node, ast.AnnAssign)
                             and isinstance(node.target, ast.Name)
                             and node.target.id == "ALLOWED_ENDPOINTS")
            if is_assign or is_ann_assign:
                value_node = node.value
                allow = {}
                for key, value in zip(value_node.keys, value_node.values):
                    path = ast.literal_eval(key)
                    if isinstance(value, ast.Call):
                        methods = {ast.literal_eval(arg) for arg in value.args[0].elts}
                    else:
                        methods = set(ast.literal_eval(value))
                    allow[path] = methods
                break
        self.assertIsNotNone(allow, "未找到 ALLOWED_ENDPOINTS 白名单")
        self.assertGreaterEqual(len(allow), 10)
        for path, methods in allow.items():
            self.assertTrue(path.startswith("/api/"), path)
            self.assertTrue(path.endswith(".json"), path)
            self.assertNotIn(FORBIDDEN_WRITE_SUFFIX, path)
            self.assertLessEqual(set(methods), {"GET", "POST"})

    def test_cookie_never_written_to_output_structure(self) -> None:
        # 报告结构里只允许来源/数量，不允许内容字段。
        self.assertIn('"content_logged": False', self.source)
        self.assertNotIn('"cookie_value"', self.source)
        self.assertNotIn('"cookie_header"', self.source)


# ---------------------------------------------------------------------------
# 4) 证据 JSON 结构
# ---------------------------------------------------------------------------
@unittest.skipUnless(READONLY_EVIDENCE.exists(), "缺少 docs/amazon-readonly-verification.json")
class ReadonlyEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = json.loads(READONLY_EVIDENCE.read_text(encoding="utf-8"))

    def test_required_top_level_fields(self) -> None:
        for key in ("generated_at", "account", "checks", "summary", "safety"):
            self.assertIn(key, self.report)
        self.assertIn(self.report["status"], {"PASS", "FAIL", "SKIP", "ERROR"})
        self.assertGreater(len(self.report["generated_at"]), 10)

    def test_checks_shape_and_summary_consistency(self) -> None:
        checks = self.report["checks"]
        self.assertIsInstance(checks, list)
        self.assertGreater(len(checks), 0)
        names = [row["name"] for row in checks]
        self.assertEqual(len(names), len(set(names)), "检查项名称重复")
        for row in checks:
            self.assertIsInstance(row["name"], str)
            self.assertIsInstance(row["ok"], bool)
            self.assertIsInstance(row["detail"], str)
            self.assertGreater(len(row["detail"]), 0)
        summary = self.report["summary"]
        self.assertEqual(summary["total"], len(checks))
        self.assertEqual(summary["passed"], sum(1 for row in checks if row["ok"]))
        self.assertEqual(summary["failed"], sum(1 for row in checks if not row["ok"]))

    def test_cookie_content_not_recorded(self) -> None:
        raw = READONLY_EVIDENCE.read_text(encoding="utf-8")
        cookie = self.report.get("cookie") or {}
        self.assertFalse(cookie.get("content_logged", True))
        for secret_name in ("JSESSIONID", "dxm_t=", "dxm_c=", "MYJ_"):
            self.assertNotIn(secret_name, raw)

    def test_readonly_safety_block(self) -> None:
        safety = self.report["safety"]
        self.assertTrue(safety["write_endpoints_never_called"])
        self.assertEqual(safety["blocked_attempts"], [])
        self.assertGreater(safety["request_count"], 0)
        for endpoint in safety["allowed_readonly_endpoints"]:
            self.assertNotIn(FORBIDDEN_WRITE_SUFFIX, endpoint)


@unittest.skipUnless(WRITE_EVIDENCE.exists(), "缺少 docs/amazon-write-verification.json")
class WriteEvidenceStructureTests(unittest.TestCase):
    """Lead 的真实写证据结构：只校验结构与成功码，不重新发起任何写。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.report = json.loads(WRITE_EVIDENCE.read_text(encoding="utf-8"))

    def test_summary_matches_runs(self) -> None:
        runs = self.report.get("runs")
        summary = self.report.get("summary")
        self.assertIsInstance(runs, list)
        self.assertIsInstance(summary, dict)
        writes = [run for run in runs if run.get("write") is True]
        self.assertEqual(summary["writes"], len(writes))
        self.assertEqual(summary["runs"], len(runs))
        self.assertGreaterEqual(summary["writes"], 1)

    def test_write_runs_have_success_response(self) -> None:
        for run in self.report["runs"]:
            if run.get("write") is not True:
                continue
            response = run.get("save_response") or {}
            self.assertEqual(str(response.get("code")), "0")
            self.assertIn("changed_count", run)
            self.assertIsInstance(run.get("payload_keys"), list)
            self.assertEqual(len(run["payload_keys"]), 39)
            self.assertEqual(run["payload_keys"], EXPECTED_SAVE_KEYS)

    def test_cookie_masked(self) -> None:
        for run in self.report["runs"]:
            masked = str(run.get("cookie_masked") or "")
            self.assertLessEqual(len(masked), 32)
            self.assertNotIn("JSESSIONID", masked)


if __name__ == "__main__":
    unittest.main(verbosity=2)
