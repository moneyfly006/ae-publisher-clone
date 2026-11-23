"""TikTok Shop 刊登 · 离线契约测试（不联网、不发真实写、不改业务代码）。

覆盖五类契约：
1. ``docs/TIKTOK_DESIGN.md`` 是实现契约本身：51 个 SAVE_KEYS（保序）、op 语义
   （1/3/2+AS_DRAFT/5）、``tiktokSave.txt`` + ZIP + multipart(file→op[,saveMode])、
   23 个 L7 站点、24 站点 SITE_META、类目参数（crossBorder=1 / categoryParentId /
   searchCategory 字段名 category / attributeList type=3）、§1.6 限额、账号与
   「未绑定 TikTok 店铺」事实、§3.4 overrides 唯一键名；
2. 后端接口契约：``build_tiktok_payload``（详情透传保留未知 camelCase 键 + 补
   SAVE_KEYS + 删 variationBos）、``validate_tiktok_payload``（for_publish
   keyword-only）、``apply_site``、``price_stock_overrides``、pipeline/publish 签名；
3. 传输契约：独立实现一份 payload→ZIP→multipart 用 zipfile 解压往返；再用假
   requests session 断言后端客户端的字段顺序/不重试/TikTokNotReady 映射；
4. 防回退：只读脚本 ``tools/verify_tiktok.py`` 无业务后端 import、无写入口完整
   字面量、``--write-roundtrip`` 必须同时 ``--confirm-write``；button_audit /
   test_offline / verify_regressions 已接入 TikTok；
5. 证据结构：主只读证据 ``docs/tiktok-readonly-verification.json``、独立复核
   ``docs/tiktok-verification-live.json``、最终报告口径（不得把离线通过写成真实
   写入成功）。

跑法：``.venv/bin/python tests/test_tiktok_contract.py``（失败退出码 1）。
"""
from __future__ import annotations

import ast
import inspect
import io
import json
import os
import re
import sys
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

DESIGN = ROOT / "docs" / "TIKTOK_DESIGN.md"
READONLY_SCRIPT = ROOT / "tools" / "verify_tiktok.py"
READONLY_EVIDENCE = ROOT / "docs" / "tiktok-readonly-verification.json"
LIVE_EVIDENCE = ROOT / "docs" / "tiktok-verification-live.json"
VERIFICATION_REPORT = ROOT / "docs" / "TIKTOK_VERIFICATION.md"
BUTTON_AUDIT = ROOT / "tools" / "button_audit.py"
OFFLINE_RUNNER = ROOT / "tools" / "test_offline.py"
REGRESSION_RUNNER = ROOT / "tools" / "verify_regressions.py"

# 唯一写入口在测试里也用拼接表达，避免任何脚本/工具把完整写路径当常量抄走。
FORBIDDEN_WRITE_SUFFIX = "add" + ".json"
WRITE_PATH = "/api/tiktokProduct/" + FORBIDDEN_WRITE_SUFFIX
ZIP_ENTRY = "tiktokSave.txt"
MULTIPART_BOUNDARY = "----AuroraTikTokContractBoundary"

# docs §1.5：51 个顶层键（保序）。文档解析结果必须与后端 SAVE_KEYS 一致。
EXPECTED_SAVE_KEYS = [
    "id", "op", "shopId", "site", "siteIds", "dxmState", "dxmOfflineState",
    "productStatus", "preSaleFlag", "productId",
    "categoryId", "categoryList", "isNewCategory",
    "productName", "description", "keyProductFeatures", "searchTerms",
    "productType", "option1", "option1Value", "option2", "option2Value",
    "option3", "option3Value", "skuImgs", "variationList",
    "mainImage", "draftImgUrl", "sizeChart", "videoId", "videoData",
    "packageWeight", "packageLength", "packageWidth", "packageHeight",
    "packageDimensionUnit", "packageDimensionMode", "warehouseId", "isCodOpen",
    "sourceUrl", "fullCid", "productUnit", "lotNum", "packageType",
    "productAttributes", "productCertifications", "brandInfo", "brandId",
    "manufacturerId", "responsiblePersonId", "productCertificationsRules",
]

# docs §3.4：overrides 唯一键名（顺序也必须一致）。
EXPECTED_OVERRIDE_KEYS = [
    "title", "description", "key_features", "search_terms",
    "category_id", "category_name", "category_list",
    "images", "video_id", "source_url",
    "product_type", "price", "stock", "seller_sku",
    "weight", "length", "width", "height", "dimension_unit",
    "warehouse_id", "product_unit",
    "brand_id", "manufacturer_id", "responsible_person_id",
    "product_attributes", "certifications",
]

L7_SITES = [
    "TH", "VN", "MY", "PH", "SG", "ID", "GB", "DE", "ES", "IT", "FR", "IE",
    "PL", "NL", "BE", "CZ", "AT", "GR", "PT", "HU", "JP", "BR", "MX",
]

EXPECTED_LIMITS = {
    "priceMin": 0.01, "priceMax": 180000000.0, "stockMax": 99999,
    "nameMax": 255, "nameMin": 25, "descMax": 10000, "descImgMax": 30,
    "imgCountMax": 9, "imgCountMin": 1, "packageMin": 1000,
    "packageMid": 1000, "packageMax": 1000, "variationSizeMax": 100,
    "variationNameMax": 20, "variationOptionMax": 50,
}

IMAGE_1 = "https://img.example.com/1.jpg"
IMAGE_2 = "https://img.example.com/2.jpg"


# ---------------------------------------------------------------------------
# 文档解析（契约即文档）
# ---------------------------------------------------------------------------
def design_text() -> str:
    return DESIGN.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    start = text.index(heading)
    tail = text[start + len(heading):]
    stops = [pos for marker in ("\n## ", "\n### ") if (pos := tail.find(marker)) >= 0]
    end = min(stops) if stops else len(tail)
    return tail[:end]


def _code_block(text: str, heading: str, lang: str = "jsonc") -> str:
    section = _section(text, heading)
    start = section.index(f"```{lang}") + len(f"```{lang}")
    end = section.index("```", start)
    return section[start:end].strip()


def _top_level_keys(block: str) -> list[str]:
    """扫描 JSONC 块，取**深度 1** 的键（忽略嵌套 categoryList/SKU 等内部键与注释）。"""
    keys: list[str] = []
    depth = 0
    index = 0
    length = len(block)
    while index < length:
        char = block[index]
        if char == "/" and index + 1 < length and block[index + 1] == "/":
            while index < length and block[index] != "\n":
                index += 1
            continue
        if char == '"':
            end = index + 1
            while end < length:
                if block[end] == "\\":
                    end += 2
                    continue
                if block[end] == '"':
                    break
                end += 1
            token = block[index + 1:end]
            cursor = end + 1
            while cursor < length and block[cursor] in " \t":
                cursor += 1
            if depth == 1 and cursor < length and block[cursor] == ":":
                keys.append(token)
            index = end + 1
            continue
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth = max(0, depth - 1)
        index += 1
    return keys


def extract_save_keys(text: str) -> list[str]:
    return _top_level_keys(_code_block(text, "### 1.5 保存包"))


def extract_override_keys(text: str) -> list[str]:
    block = _code_block(text, "### 3.4 `overrides`", lang="python")
    return re.findall(r'"([a-z0-9_]+)"\s*:', block)


def extract_limits(text: str) -> dict:
    return json.loads(_code_block(text, "### 1.6 站点限额"))


def extract_l7_sites(text: str) -> list[str]:
    match = re.search(r"data\.tiktokL7Site=(\[[^\]]*\])", text)
    assert match, "契约 §1.1 缺少 data.tiktokL7Site 列表"
    return json.loads(match.group(1))


def extract_site_meta_table(text: str) -> dict[str, dict]:
    rows: dict[str, dict] = {}
    for line in _section(text, "## 2. 站点").splitlines():
        match = re.match(
            r"^\| ([A-Z]{2}) \| ([^|]+?) \| ([A-Z]{3}) \| ([a-z]+) \| ([^|]+?) \|$",
            line.strip())
        if match:
            rows[match.group(1)] = {
                "country": match.group(2).strip(),
                "currency": match.group(3).strip(),
                "lang": match.group(4).strip(),
                "lang_name": match.group(5).strip(),
            }
    return rows


# ---------------------------------------------------------------------------
# 独立实现：payload → ZIP → multipart（不 import 后端、不联网）
# ---------------------------------------------------------------------------
def build_payload_zip(payload: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(ZIP_ENTRY, json.dumps(payload, ensure_ascii=False))
    return buffer.getvalue()


def build_multipart(payload: dict, op: str = "1", save_mode: str = "",
                    boundary: str = MULTIPART_BOUNDARY) -> tuple[str, bytes]:
    """契约 §1.5：file(ZIP) 在前，op 在后；saveMode 仅在非空时追加。"""
    zip_bytes = build_payload_zip(payload)
    parts: list[bytes] = [
        (f"--{boundary}\r\n"
         'Content-Disposition: form-data; name="file"; filename="blob"\r\n'
         "Content-Type: application/zip\r\n"
         "\r\n").encode("utf-8") + zip_bytes + b"\r\n",
        (f"--{boundary}\r\n"
         'Content-Disposition: form-data; name="op"\r\n'
         "\r\n"
         f"{op}\r\n").encode("utf-8"),
    ]
    if save_mode:
        parts.append((f"--{boundary}\r\n"
                      'Content-Disposition: form-data; name="saveMode"\r\n'
                      "\r\n"
                      f"{save_mode}\r\n").encode("utf-8"))
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return f"multipart/form-data; boundary={boundary}", b"".join(parts)


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
    def __init__(self, payload: dict | None = None, status_code: int = 200):
        self._payload = payload if payload is not None else {"code": 0, "data": {}}
        self.status_code = status_code
        self.text = json.dumps(self._payload, ensure_ascii=False)

    def json(self) -> dict:
        return self._payload


class FakeSession:
    """假 requests.Session：按路径返回预置响应，只记录调用，绝不联网。"""

    def __init__(self, responses: dict[str, dict] | None = None):
        self.responses = responses or {}
        self.calls: list[dict] = []

    def request(self, method, url, params=None, data=None, files=None,
                headers=None, timeout=None, **kwargs):
        path = "/" + str(url).split("/", 3)[-1] if "://" in str(url) else str(url)
        payload = self.responses.get(path)
        if payload is None:
            payload = {"code": 0, "data": {}}
        self.calls.append({"method": method, "url": url, "path": path,
                           "params": params, "data": data, "files": files,
                           "headers": dict(headers or {}), "timeout": timeout})
        return FakeResponse(payload)


def sample_detail() -> dict:
    """结构对齐 gettiktokProduct 的 data.product（含未知 camelCase 键与 variationBos）。"""
    return {
        "product": {
            "id": "5764600000000012345",
            "shopId": "9001",
            "site": "GB",
            "siteIds": ["GB"],
            "productName": "Wireless Bluetooth Earbuds with Charging Case",
            "description": "<p>Old description</p>",
            "keyProductFeatures": [],
            "searchTerms": [],
            "categoryId": "605196",
            "categoryList": [{"categoryId": "605196", "categoryName": "Automotive"}],
            "mainImage": IMAGE_1,
            "draftImgUrl": IMAGE_1,
            "variationList": [{"sellerSku": "SKU-1", "originalPrice": 19.99,
                               "availableStock": 10}],
            "packageWeight": 0.25,
            "packageLength": 10, "packageWidth": 8, "packageHeight": 4,
            "packageDimensionUnit": "CENTIMETER",
            "productType": 1,
            "unknownFutureKey": {"nested": ["kept", 1]},
            "variationBos": [{"should": "be removed"}],
            "futureCamelField": "passthrough",
        },
        "site": "GB",
        "category": {"categoryId": "605196"},
    }


def valid_payload(**over) -> dict:
    """能通过 validate_tiktok_payload 的最小合法保存包（US-无关，GB 站点）。"""
    payload = {
        "shopId": "9001",
        "site": "GB",
        "categoryId": "605196",
        "categoryList": [{"categoryId": "605196", "categoryName": "Automotive"}],
        "productName": "Wireless Bluetooth Earbuds with Charging Case",
        "description": "<p>Great earbuds for daily use.</p><ul><li>Battery: 5h</li></ul>",
        "mainImage": f"{IMAGE_1}|{IMAGE_2}",
        "variationList": [{"sellerSku": "SKU-1", "originalPrice": 19.99,
                           "availableStock": 10}],
        "packageWeight": 0.25,
        "packageDimensionUnit": "CENTIMETER",
        "packageLength": 10, "packageWidth": 8, "packageHeight": 4,
        "keyProductFeatures": [],
        "searchTerms": [],
        "op": 1,
    }
    payload.update(over)
    return payload


# ---------------------------------------------------------------------------
# 1) 设计文档契约
# ---------------------------------------------------------------------------
@unittest.skipUnless(DESIGN.exists(), "缺少 docs/TIKTOK_DESIGN.md")
class DesignContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = design_text()

    def test_required_sections(self) -> None:
        for heading in ("## 0. 环境事实", "## 1. 逆向结论", "## 3. 后端模块与接口",
                        "## 4. 平台 UI", "## 5. 校验规则", "## 6. 验证与验收标准",
                        "## 7. 安全与边界", "## 8. 结论口径"):
            self.assertIn(heading, self.text, f"契约缺少章节 {heading}")

    def test_save_keys_51_and_order(self) -> None:
        keys = extract_save_keys(self.text)
        self.assertEqual(len(keys), 51, f"文档 SAVE_KEYS 数量 {len(keys)} != 51")
        self.assertEqual(keys, EXPECTED_SAVE_KEYS)
        from aeclone.services.tiktok_payload import SAVE_KEYS
        self.assertEqual(list(SAVE_KEYS), EXPECTED_SAVE_KEYS,
                         "后端 SAVE_KEYS 与文档不一致/顺序漂移")

    def test_op_semantics(self) -> None:
        for needle in ("`1` = **保存**", "`3` = **保存并移入待发布**",
                       "`2` + `saveMode=\"AS_DRAFT\"` = **发布为草稿**",
                       "`2` = **发布**", "`5` = **定时发布**"):
            self.assertIn(needle, self.text, f"op 语义缺失：{needle}")
        from aeclone.integrations import tiktok as client_mod
        self.assertEqual((client_mod.OP_SAVE, client_mod.OP_MOVE_PENDING,
                          client_mod.OP_PUBLISH, client_mod.OP_SCHEDULE),
                         ("1", "3", "2", "5"))
        self.assertEqual(client_mod.SAVE_MODE_AS_DRAFT, "AS_DRAFT")
        self.assertEqual(client_mod.SAVE_NAME, ZIP_ENTRY)

    def test_zip_and_multipart_contract(self) -> None:
        for needle in ("tiktokSave.txt", "multipart", "`file`=ZIP",
                       "`op`(+可选 `saveMode`)", "ZIP"):
            self.assertIn(needle, self.text, f"传输契约缺失：{needle}")

    def test_l7_sites_23(self) -> None:
        sites = extract_l7_sites(self.text)
        self.assertEqual(sites, L7_SITES)
        self.assertIn("data.tiktokL7ShopId=\"ALL\"", self.text)
        from aeclone.integrations.tiktok import L7_SHOP_ID, L7_SITES as BACKEND_SITES
        self.assertEqual(list(BACKEND_SITES), L7_SITES)
        self.assertEqual(L7_SHOP_ID, "ALL")

    def test_site_meta_24_and_backend_alignment(self) -> None:
        table = extract_site_meta_table(self.text)
        self.assertEqual(len(table), 24, f"§2 站点表 {len(table)} != 24")
        from aeclone.services.tiktok_prompts import SITE_META as prompt_meta
        from aeclone.services.tiktok_payload import SITE_META as payload_meta
        self.assertEqual(len(prompt_meta), 24)
        for site, row in table.items():
            self.assertIn(site, prompt_meta, f"prompts.SITE_META 缺 {site}")
            self.assertEqual(prompt_meta[site]["country"], row["country"])
            self.assertEqual(prompt_meta[site]["currency"], row["currency"])
            self.assertEqual(prompt_meta[site]["lang"], row["lang"])
            self.assertEqual(prompt_meta[site]["lang_name"], row["lang_name"])
            self.assertIn(site, payload_meta, f"payload.SITE_META 缺 {site}")

    def test_limits_match_contract(self) -> None:
        limits = extract_limits(self.text)
        self.assertEqual(set(limits), set(EXPECTED_LIMITS))
        for key, expected in EXPECTED_LIMITS.items():
            self.assertEqual(float(limits[key]), float(expected), f"限额 {key} 不一致")
        from aeclone.integrations.tiktok import DEFAULT_LIMITS as CLIENT_LIMITS
        from aeclone.services.tiktok_payload import DEFAULT_LIMITS as PAYLOAD_LIMITS
        self.assertEqual(CLIENT_LIMITS, PAYLOAD_LIMITS, "客户端与保存包默认限额漂移")
        for key, expected in EXPECTED_LIMITS.items():
            self.assertEqual(float(PAYLOAD_LIMITS[key]), float(expected))

    def test_category_api_params_documented(self) -> None:
        for needle in ("crossBorder", "categoryParentId", "searchCategory",
                       "`category`=关键词", "`type=3`", "attributeList"):
            self.assertIn(needle, self.text, f"类目契约缺失：{needle}")

    def test_account_and_unbound_shop_truth(self) -> None:
        for needle in ("QINGHEMAOYI", "2728813", "**没有 TikTok 店铺**",
                       "TikTok 采集箱 | **0**", "GB 顶层 = 30 条",
                       "\"phone\" 返回 73 条", "返回 10 条属性",
                       "所有写操作被服务端拒绝"):
            self.assertIn(needle, self.text, f"账号/事实缺失：{needle}")

    def test_safety_rules(self) -> None:
        for needle in ("save_to_tiktok", "confirm_write=True", "AURORA_NO_SAVE",
                       "AURORA_TEST", "唯一真实写收口", "delete payload.variationBos",
                       "不得在报告里把「离线 fixture 通过」写成「真实接口写入成功」"):
            self.assertIn(needle, self.text, f"安全契约缺失：{needle}")

    def test_write_endpoint_and_param_shape(self) -> None:
        self.assertIn("`/api/tiktokProduct/add.json`", self.text)
        self.assertIn("`idStr`", self.text)
        self.assertIn("`productId`", self.text)
        for needle in ("getOfflineCounts", "getOnlineCounts", "getLimitBySite",
                       "gettiktokProduct", "getByProductId", "getVariantByProductId"):
            self.assertIn(needle, self.text, f"接口清单缺失：{needle}")


# ---------------------------------------------------------------------------
# 2) 后端接口契约（payload / validate / overrides / pipeline / publish）
# ---------------------------------------------------------------------------
class BackendPayloadContractTests(unittest.TestCase):
    def test_override_keys_26_and_order(self) -> None:
        from aeclone.services.tiktok_payload import OVERRIDE_KEYS
        doc_keys = extract_override_keys(design_text()) if DESIGN.exists() else []
        if doc_keys:
            self.assertEqual(doc_keys, EXPECTED_OVERRIDE_KEYS)
        self.assertEqual(list(OVERRIDE_KEYS), EXPECTED_OVERRIDE_KEYS + ["variant_edits", "clear_images", "size_chart", "sku_images"])

    def test_build_payload_passthrough_and_save_keys(self) -> None:
        from aeclone.services.tiktok_payload import SAVE_KEYS, build_tiktok_payload
        payload = build_tiktok_payload(
            sample_detail(), None,
            {"title": "New TikTok Title For Wireless Earbuds GB",
             "description": "<p>New desc</p>", "images": [IMAGE_2, IMAGE_1],
             "category_id": "605196", "category_name": "Automotive",
             "price": "21.50", "stock": "7", "seller_sku": "SKU-1",
             "weight": "0.4", "length": "12", "width": "9", "height": "5",
             "dimension_unit": "cm", "source_url": "https://example.com/src"},
            site="GB", shop_id="9001", op="1")
        # 未知 camelCase 键必须原样透传；variationBos 必须删除。
        self.assertEqual(payload["unknownFutureKey"], {"nested": ["kept", 1]})
        self.assertEqual(payload["futureCamelField"], "passthrough")
        self.assertNotIn("variationBos", payload)
        # 51 个 SAVE_KEYS 必须全部存在（详情透传 + 缺失补齐；未知键不删除）。
        for key in SAVE_KEYS:
            self.assertIn(key, payload, f"保存包缺少 SAVE_KEYS：{key}")
        # 保序契约：空详情时 SAVE_KEYS 按文档顺序依次补齐（列表前 51 键即 SAVE_KEYS）。
        empty_order = build_tiktok_payload({}, None, {"title": "x" * 30}, site="GB")
        self.assertEqual(list(empty_order)[:51], list(SAVE_KEYS),
                         "空详情的 SAVE_KEYS 补齐顺序必须与契约一致")
        # overrides 映射 + op 写 int + 数值规范化。
        self.assertEqual(payload["productName"], "New TikTok Title For Wireless Earbuds GB")
        self.assertEqual(payload["shopId"], "9001")
        self.assertEqual(payload["site"], "GB")
        self.assertEqual(payload["mainImage"], f"{IMAGE_2}|{IMAGE_1}")
        self.assertEqual(payload["draftImgUrl"], f"{IMAGE_2}|{IMAGE_1}")
        self.assertEqual(payload["categoryId"], "605196")
        self.assertEqual(payload["packageWeight"], 0.4)
        self.assertEqual(payload["packageDimensionUnit"], "CENTIMETER")
        self.assertEqual(payload["sourceUrl"], "https://example.com/src")
        self.assertEqual(payload["op"], 1)
        self.assertIsInstance(payload["op"], int)
        self.assertEqual(payload["variationList"][0]["originalPrice"], 21.5)
        self.assertEqual(payload["variationList"][0]["availableStock"], 7)

    def test_build_payload_empty_detail_gets_all_save_keys(self) -> None:
        from aeclone.services.tiktok_payload import SAVE_KEYS, build_tiktok_payload
        payload = build_tiktok_payload({}, None, {"title": "x" * 30}, site="GB")
        for key in SAVE_KEYS:
            self.assertIn(key, payload)
        self.assertNotIn("variationBos", payload)
        self.assertEqual(payload["op"], 1)

    def test_extract_detail_product_deepcopy(self) -> None:
        from aeclone.services.tiktok_payload import extract_detail_product
        detail = sample_detail()
        product = extract_detail_product(detail)
        self.assertEqual(product["id"], "5764600000000012345")
        product["id"] = "changed"
        self.assertEqual(detail["product"]["id"], "5764600000000012345")

    def test_apply_site_clears_category_and_warehouse(self) -> None:
        from aeclone.services.tiktok_payload import apply_site
        payload = {"site": "GB", "siteIds": ["GB"], "categoryId": "605196",
                   "categoryList": [{"categoryId": "605196"}],
                   "warehouseId": "wh1", "isNewCategory": 1}
        out = apply_site(payload, "US", shop_id="9001")
        self.assertEqual(out["site"], "US")
        self.assertEqual(out["categoryId"], "")
        self.assertEqual(out["categoryList"], [])
        self.assertEqual(out["warehouseId"], "")
        self.assertEqual(out["shopId"], "9001")
        self.assertEqual(out["isNewCategory"], 0)
        self.assertEqual(payload["site"], "GB")  # 不修改入参
        # 同站点切换不清空类目。
        same = apply_site({"site": "GB", "categoryId": "605196"}, "GB")
        self.assertEqual(same["categoryId"], "605196")

    def test_pipe_helpers(self) -> None:
        from aeclone.services.tiktok_payload import join_pipe, split_pipe
        self.assertEqual(split_pipe(f" {IMAGE_1} | | {IMAGE_2} "),
                         [IMAGE_1, IMAGE_2])
        self.assertEqual(join_pipe([IMAGE_1, "", None, IMAGE_2]),
                         f"{IMAGE_1}|{IMAGE_2}")
        self.assertEqual(join_pipe(f"{IMAGE_1}|{IMAGE_2}"), f"{IMAGE_1}|{IMAGE_2}")

    def test_price_stock_overrides(self) -> None:
        from aeclone.services.tiktok_payload import price_stock_overrides
        single = price_stock_overrides(
            {"variationList": [{"sellerSku": "S1", "originalPrice": 10,
                                "availableStock": 1}]}, "12.5", "9", "S1")
        self.assertEqual(single["variationList"][0]["originalPrice"], 12.5)
        self.assertEqual(single["variationList"][0]["availableStock"], 9)
        self.assertEqual(single["variationList"][0]["sellerSku"], "S1")
        # 多变种且 SKU 不能唯一命中：拒绝批量改价（原样返回）。
        multi = {"variationList": [{"sellerSku": "S1", "originalPrice": 10},
                                   {"sellerSku": "S2", "originalPrice": 20}]}
        unchanged = price_stock_overrides(multi, "1", "1", "S3")
        self.assertEqual(unchanged["variationList"][0]["originalPrice"], 10)
        self.assertEqual(unchanged["variationList"][1]["originalPrice"], 20)

    def test_validate_valid_payload_returns_no_issues(self) -> None:
        from aeclone.services.tiktok_payload import validate_tiktok_payload
        self.assertEqual(validate_tiktok_payload(valid_payload()), [],
                         "合法 GB 保存包不应有阻断项")

    def test_validate_signature_for_publish_keyword_only(self) -> None:
        from aeclone.services.tiktok_payload import validate_tiktok_payload
        sig = inspect.signature(validate_tiktok_payload)
        for name in ("limits", "site", "is_global", "for_publish"):
            param = sig.parameters[name]
            self.assertIs(param.kind, inspect.Parameter.KEYWORD_ONLY,
                          f"{name} 必须 keyword-only")
        self.assertFalse(sig.parameters["for_publish"].default)

    def test_validate_blocks_missing_required_fields(self) -> None:
        from aeclone.services.tiktok_payload import validate_tiktok_payload
        cases = {
            "shopId": ("未选择店铺", {"shopId": ""}),
            "categoryId": ("未选择叶子类目", {"categoryId": ""}),
            "productName": ("标题为空", {"productName": ""}),
            "short_title": ("小于下限", {"productName": "too short"}),
            "description": ("产品描述为空", {"description": ""}),
            "bad_tag": ("HTML 标签", {"description": "<p>x</p><script>bad</script>"}),
            "mainImage": ("主图数量", {"mainImage": ""}),
            "bad_image": ("不是 http(s)", {"mainImage": "ftp://x/1.jpg"}),
            "price": ("价格必填", {"variationList": [
                {"sellerSku": "S1", "availableStock": 1}]}),
            "price_range": ("超出范围", {"variationList": [
                {"sellerSku": "S1", "originalPrice": 0.001,
                 "availableStock": 1}]}),
            "stock_range": ("库存", {"variationList": [
                {"sellerSku": "S1", "originalPrice": 10,
                 "availableStock": 100000}]}),
            "weight": ("重量必须大于 0", {"packageWeight": 0}),
            "unit": ("尺寸单位", {"packageDimensionUnit": "POUND"}),
            "sku": ("sellerSku", {"variationList": [
                {"originalPrice": 10, "availableStock": 1}]}),
        }
        for name, (needle, over) in cases.items():
            issues = validate_tiktok_payload(valid_payload(**over))
            self.assertTrue(any(needle in issue for issue in issues),
                            f"{name} 未产生含「{needle}」的阻断项：{issues}")

    def test_validate_limits_injection(self) -> None:
        from aeclone.services.tiktok_payload import validate_tiktok_payload
        payload = valid_payload(productName="x" * 30)
        self.assertEqual(validate_tiktok_payload(payload, limits={"nameMin": 10}), [])
        issues = validate_tiktok_payload(valid_payload(), limits={"nameMax": 10})
        self.assertTrue(any("超过上限" in issue for issue in issues), issues)

    def test_validate_us_features_and_publish_only_rules(self) -> None:
        from aeclone.services.tiktok_payload import validate_tiktok_payload
        features = ["Good sound", "Long battery", "Comfort fit"]
        us = valid_payload(site="US", keyProductFeatures=features,
                           searchTerms=["wireless earbuds"])
        self.assertEqual(validate_tiktok_payload(us), [])
        # 非 US 站点：草稿保存不阻断，发布动作（for_publish=True）才阻断。
        gb = valid_payload(site="GB", keyProductFeatures=features,
                           searchTerms=["wireless earbuds"])
        self.assertEqual(validate_tiktok_payload(gb), [])
        publish_issues = validate_tiktok_payload(gb, for_publish=True)
        self.assertTrue(any("不支持五点描述" in issue for issue in publish_issues))
        self.assertTrue(any("不支持搜索关键词" in issue for issue in publish_issues))

    def test_validate_global_english_only(self) -> None:
        from aeclone.services.tiktok_payload import validate_tiktok_payload
        issues = validate_tiktok_payload(
            valid_payload(productName="这是一个中文标题" * 3,
                          description="<p>中文描述</p>"),
            is_global=True)
        self.assertTrue(any("全球产品" in issue and "中文" in issue for issue in issues),
                        issues)

    def test_build_warnings_non_blocking(self) -> None:
        from aeclone.services.tiktok_payload import build_warnings
        warnings = build_warnings(valid_payload(mainImage=IMAGE_1,
                                                packageWeight=0.2), site="GB")
        self.assertIsInstance(warnings, list)
        self.assertTrue(any("主图仅" in warning for warning in warnings), warnings)
        self.assertTrue(any("资质" in warning for warning in warnings), warnings)

    def test_pipeline_and_publish_signatures(self) -> None:
        from aeclone.services import tiktok_pipeline as pipeline
        from aeclone.services import tiktok_publish as publish

        run_sig = inspect.signature(pipeline.run_tiktok_product)
        for name, default in (("dry_run", True), ("use_ai", False),
                              ("op", "1"), ("client", None),
                              ("confirm_write", False)):
            self.assertIn(name, run_sig.parameters)
            self.assertEqual(run_sig.parameters[name].default, default)
            self.assertIs(run_sig.parameters[name].kind,
                          inspect.Parameter.KEYWORD_ONLY)
        prep_sig = inspect.signature(pipeline.prepare_save)
        for name in ("product_id", "cfg", "overrides", "client"):
            self.assertIn(name, prep_sig.parameters)
        self.assertIs(prep_sig.parameters["op"].kind,
                      inspect.Parameter.KEYWORD_ONLY)
        ai_sig = inspect.signature(pipeline.ai_generate_field)
        self.assertEqual(list(ai_sig.parameters)[:5],
                         ["product", "cfg", "field", "site", "count"])
        self.assertEqual(ai_sig.parameters["count"].default, 5)

        save_sig = inspect.signature(publish.save_to_tiktok)
        for name in ("product_id", "cfg", "overrides", "allow_invalid", "op",
                     "save_mode", "client", "confirm_write"):
            self.assertIn(name, save_sig.parameters)
        self.assertFalse(save_sig.parameters["confirm_write"].default)
        self.assertEqual(save_sig.parameters["op"].default, "1")

    def test_forced_preview_and_save_guards(self) -> None:
        from aeclone.services import tiktok_pipeline as pipeline
        from aeclone.services import tiktok_publish as publish

        for env_name in ("AURORA_TEST", "AURORA_NO_SAVE"):
            with patch.dict(os.environ, {env_name: "1"}):
                self.assertTrue(pipeline.forced_preview())
                with self.assertRaises(RuntimeError) as caught:
                    publish.save_to_tiktok("product-1", None)
                self.assertIn("拒绝", str(caught.exception))
        with patch.dict(os.environ, {"AURORA_TEST": "", "AURORA_NO_SAVE": ""}):
            self.assertFalse(pipeline.forced_preview())
            with self.assertRaises(RuntimeError) as caught:
                publish.save_to_tiktok("product-1", None)
            self.assertIn("confirm_write", str(caught.exception))

    def test_only_write_entry_is_publish_module(self) -> None:
        files = [ROOT / "src" / "aeclone" / "integrations" / "tiktok.py",
                 *sorted((ROOT / "src" / "aeclone" / "services").glob("tiktok_*.py")),
                 ROOT / "src" / "aeclone" / "ui" / "app.py",
                 ROOT / "src" / "aeclone" / "platforms.py"]
        callers: list[str] = []
        write_literals: list[str] = []
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace")
            rel = path.relative_to(ROOT).as_posix()
            if ".save_product(" in text:
                callers.append(rel)
            if WRITE_PATH in text:
                write_literals.append(rel)
        # 写接口调用只允许在唯一写收口 tiktok_publish.py（客户端自身定义不算外部调用）。
        external = [rel for rel in callers
                    if rel != "src/aeclone/integrations/tiktok.py"]
        self.assertEqual(external, ["src/aeclone/services/tiktok_publish.py"],
                         f"发现越界写调用：{external}")
        self.assertEqual(write_literals, ["src/aeclone/integrations/tiktok.py"],
                         f"写入口字面量越界：{write_literals}")
        # UI 不得直接 HTTP / 不得直接调 save_product。
        ui_text = (ROOT / "src" / "aeclone" / "ui" / "app.py").read_text(encoding="utf-8")
        for needle in ("requests.", "save_product(", WRITE_PATH):
            self.assertNotIn(needle, ui_text, f"UI 出现越界写/HTTP：{needle}")
        # pipeline 只能经 save_to_tiktok 收口。
        pipeline_text = (ROOT / "src" / "aeclone" / "services" /
                         "tiktok_pipeline.py").read_text(encoding="utf-8")
        self.assertIn("save_to_tiktok(", pipeline_text)


# ---------------------------------------------------------------------------
# 3) 传输契约：ZIP + multipart 形状 / 客户端行为（假 session，不联网）
# ---------------------------------------------------------------------------
class TransportContractTests(unittest.TestCase):
    def test_zip_roundtrip(self) -> None:
        payload = valid_payload()
        archive_bytes = build_payload_zip(payload)
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            self.assertEqual(archive.namelist(), [ZIP_ENTRY])
            restored = json.loads(archive.read(ZIP_ENTRY).decode("utf-8"))
        self.assertEqual(restored, payload)

    def test_multipart_file_before_op_and_save_mode(self) -> None:
        payload = valid_payload()
        content_type, body = build_multipart(payload, op="2", save_mode="AS_DRAFT")
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        self.assertLess(body.index(b'name="file"'), body.index(b'name="op"'))
        parts = parse_multipart(content_type, body)
        self.assertEqual([part["field"] for part in parts],
                         ["file", "op", "saveMode"])
        self.assertEqual(parts[0]["filename"], "blob")
        self.assertEqual(parts[0]["headers"]["content-type"], "application/zip")
        self.assertEqual(parts[1]["payload"], b"2")
        self.assertEqual(parts[2]["payload"], b"AS_DRAFT")
        with zipfile.ZipFile(io.BytesIO(parts[0]["payload"])) as archive:
            self.assertEqual(archive.namelist(), [ZIP_ENTRY])
            self.assertEqual(json.loads(archive.read(ZIP_ENTRY).decode("utf-8")),
                             payload)

    def test_non_ascii_roundtrip(self) -> None:
        payload = valid_payload(productName="5 吨柴油轮式挖掘装载机")
        content_type, body = build_multipart(payload)
        parts = parse_multipart(content_type, body)
        with zipfile.ZipFile(io.BytesIO(parts[0]["payload"])) as archive:
            restored = json.loads(archive.read(ZIP_ENTRY).decode("utf-8"))
        self.assertEqual(restored["productName"], "5 吨柴油轮式挖掘装载机")

    def test_client_save_product_multipart_shape(self) -> None:
        from aeclone.integrations.tiktok import TikTokClient
        session = FakeSession({WRITE_PATH: {"code": 0, "data": "T1"}})
        client = TikTokClient({"JSESSIONID": "x"})
        client.s = session
        payload = valid_payload()
        result = client.save_product(payload, op="3")
        self.assertEqual(result["code"], 0)
        # 写接口绝不自动重试：恰好一次调用。
        self.assertEqual(len(session.calls), 1)
        call = session.calls[0]
        self.assertEqual(call["method"], "POST")
        self.assertTrue(str(call["url"]).endswith(WRITE_PATH))
        fields = call["files"]
        self.assertIsInstance(fields, list, "files 必须是有序列表（file 在前）")
        self.assertEqual([name for name, _ in fields], ["file", "op"])
        filename, blob, content_type = fields[0][1]
        self.assertEqual(filename, "blob")
        self.assertEqual(content_type, "application/zip")
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            self.assertEqual(archive.namelist(), [ZIP_ENTRY])
            stored = json.loads(archive.read(ZIP_ENTRY).decode("utf-8"))
        self.assertEqual(stored["productName"], payload["productName"])
        self.assertEqual(fields[1][1], (None, "3"))

    def test_client_save_mode_appended_last(self) -> None:
        from aeclone.integrations.tiktok import TikTokClient
        session = FakeSession({WRITE_PATH: {"code": 0, "data": "T1"}})
        client = TikTokClient({"JSESSIONID": "x"})
        client.s = session
        client.save_product(valid_payload(), op="2", save_mode="AS_DRAFT")
        self.assertEqual([name for name, _ in session.calls[0]["files"]],
                         ["file", "op", "saveMode"])
        self.assertEqual(session.calls[0]["files"][2][1], (None, "AS_DRAFT"))

    def test_client_save_not_ready_mapping_and_no_retry(self) -> None:
        from aeclone.integrations.tiktok import TikTokClient, TikTokNotReady
        session = FakeSession({WRITE_PATH: {
            "code": -1, "msg": "产品未选择店铺或店铺无效！"}})
        client = TikTokClient({"JSESSIONID": "x"})
        client.s = session
        with self.assertRaises(TikTokNotReady) as caught:
            client.save_product(valid_payload(), op="1")
        message = str(caught.exception)
        self.assertIn("店铺", message)
        self.assertIn("授权店铺", message)
        self.assertEqual(len(session.calls), 1, "写接口不得自动重试")

    def test_client_invalid_op_and_empty_payload(self) -> None:
        from aeclone.integrations.tiktok import TikTokClient, TikTokError
        client = TikTokClient({"JSESSIONID": "x"})
        client.s = FakeSession()
        with self.assertRaises(TikTokError):
            client.save_product({}, op="1")
        with self.assertRaises(TikTokError):
            client.save_product(valid_payload(), op="9")

    def test_client_category_params_contract(self) -> None:
        from aeclone.integrations.tiktok import TikTokClient
        session = FakeSession({
            "/api/tiktokCategory/list.json": {"code": 0, "data": []},
            "/api/tiktokCategory/searchCategory.json": {"code": 0, "data": []},
            "/api/tiktokCategory/attributeList.json": {"code": 0, "data": []},
            "/api/tiktokProduct/getLimitBySite.json": {
                "code": 0, "data": dict(EXPECTED_LIMITS)},
        })
        client = TikTokClient({"JSESSIONID": "x"})
        client.s = session
        client.categories("GB")
        data = session.calls[-1]["data"]
        self.assertEqual(data, {"shopId": "-1", "site": "GB",
                                "crossBorder": 1, "categoryParentId": "0"})
        client.categories("GB", parent_id="605196")
        self.assertEqual(session.calls[-1]["data"]["categoryParentId"], "605196")
        client.search_categories("phone", "GB")
        data = session.calls[-1]["data"]
        self.assertEqual(data["category"], "phone")
        self.assertNotIn("keyword", data)
        self.assertNotIn("searchValue", data)
        client.category_attributes("GB", "930568")
        data = session.calls[-1]["data"]
        self.assertEqual(data["categoryId"], "930568")
        self.assertEqual(data["type"], 3)
        limits = client.site_limits("GB")
        self.assertEqual(session.calls[-1]["data"], {"site": "GB"})
        for key, expected in EXPECTED_LIMITS.items():
            self.assertEqual(float(limits[key]), float(expected))

    def test_client_sites_fallback_23_when_no_shop(self) -> None:
        from aeclone.integrations.tiktok import TikTokClient
        session = FakeSession({
            "/api/userIn.json": {"code": 0, "data": {
                "account": "QINGHEMAOYI", "puid": 2728813,
                "shopMap": {"9408005": {"id": "9408005", "name": "QingHe",
                                        "platform": "amazon"}}}},
            "/api/tiktokProduct/getL7ShopIdAndSite.json": {"code": 0, "data": {
                "tiktokL7Site": L7_SITES, "tiktokL7ShopId": "ALL"}},
        })
        client = TikTokClient({"JSESSIONID": "x"})
        client.s = session
        self.assertEqual(client.shops(), [])  # 未绑定 TikTok 店铺必须如实为空
        sites = client.sites("")
        self.assertEqual([row["site"] for row in sites], L7_SITES)
        self.assertTrue(all(row["cross_border"] for row in sites))
        self.assertEqual(len(sites), 23)


# ---------------------------------------------------------------------------
# 4) 只读脚本 / 工具接入 防回退
# ---------------------------------------------------------------------------
@unittest.skipUnless(READONLY_SCRIPT.exists(), "缺少 tools/verify_tiktok.py")
class ReadOnlyScriptAntiRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = READONLY_SCRIPT.read_text(encoding="utf-8")

    def test_no_full_write_endpoint_literal(self) -> None:
        self.assertNotIn(WRITE_PATH, self.source,
                         "只读脚本不得出现唯一写入口的完整字面量")
        self.assertNotIn("/" + FORBIDDEN_WRITE_SUFFIX, self.source)
        self.assertIn("FORBIDDEN_WRITE_SUFFIX", self.source)
        self.assertIn('"add" + ".json"', self.source)

    def test_no_backend_import(self) -> None:
        """独立复核脚本不得 import 业务后端（AST 精确判定 import 语句）。"""
        tree = ast.parse(self.source)
        modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                modules.append(node.module or "")
        backend = [name for name in modules if name.split(".")[0] == "aeclone"]
        self.assertEqual(backend, [], f"独立只读脚本 import 了业务后端：{backend}")
        for needle in ("TikTokClient(", "save_to_tiktok", "run_tiktok_product",
                       "tiktok_payload"):
            self.assertNotIn(needle, self.source,
                             f"独立只读脚本不得使用业务后端：{needle}")

    def test_allowlist_only_readonly_endpoints(self) -> None:
        tree = ast.parse(self.source)
        allow: dict | None = None
        for node in ast.walk(tree):
            is_assign = isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "ALLOWED_ENDPOINTS"
                for target in node.targets)
            is_ann = (isinstance(node, ast.AnnAssign)
                      and isinstance(node.target, ast.Name)
                      and node.target.id == "ALLOWED_ENDPOINTS")
            if is_assign or is_ann:
                value = node.value
                allow = {}
                if isinstance(value, ast.Dict):
                    for key, methods in zip(value.keys, value.values):
                        path = ast.literal_eval(key)
                        if isinstance(methods, ast.Call):
                            names = {ast.literal_eval(arg) for arg in methods.args[0].elts}
                        else:
                            names = set(ast.literal_eval(methods))
                        allow[path] = names
                break
        self.assertIsNotNone(allow, "未找到 ALLOWED_ENDPOINTS 白名单")
        self.assertGreaterEqual(len(allow), 12)
        for path, methods in allow.items():
            self.assertTrue(path.startswith("/api/"), path)
            self.assertTrue(path.endswith(".json"), path)
            self.assertNotIn(FORBIDDEN_WRITE_SUFFIX, path)
            self.assertLessEqual(set(methods), {"GET", "POST"})

    def test_write_roundtrip_requires_confirm_write(self) -> None:
        for needle in ("--write-roundtrip", "--confirm-write",
                       "if not args.confirm_write", "write_performed"):
            self.assertIn(needle, self.source, f"写回环安全契约缺失：{needle}")
        # 默认 dry-run：报告里的写标记默认 false，且确认门在写函数之前。
        self.assertIn("write_performed", self.source)
        self.assertLess(self.source.index("if not args.confirm_write"),
                        self.source.index("def write_roundtrip"),
                        "confirm_write 校验必须在写回环函数定义之前（参数校验阶段）")
        # 空 payload 传输自检必须显式断言（不得接受外部 payload）。
        self.assertIn("transport_selfcheck", self.source)
        self.assertIn("payload_empty", self.source)
        self.assertIn("数据不落库", self.source)

    def test_default_output_is_live_evidence(self) -> None:
        self.assertIn('"tiktok-verification-live.json"', self.source)
        self.assertNotIn('"tiktok-readonly-verification.json"', self.source,
                         "独立复核不得覆盖主证据文件")

    def test_button_audit_and_runners_include_tiktok(self) -> None:
        sys.path.insert(0, str(ROOT / "tools"))
        from button_audit import _cases
        platforms = {case[2] for case in _cases()}
        self.assertTrue({"tiktok", "shopee", "aliexpress"}.issubset(platforms))
        offline_source = OFFLINE_RUNNER.read_text(encoding="utf-8")
        for name in ("test_tiktok_client", "test_tiktok_payload",
                     "test_tiktok_prompts", "test_tiktok_pipeline",
                     "test_tiktok_contract", "test_tiktok_ui_page"):
            self.assertIn(name, offline_source, f"test_offline 未接入 {name}")
        regression_source = REGRESSION_RUNNER.read_text(encoding="utf-8")
        for name in ("test_tiktok_client", "test_tiktok_payload",
                     "test_tiktok_prompts", "test_tiktok_pipeline",
                     "test_tiktok_contract", "test_tiktok_ui_page"):
            self.assertIn(name, regression_source, f"verify_regressions 未接入 {name}")


# ---------------------------------------------------------------------------
# 5) 证据 JSON 结构 + 报告口径
# ---------------------------------------------------------------------------
@unittest.skipUnless(READONLY_EVIDENCE.exists(),
                     "缺少主只读证据 docs/tiktok-readonly-verification.json")
class ReadonlyEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = READONLY_EVIDENCE.read_text(encoding="utf-8")
        cls.report = json.loads(cls.raw)

    def test_status_and_summary(self) -> None:
        self.assertEqual(self.report["status"], "PASS")
        summary = self.report["summary"]
        checks = self.report["checks"]
        self.assertEqual(summary["total"], len(checks))
        self.assertEqual(summary["passed"], sum(1 for row in checks if row["ok"]))
        self.assertEqual(summary["failed"], 0)
        self.assertGreaterEqual(summary["total"], 18)
        names = [row["name"] for row in checks]
        self.assertEqual(len(names), len(set(names)), "检查项名称重复")

    def test_account_and_no_tiktok_shop_truth(self) -> None:
        account = self.report["account"]
        self.assertEqual(account["account"], "QINGHEMAOYI")
        self.assertEqual(int(account["puid"]), 2728813)
        self.assertEqual(int(account["tiktok_shop_count"]), 0)
        shops = self.report["shops"]
        self.assertEqual(int(shops["tiktok_count"]), 0)
        self.assertEqual(shops["tiktok_shops"], [])
        self.assertIn("没有 TikTok Shop", shops["truth"])

    def test_23_sites_and_limits(self) -> None:
        sites = self.report["sites"]
        self.assertEqual(int(sites["count"]), 23)
        self.assertEqual(sites["sites"], L7_SITES)
        self.assertEqual(sites["tiktokL7ShopId"], "ALL")
        self.assertTrue(sites["matches_contract"])
        limits = self.report["limits"]
        self.assertEqual(limits["mismatches"], {})
        for key, expected in EXPECTED_LIMITS.items():
            self.assertEqual(float(limits["limits"][key]), float(expected))

    def test_category_facts(self) -> None:
        cats = self.report["categories"]
        self.assertEqual(int(cats["gb_roots"]["count"]), 30)
        self.assertEqual(int(cats["gb_roots"]["cross_border"]), 1)
        self.assertEqual(int(cats["search"]["count"]), 73)
        self.assertEqual(cats["search"]["keyword_field"], "category")
        self.assertGreaterEqual(int(cats["attributes"]["attr_count_type3"]), 1)
        self.assertEqual(cats["attributes"]["attr_types"], ["3"])
        self.assertEqual(int(cats["drill"]["leaf"]["isLeaf"]), 1)

    def test_save_transport_selfcheck(self) -> None:
        probe = self.report["save_transport"]
        self.assertEqual(str(probe["code"]), "-1")
        self.assertIn("店铺", probe["msg"])
        self.assertEqual(probe["http_status"], 200)
        self.assertTrue(probe["file_field_first"])
        self.assertEqual(probe["multipart_fields"], ["file", "op"])
        self.assertEqual(probe["zip_entries"], [ZIP_ENTRY])
        self.assertEqual(probe["zip_content"], "{}")
        self.assertTrue(probe["payload_empty"])
        self.assertFalse(probe["data_written"])

    def test_safety_flags_and_cookie_not_logged(self) -> None:
        safety = self.report["safety"]
        self.assertTrue(safety["write_endpoints_never_called"])
        self.assertEqual(int(safety["write_calls_via_read_api"]), 0)
        self.assertEqual(int(safety["transport_selfcheck_calls"]), 1)
        self.assertTrue(safety["transport_selfcheck_payload_empty"])
        self.assertFalse(safety["transport_selfcheck_data_written"])
        self.assertEqual(safety["blocked_attempts"], [])
        self.assertGreater(int(safety["request_count"]), 0)
        self.assertFalse(self.report["cookie"]["content_logged"])
        self.assertFalse(self.report["cookie"]["content_in_report"])
        for secret_name in ("JSESSIONID", "dxm_t=", "dxm_c=", "MYJ_"):
            self.assertNotIn(secret_name, self.raw, f"证据泄漏 Cookie：{secret_name}")
        for endpoint in safety["allowed_readonly_endpoints"]:
            self.assertNotIn(FORBIDDEN_WRITE_SUFFIX, endpoint)


@unittest.skipUnless(LIVE_EVIDENCE.exists(),
                     "缺少独立复核证据 docs/tiktok-verification-live.json")
class LiveEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = LIVE_EVIDENCE.read_text(encoding="utf-8")
        cls.report = json.loads(cls.raw)

    def test_mode_and_status(self) -> None:
        self.assertIn("read-only", self.report["mode"])
        self.assertIn(self.report["status"], {"PASS", "FAIL", "SKIP", "ERROR"})
        self.assertGreater(len(self.report["generated_at"]), 10)
        self.assertFalse(self.report["safety"]["write_performed"],
                         "默认只读复核不得执行真实写")
        self.assertFalse(self.report["write_roundtrip"]["performed"],
                         "未绑定 TikTok 店铺时写回环必须未执行")

    def test_checks_and_summary_consistency(self) -> None:
        checks = self.report["checks"]
        self.assertIsInstance(checks, list)
        self.assertGreater(len(checks), 0)
        summary = self.report["summary"]
        self.assertEqual(summary["total"], len(checks))
        self.assertEqual(summary["passed"], sum(1 for row in checks if row["ok"]))
        self.assertEqual(summary["failed"], sum(1 for row in checks if not row["ok"]))
        names = [row["name"] for row in checks]
        self.assertEqual(len(names), len(set(names)))
        for row in checks:
            self.assertIsInstance(row["ok"], bool)
            self.assertIsInstance(row["detail"], str)
            self.assertGreater(len(row["detail"]), 0)

    def test_unbound_shop_is_stated(self) -> None:
        shops = self.report["shops"]
        self.assertEqual(int(shops["tiktok_count"]), 0)
        self.assertIn("未绑定", shops["truth"])
        self.assertTrue(self.report["write_roundtrip"]["skipped_reason"])

    def test_cookie_not_recorded(self) -> None:
        self.assertFalse(self.report["cookie"]["content_logged"])
        for secret_name in ("JSESSIONID", "dxm_t=", "dxm_c=", "MYJ_"):
            self.assertNotIn(secret_name, self.raw)


@unittest.skipUnless(VERIFICATION_REPORT.exists(),
                     "缺少验证报告 docs/TIKTOK_VERIFICATION.md")
class VerificationReportHonestyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = VERIFICATION_REPORT.read_text(encoding="utf-8")

    def test_unbound_shop_and_write_roundtrip_limitation(self) -> None:
        self.assertIn("未绑定 TikTok 店铺", self.text)
        self.assertIn("真实写入回环", self.text)
        self.assertIn("未验证", self.text)
        # 必须区分真实接口只读验证与离线 fixture。
        self.assertIn("离线", self.text)
        self.assertIn("真实接口", self.text)

    def test_no_overclaiming(self) -> None:
        # 报告中不得出现「真实写入回环已通过」之类结论。
        for overclaim in ("真实写入回环已通过", "真实写入成功", "写入回环 PASS",
                          "writes=1", "write_performed\": true"):
            self.assertNotIn(overclaim, self.text, f"报告存在夸大表述：{overclaim}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
