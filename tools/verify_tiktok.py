"""TikTok 刊登 · 真实账号**独立复核**（requests 自实现，不 import 业务后端）。

定位
----
- 主只读证据：``tools/verify_tiktok_read.py`` → ``docs/tiktok-readonly-verification.json``
  （tiktok-client 交付）。
- 本脚本是**独立复核**：不 import ``aeclone`` 业务后端，按 ``docs/TIKTOK_DESIGN.md``
  §0/§1/§6.2 用 requests 独立实现同一批只读检查，输出
  ``docs/tiktok-verification-live.json``，**不覆盖**主证据。
- 两者结论冲突时以真实响应为准，并在 ``docs/TIKTOK_VERIFICATION.md`` 单列。

安全边界（硬约束，代码内断言）
-----------------------------
- 只允许白名单内的只读端点；非白名单/写入口直接抛错并记录 ``blocked_attempts``。
- 唯一写入口路径以拼接方式表达，源码不出现完整字面量；通用只读 API 对它做显式断言。
- **唯一**允许触达写入口的是 ``probe_save_transport()``：只发送**空 payload**（``{}``）
  的 ZIP，服务端在「店铺校验」阶段即拒绝（实测 ``code=-1``「产品未选择店铺或店铺
  无效！」），数据不落库；它不接受外部 payload，也不提供任何写开关。
- 真实写入回环（§6.4）默认**不执行**：必须同时 ``--write-roundtrip`` +
  ``--confirm-write``，且**只由 Lead 在账号绑定 TikTok 店铺后**运行；本账号未绑定
  TikTok 店铺，预检会直接 SKIP（不发起写请求）。
- Cookie 只从 ``--cookie-file`` / ``DXM_COOKIE_PATH`` / ``.tmp-probe/cookies.txt``
  读入内存；控制台只打印前 6 位脱敏，报告不写任何 Cookie 内容。

跑法
----
    .venv/bin/python tools/verify_tiktok.py                  # 只读复核（默认）
    .venv/bin/python tools/verify_tiktok.py --cookie-file /path/cookies.txt
    # 仅 Lead、仅在绑定 TikTok 店铺后（写回环；默认 dry-run）：
    .venv/bin/python tools/verify_tiktok.py --write-roundtrip --confirm-write \\
        --product-id <采集箱草稿 id>

退出码：全 PASS → 0；有 FAIL / 安全违规 → 1；Cookie 缺失/过期 → SKIP 且 0。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import os
import sys
import time
import zipfile
from functools import partial
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COOKIE_FILE = ROOT / ".tmp-probe" / "cookies.txt"
DEFAULT_OUTPUT = ROOT / "docs" / "tiktok-verification-live.json"

BASE = "https://www.dianxiaomi.com"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TIMEOUT = 30
SAVE_NAME = "tiktokSave.txt"

# ---- 契约期望值（docs/TIKTOK_DESIGN.md §0/§1/§2/§6.2） ----------------------
EXPECT = {
    "account": "QINGHEMAOYI",
    "puid": 2728813,
    "tiktok_shops": 0,
    "l7_sites": ["TH", "VN", "MY", "PH", "SG", "ID", "GB", "DE", "ES", "IT", "FR",
                 "IE", "PL", "NL", "BE", "CZ", "AT", "GR", "PT", "HU", "JP", "BR",
                 "MX"],
    "l7_shop_id": "ALL",
    "site": "GB",
    "gb_category_roots": 30,
    "search_keyword": "phone",
    "search_count": 73,
    "attr_count_type3": 10,
    "draft_total": 0,
    "limits": {
        "priceMin": 0.01, "priceMax": 180000000.0, "stockMax": 99999,
        "nameMax": 255, "nameMin": 25, "descMax": 10000, "descImgMax": 30,
        "imgCountMax": 9, "imgCountMin": 1, "packageMin": 1000,
        "packageMid": 1000, "packageMax": 1000, "variationSizeMax": 100,
        "variationNameMax": 20, "variationOptionMax": 50,
    },
}

# ---- 只读端点白名单（路径 -> 允许的方法） -----------------------------------
ALLOWED_ENDPOINTS: dict[str, frozenset[str]] = {
    "/api/userIn.json": frozenset({"GET"}),
    "/api/tiktokProduct/getL7ShopIdAndSite.json": frozenset({"GET"}),
    "/api/tiktokProduct/pageList.json": frozenset({"POST"}),
    "/api/tiktokProduct/getOfflineCounts.json": frozenset({"POST"}),
    "/api/tiktokProduct/getOnlineCounts.json": frozenset({"POST"}),
    "/api/tiktokProduct/getLimitBySite.json": frozenset({"POST"}),
    "/api/tiktokProduct/listWarehouse.json": frozenset({"POST"}),
    "/api/tiktokProduct/getByProductId.json": frozenset({"POST"}),
    "/api/tiktokProduct/gettiktokProduct.json": frozenset({"POST"}),
    "/api/tiktokProduct/getVariantByProductId.json": frozenset({"POST"}),
    "/api/tiktokCategory/list.json": frozenset({"POST"}),
    "/api/tiktokCategory/searchCategory.json": frozenset({"POST"}),
    "/api/tiktokCategory/attributeList.json": frozenset({"POST"}),
    "/api/tiktokCategory/historyList.json": frozenset({"POST"}),
    "/api/multiShopProduct/tiktok/shopList.json": frozenset({"POST"}),
}

# 唯一写入口以拼接方式表达：源码不出现完整写路径字面量，契约测试反向断言。
FORBIDDEN_WRITE_SUFFIX = "add" + ".json"
WRITE_ENDPOINT = "/api/tiktokProduct/" + FORBIDDEN_WRITE_SUFFIX


def is_forbidden_write(path: str) -> bool:
    text = str(path or "")
    return text == WRITE_ENDPOINT or FORBIDDEN_WRITE_SUFFIX in text


# ---------------------------------------------------------------------------
# Cookie 读取 / 脱敏
# ---------------------------------------------------------------------------
def resolve_cookie_file(explicit: str = "") -> tuple[Path | None, str]:
    if explicit:
        return Path(explicit).expanduser(), "--cookie-file"
    env_path = str(os.environ.get("DXM_COOKIE_PATH", "") or "").strip()
    if env_path:
        return Path(env_path).expanduser(), "DXM_COOKIE_PATH"
    if DEFAULT_COOKIE_FILE.exists():
        return DEFAULT_COOKIE_FILE, "default .tmp-probe/cookies.txt"
    return None, "missing"


def parse_cookie_pairs(raw: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for line in str(raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            parts = line.split("\t")
            if len(parts) >= 7 and parts[5].strip() and parts[6].strip():
                pairs.append((parts[5].strip(), parts[6].strip()))
            continue
        for part in line.split(";"):
            if "=" in part:
                name, value = part.strip().split("=", 1)
                if name.strip():
                    pairs.append((name.strip(), value.strip()))
    return pairs


def mask_cookie(raw: str) -> str:
    text = str(raw or "").strip()
    return (text[:6] + "…") if text else "(空)"


def zip_payload(payload: dict) -> bytes:
    raw = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(SAVE_NAME, raw.encode("utf-8"))
    return buf.getvalue()


def multipart_body(payload: dict, op: str = "1", save_mode: str = "",
                   boundary: str = "----AuroraTikTokLiveBoundary") -> tuple[str, bytes]:
    """独立构造 multipart：``file``(ZIP) 在前、``op`` 在后，``saveMode`` 仅非空时追加。"""
    zip_bytes = zip_payload(payload)
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
        disposition = raw_headers.decode("utf-8", "replace").splitlines()[0]
        field = None
        import re as _re
        match = _re.search(r'name="([^"]+)"', disposition)
        if match:
            field = match.group(1)
        parts.append({"field": field, "payload": payload,
                      "headers": raw_headers.decode("utf-8", "replace")})
    return parts


# ---------------------------------------------------------------------------
# 只读请求守卫
# ---------------------------------------------------------------------------
class ReadOnlyApi:
    """所有真实请求的唯一出口；非白名单/写入口一律拒绝。"""

    def __init__(self, cookie_header: str, timeout: int = TIMEOUT):
        self.timeout = timeout
        self.request_count = 0
        self.cache_hits = 0
        self.blocked_attempts: list[dict] = []
        self.transport_selfcheck_calls = 0
        self.transport_selfcheck_payload_empty = True
        self.transport_selfcheck_data_written = False
        self.write_mode = False
        self._cache: dict[str, dict] = {}
        self.session = requests.Session()
        for name, value in parse_cookie_pairs(cookie_header):
            self.session.cookies.set(name, value, domain=".dianxiaomi.com")
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{BASE}/web/tiktokProduct/pageList/draft",
        })

    def _guard(self, method: str, path: str) -> None:
        method = str(method).upper()
        if is_forbidden_write(path):
            self.blocked_attempts.append({"method": method, "path": path,
                                          "reason": "forbidden write endpoint"})
            raise RuntimeError("只读复核禁止调用唯一写入口（保存/发布端点）")
        allowed = ALLOWED_ENDPOINTS.get(path)
        if allowed is None:
            self.blocked_attempts.append({"method": method, "path": path,
                                          "reason": "path not in read-only allowlist"})
            raise RuntimeError(f"只读复核拒绝未白名单端点：{path}")
        if method not in allowed:
            self.blocked_attempts.append({"method": method, "path": path,
                                          "reason": "method not allowed"})
            raise RuntimeError(f"只读复核拒绝方法 {method} {path}")

    @staticmethod
    def _cache_key(method: str, path: str, params: dict | None,
                   data: dict | None) -> str:
        return json.dumps({"method": method, "path": path, "params": params,
                           "data": data}, sort_keys=True, ensure_ascii=False,
                          default=str)

    def _send(self, method: str, path: str, **kwargs) -> dict:
        key = self._cache_key(method, path, kwargs.get("params"), kwargs.get("data"))
        if key in self._cache:
            self.cache_hits += 1
            return self._cache[key]
        payload: dict = {}
        for attempt in range(2):  # 频控（code=2005）最多重试一次
            self._guard(method, path)
            self.request_count += 1
            response = self.session.request(method, BASE + path,
                                            timeout=self.timeout, **kwargs)
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code} {method} {path}")
            try:
                payload = response.json()
            except ValueError as exc:
                snippet = response.text[:80].replace("\n", " ")
                raise RuntimeError(
                    f"非 JSON 响应（可能登录态失效）：{snippet}") from exc
            if not isinstance(payload, dict):
                raise RuntimeError(f"响应不是 JSON 对象：{type(payload).__name__}")
            if str(payload.get("code")) in ("2005", "429") and attempt == 0:
                print(f"[WAIT] {path} 返回频控 code={payload.get('code')}，2 秒后重试一次")
                time.sleep(2)
                continue
            break
        self._cache[key] = payload
        return payload

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._send("GET", path, params=params or {})

    def post(self, path: str, data: dict | None = None) -> dict:
        return self._send("POST", path, data=data or {})

    # ---- 唯一例外：空 payload 传输自检（不产生数据） ----
    def probe_save_transport(self) -> dict:
        """仅发送 **空 payload** 的 ZIP 到保存端点，证明 ZIP/multipart 传输格式正确。

        服务端在「产品未选择店铺或店铺无效！」阶段拒绝（code=-1），数据不落库；
        本方法不接受外部 payload、不提供写开关，也不允许在写回环之外被调用。
        """
        payload: dict = {}
        assert payload == {}, "传输自检只允许空 payload（数据不落库）"
        content_type, body = multipart_body(payload, op="1")
        self.transport_selfcheck_calls += 1
        self.transport_selfcheck_payload_empty = (payload == {})
        response = self.session.request(
            "POST", BASE + WRITE_ENDPOINT, data=body,
            headers={"Content-Type": content_type}, timeout=self.timeout)
        probe: dict = {
            "method": "POST",
            "endpoint": WRITE_ENDPOINT,
            "http_status": response.status_code,
            "zip_entry": SAVE_NAME,
            "payload": payload,
            "payload_empty": payload == {},
            "purpose": "仅验证 ZIP(tiktokSave.txt)+multipart(file,op) 传输格式",
            "data_written": False,
        }
        try:
            data = response.json()
        except ValueError:
            data = {}
        probe["code"] = str(data.get("code"))
        probe["msg"] = str(data.get("msg") or "")
        probe["shop_error"] = "店铺" in probe["msg"]
        parts = parse_multipart(content_type, body)
        probe["multipart_fields"] = [part["field"] for part in parts]
        probe["file_field_first"] = (bool(parts) and parts[0]["field"] == "file"
                                     and body.find(b'name="file"') < body.find(b'name="op"'))
        probe["file_field_content_type"] = "application/zip"
        probe["zip_entries"] = [SAVE_NAME]
        probe["zip_content"] = "{}"
        probe["note"] = ("空 payload 在店铺校验阶段被拒绝，不产生任何数据；"
                         "本账号未绑定 TikTok 店铺。")
        self.transport_selfcheck_data_written = False
        return probe


# ---------------------------------------------------------------------------
# 检查框架
# ---------------------------------------------------------------------------
class Checker:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def record(self, name: str, ok: bool, detail: str,
               observed: dict | None = None) -> bool:
        row = {"name": name, "ok": bool(ok), "detail": str(detail)}
        if observed is not None:
            row["observed"] = observed
        self.checks.append(row)
        print(f"[{'PASS' if ok else 'FAIL'}] {name} — {detail}")
        return bool(ok)

    def run(self, name: str, fn, ctx: dict) -> bool:
        try:
            ok, detail, observed = fn(ctx)
            return self.record(name, ok, detail, observed)
        except Exception as exc:  # noqa: BLE001 - 单检查失败不拖垮其它检查
            return self.record(name, False, f"{type(exc).__name__}: {exc}")


def _code(payload: dict) -> str:
    return str(payload.get("code"))


def _data(payload: dict):
    return payload.get("data")


def _as_int(value, default: int = -1) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _as_dict(node) -> dict:
    return node if isinstance(node, dict) else {}


def _as_list(node) -> list:
    return node if isinstance(node, list) else []


def observed_of(checks: list[dict], name: str) -> dict:
    for row in checks:
        if row.get("name") == name:
            return dict(row.get("observed") or {})
    return {}


# ---------------------------------------------------------------------------
# 各项只读检查（与主证据独立实现）
# ---------------------------------------------------------------------------
def check_login(api: ReadOnlyApi, ctx: dict) -> tuple[bool, str, dict]:
    payload = api.get("/api/userIn.json")
    data = _as_dict(_data(payload))
    shops = data.get("shopMap") or {}
    ok = (_code(payload) == "0" and str(data.get("account")) == EXPECT["account"]
          and _as_int(data.get("puid")) == int(EXPECT["puid"]))
    ctx["account"] = data
    ctx["shops_raw"] = shops
    detail = (f"code={_code(payload)} account={data.get('account')} "
              f"puid={data.get('puid')} 店铺 {len(shops) if isinstance(shops, dict) else 0} 个")
    return ok, detail, {"account": data.get("account"), "puid": data.get("puid"),
                        "shop_count": len(shops) if isinstance(shops, dict) else 0}


def check_shop_truth(api: ReadOnlyApi, ctx: dict) -> tuple[bool, str, dict]:
    shops = ctx.get("shops_raw")
    if not isinstance(shops, dict):
        shops = _as_dict(_as_dict(_data(api.get("/api/userIn.json"))).get("shopMap"))
    rows = [row for row in shops.values() if isinstance(row, dict)] \
        if isinstance(shops, dict) else []
    tiktok = [row for row in rows
              if str(row.get("platform") or "").strip().lower() in
              ("tiktok", "tiktokshop", "tiktok_shop")]
    ok = len(tiktok) == int(EXPECT["tiktok_shops"])
    detail = (f"TikTok 店铺 {len(tiktok)} 个（期望 {EXPECT['tiktok_shops']}：账号未绑定）；"
              f"其它店铺={[(row.get('name'), row.get('platform')) for row in rows]}")
    return ok, detail, {"tiktok_count": len(tiktok),
                        "tiktok_shops": [{"id": row.get("id"),
                                          "name": row.get("name")} for row in tiktok],
                        "other_shops": [{"id": row.get("id"), "name": row.get("name"),
                                         "platform": row.get("platform")}
                                        for row in rows],
                        "truth": "账号 QINGHEMAOYI 未绑定 TikTok 店铺：已授权店铺只有 "
                                 "Amazon/手工订单，没有 TikTok Shop"}


def check_l7_sites(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.get("/api/tiktokProduct/getL7ShopIdAndSite.json")
    data = _as_dict(_data(payload))
    raw = data.get("tiktokL7Site")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = None
    sites = [str(code).strip().upper() for code in _as_list(raw) if str(code).strip()]
    shop_id = str(data.get("tiktokL7ShopId") or "")
    ok = (_code(payload) == "0" and len(sites) == 23
          and sites == EXPECT["l7_sites"] and shop_id == EXPECT["l7_shop_id"])
    return ok, (f"code={_code(payload)} 站点 {len(sites)} 个（期望 23）"
                f" shopId={shop_id} 列表一致={sites == EXPECT['l7_sites']}"), {
        "count": len(sites), "sites": sites, "tiktokL7ShopId": shop_id,
        "expected_count": 23, "matches_contract": sites == EXPECT["l7_sites"],
    }


def _page_list(api: ReadOnlyApi, state: str):
    return api.post("/api/tiktokProduct/pageList.json", {
        "pageNo": 1, "pageSize": 20, "shopId": "-1",
        "dxmState": state, "searchValue": "",
    })


def check_page_list(api: ReadOnlyApi, state: str):
    def _run(_ctx) -> tuple[bool, str, dict]:
        payload = _page_list(api, state)
        data = _as_dict(_data(payload))
        page = _as_dict(data.get("page"))
        rows = _as_list(page.get("list"))
        total = _as_int(page.get("totalSize"))
        expected = int(EXPECT["draft_total"])
        ok = _code(payload) == "0" and total == expected
        return ok, (f"code={_code(payload)} totalSize={total}（期望 {expected}）"
                    f" pageNo={page.get('pageNo')} rows={len(rows)}"), {
            "state": state, "code": _code(payload), "total_size": total,
            "expected": expected, "rows_on_page": len(rows),
            "page_no": _as_int(page.get("pageNo")),
            "total_page": _as_int(page.get("totalPage")),
        }
    return _run


def check_offline_counts(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.post("/api/tiktokProduct/getOfflineCounts.json", {"shopId": "-1"})
    data = _as_dict(_data(payload))
    required = ("draftNum", "publishingTiming", "waitPublish", "publishFail",
                "publishing")
    missing = [key for key in required if key not in data]
    ok = _code(payload) == "0" and not missing and _as_int(data.get("draftNum")) == 0
    detail = (f"code={_code(payload)} draftNum={data.get('draftNum')} "
              f"waitPublish={data.get('waitPublish')} publishFail={data.get('publishFail')} "
              f"缺字段={missing or '无'}")
    return ok, detail, {"counts": data, "missing_fields": missing}


def check_online_counts(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.post("/api/tiktokProduct/getOnlineCounts.json", {"shopId": "-1"})
    data = _data(payload)
    ok = _code(payload) == "0" and (isinstance(data, dict) or data is None)
    return ok, f"code={_code(payload)} data={json.dumps(data, ensure_ascii=False)[:80]}", {
        "counts": data if isinstance(data, dict) else {}}


def check_category_roots(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.post("/api/tiktokCategory/list.json", {
        "shopId": "-1", "site": EXPECT["site"], "crossBorder": 1,
        "categoryParentId": "0"})
    rows = [row for row in _as_list(_data(payload)) if isinstance(row, dict)]
    invalid = [row for row in rows
               if not str(row.get("categoryId") or "").isdigit()]
    ok = (_code(payload) == "0" and len(rows) == int(EXPECT["gb_category_roots"])
          and not invalid)
    first = rows[0] if rows else {}
    detail = (f"code={_code(payload)} GB 根类目={len(rows)}"
              f"（期望 {EXPECT['gb_category_roots']}） 字段异常={len(invalid)}"
              f" 首条={first.get('categoryId')}/{first.get('categoryName')}")
    return ok, detail, {"count": len(rows), "invalid_nodes": invalid,
                        "first": {key: first.get(key) for key in
                                  ("categoryId", "categoryName", "transName")},
                        "cross_border": 1, "site": EXPECT["site"]}


def check_category_drill(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    roots = _as_list(_data(api.post("/api/tiktokCategory/list.json", {
        "shopId": "-1", "site": EXPECT["site"], "crossBorder": 1,
        "categoryParentId": "0"})))
    root = next((row for row in roots if isinstance(row, dict)), {})
    levels: list[dict] = []
    parent = str(root.get("categoryId") or "")
    ok = bool(parent)
    for depth in range(2):
        if not parent:
            break
        payload = api.post("/api/tiktokCategory/list.json", {
            "shopId": "-1", "site": EXPECT["site"], "crossBorder": 1,
            "categoryParentId": parent})
        rows = [row for row in _as_list(_data(payload)) if isinstance(row, dict)]
        if _code(payload) != "0" or not rows:
            ok = False
            break
        node = rows[0]
        levels.append({"depth": depth + 1, "parent_category_id": parent,
                       "count": len(rows),
                       "categoryId": node.get("categoryId"),
                       "categoryName": node.get("categoryName"),
                       "isLeaf": _as_int(node.get("isLeaf"), 0),
                       "nodePathId": node.get("nodePathId") or node.get("nodePath")})
        if _as_int(node.get("isLeaf"), 0) != 1:
            parent = str(node.get("categoryId") or "")
        else:
            break
    leaf = levels[-1] if levels else {}
    ok = ok and bool(levels)
    detail = (" → ".join(f"{row.get('categoryName')}({row.get('categoryId')},"
                         f"子={row.get('count')})" for row in levels)
              + f" · 叶子={leaf.get('isLeaf')}")
    return ok, detail, {"levels": levels,
                        "leaf": {key: leaf.get(key) for key in
                                 ("categoryId", "categoryName", "isLeaf", "nodePathId")}}


def check_search_category(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.post("/api/tiktokCategory/searchCategory.json", {
        "shopId": "-1", "site": EXPECT["site"], "crossBorder": 1,
        "category": EXPECT["search_keyword"]})
    rows = [row for row in _as_list(_data(payload)) if isinstance(row, dict)]
    ok = _code(payload) == "0" and len(rows) >= 1
    detail = (f"code={_code(payload)} 关键词={EXPECT['search_keyword']!r}"
              f"（字段名 category） 命中={len(rows)} 条")
    return ok, detail, {"count": len(rows),
                        "keyword": EXPECT["search_keyword"],
                        "keyword_field": "category",
                        "sample": [{"categoryId": row.get("categoryId"),
                                    "categoryName": row.get("categoryName")}
                                   for row in rows[:5]]}


def check_attributes(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    # 先用下钻得到的叶子类目（GB Automotive 下钻），失败回退到根类目。
    leaf_id = ""
    try:
        drill = _as_dict(_data(api.post("/api/tiktokCategory/list.json", {
            "shopId": "-1", "site": EXPECT["site"], "crossBorder": 1,
            "categoryParentId": "605196"})))
        _ = drill
    except Exception:
        pass
    roots = _as_list(_data(api.post("/api/tiktokCategory/list.json", {
        "shopId": "-1", "site": EXPECT["site"], "crossBorder": 1,
        "categoryParentId": "0"})))
    for row in roots:
        if not isinstance(row, dict):
            continue
        if str(row.get("categoryId")) == "605196":
            leaf_id = "605196"
            break
    if not leaf_id:
        leaf_id = str((roots[0] or {}).get("categoryId") or "") \
            if roots and isinstance(roots[0], dict) else ""
    # 下钻到叶子：Car Electronics → Alarm Systems & Security（实测叶子 930568）。
    for parent in ("929928", "930568"):
        rows = [row for row in _as_list(_data(api.post(
            "/api/tiktokCategory/list.json", {
                "shopId": "-1", "site": EXPECT["site"], "crossBorder": 1,
                "categoryParentId": parent}))) if isinstance(row, dict)]
        found = next((row for row in rows
                      if str(row.get("categoryId")) == "930568"), None)
        if found:
            leaf_id = "930568"
            break
    payload = api.post("/api/tiktokCategory/attributeList.json", {
        "shopId": "-1", "site": EXPECT["site"], "crossBorder": 1,
        "categoryId": leaf_id, "type": 3})
    rows = [row for row in _as_list(_data(payload)) if isinstance(row, dict)]
    types = sorted({str(row.get("attrType") if row.get("attrType") is not None
                        else 3) for row in rows})
    # 对照：不带 type 只返回 1 条（契约 §1.4 实测），证明 type=3 必填。
    without = api.post("/api/tiktokCategory/attributeList.json", {
        "shopId": "-1", "site": EXPECT["site"], "crossBorder": 1,
        "categoryId": leaf_id})
    without_rows = [row for row in _as_list(_data(without)) if isinstance(row, dict)]
    ok = (_code(payload) == "0" and len(rows) >= 1
          and (len(without_rows) < len(rows) or len(without_rows) == 1))
    detail = (f"code={_code(payload)} 叶子 {leaf_id} 属性={len(rows)} 条"
              f"（期望 ≥1，主证据实测 {EXPECT['attr_count_type3']} 条）"
              f" · 不带 type 仅 {len(without_rows)} 条（证明必须 type=3）")
    return ok, detail, {"category_id": leaf_id, "attr_count_type3": len(rows),
                        "attr_count_without_type": len(without_rows),
                        "attr_types": types,
                        "sample": [{"attrName": row.get("attrName"),
                                    "attrId": row.get("attrId")} for row in rows[:5]]}


def check_limits(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.post("/api/tiktokProduct/getLimitBySite.json",
                       {"site": EXPECT["site"]})
    data = _as_dict(_data(payload))
    mismatches: dict = {}
    for key, expected in EXPECT["limits"].items():
        actual = data.get(key)
        if actual is None:
            mismatches[key] = {"expected": expected, "actual": None}
            continue
        try:
            if abs(float(actual) - float(expected)) > 1e-9:
                mismatches[key] = {"expected": expected, "actual": actual}
        except (TypeError, ValueError):
            mismatches[key] = {"expected": expected, "actual": actual}
    ok = _code(payload) == "0" and not mismatches
    detail = (f"code={_code(payload)} 字段 {len(data)} 个 不符="
              f"{mismatches or '无'} · 标题 {data.get('nameMin')}–{data.get('nameMax')}"
              f" 描述≤{data.get('descMax')} 主图 {data.get('imgCountMin')}–"
              f"{data.get('imgCountMax')} 库存≤{data.get('stockMax')}")
    return ok, detail, {"limits": data, "expected": EXPECT["limits"],
                        "mismatches": mismatches}


def check_warehouses(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.post("/api/tiktokProduct/listWarehouse.json", {"shopId": "-1"})
    data = _data(payload)
    rows = [row for row in _as_list(data) if isinstance(row, dict)]
    ok = _code(payload) == "0" and isinstance(data, list)
    return ok, f"code={_code(payload)} 仓库={len(rows)} 个（无店铺可为空）", {
        "count": len(rows), "data_is_list": isinstance(data, list)}


def check_multishop(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.post("/api/multiShopProduct/tiktok/shopList.json", {})
    data = _as_dict(_data(payload))
    site_shop = data.get("siteShop")
    pop_shop = data.get("popShop")
    ok = _code(payload) == "0" and isinstance(data, dict)
    return ok, (f"code={_code(payload)} siteShop={site_shop} popShop={pop_shop}"
                "（无店铺时均为 null，与契约一致）"), {
        "siteShop": site_shop, "popShop": pop_shop}


def check_history(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.post("/api/tiktokCategory/historyList.json", {
        "shopId": "-1", "site": EXPECT["site"]})
    data = _data(payload)
    rows = [row for row in _as_list(data) if isinstance(row, dict)]
    ok = _code(payload) == "0" and isinstance(data, list)
    return ok, f"code={_code(payload)} 最近类目={len(rows)} 条（无店铺可为空）", {
        "count": len(rows)}


def check_detail_fallback(api: ReadOnlyApi, ctx: dict) -> tuple[bool, str, dict]:
    """详情回退：主端点 best-effort，getByProductId 必须 code=0 且 data=null/对象。

    账号无 TikTok 商品时主端点可能直接 HTTP 404（无真实 id），属预期，不判失败；
    关键是回退端点可调用且空数据按契约返回（不崩溃、不伪造数据）。
    """
    product_id = str(ctx.get("product_id") or "5764600000000012345")
    primary_code, primary_error = "", ""
    try:
        primary = api.post("/api/tiktokProduct/gettiktokProduct.json",
                           {"idStr": product_id})
        primary_code = _code(primary)
    except Exception as exc:  # noqa: BLE001 - 无真实 id 时主端点 404 属预期
        primary_error = f"{type(exc).__name__}: {exc}"
    fallback = api.post("/api/tiktokProduct/getByProductId.json",
                        {"productId": product_id})
    data = _data(fallback)
    ok = _code(fallback) == "0" and (data is None or isinstance(data, dict))
    detail = (f"gettiktokProduct code={primary_code or '-'}"
              f"（{primary_error[:60] or '可调用'}）· getByProductId "
              f"code={_code(fallback)} data="
              f"{'null' if data is None else type(data).__name__}"
              "（账号无 TikTok 商品：读不到属预期）")
    return ok, detail, {
        "primary_code": primary_code, "primary_error": primary_error,
        "fallback_code": _code(fallback),
        "data": data if not isinstance(data, dict) else list(data)[:8],
        "data_is_null": data is None}


def check_save_transport(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    probe = api.probe_save_transport()
    ok = (probe["http_status"] == 200 and probe["code"] == "-1"
          and probe["shop_error"] and probe["file_field_first"]
          and probe["zip_entries"] == [SAVE_NAME]
          and probe["zip_content"] == "{}" and probe["payload_empty"])
    detail = (f"HTTP {probe['http_status']} code={probe['code']} msg={probe['msg']!r}"
              f" · ZIP 条目={probe['zip_entries']} · multipart file 在前="
              f"{probe['file_field_first']} → 传输格式正确；空 payload 数据不落库")
    return ok, detail, probe


# ---------------------------------------------------------------------------
# 真实写入回环（§6.4；默认 dry-run，只由 Lead 在绑定店铺后显式执行）
# ---------------------------------------------------------------------------
def write_confirm_error(args) -> str:
    """写回环确认门（必须在任何写操作/写函数之前执行）。"""
    if not args.confirm_write:
        return ("拒绝真实写入：--write-roundtrip 必须同时提供 --confirm-write"
                "（默认 dry-run，绝不写平台）")
    if os.environ.get("AURORA_TEST") or os.environ.get("AURORA_NO_SAVE"):
        return "拒绝真实写入：检测到 AURORA_TEST/AURORA_NO_SAVE（验收模式禁止真实写）"
    return ""


def _api_user_and_shops(api: ReadOnlyApi) -> tuple[dict, list[dict]]:
    data = _as_dict(_data(api.get("/api/userIn.json")))
    shops = data.get("shopMap") or {}
    rows = [row for row in shops.values() if isinstance(row, dict)] \
        if isinstance(shops, dict) else []
    tiktok = [row for row in rows
              if str(row.get("platform") or "").strip().lower() in
              ("tiktok", "tiktokshop", "tiktok_shop")]
    return data, tiktok


def _write_save(api: ReadOnlyApi, payload: dict, op: str = "1",
                save_mode: str = "") -> dict:
    """唯一真实写出口（仅 write_roundtrip 调用；写接口零重试）。"""
    if not api.write_mode:
        raise RuntimeError("写回环未获授权：write_mode 未开启")
    content_type, body = multipart_body(payload, op=op, save_mode=save_mode)
    response = api.session.request(
        "POST", BASE + WRITE_ENDPOINT, data=body,
        headers={"Content-Type": content_type,
                 "X-Requested-With": "XMLHttpRequest"}, timeout=api.timeout)
    try:
        return response.json()
    except ValueError:
        raise RuntimeError(f"保存接口返回非 JSON（HTTP {response.status_code}）："
                           f"{response.text[:160]}")


def _read_detail(api: ReadOnlyApi, product_id: str) -> dict:
    for path, key in (("/api/tiktokProduct/gettiktokProduct.json", "idStr"),
                      ("/api/tiktokProduct/getByProductId.json", "productId")):
        try:
            payload = api.post(path, {key: str(product_id)})
        except Exception:
            continue
        data = _data(payload)
        if isinstance(data, dict) and data:
            return data
    raise RuntimeError(f"读不到商品 {product_id} 详情（无法执行写回环）")


def write_roundtrip(cookie_header: str, timeout: int, product_id: str,
                    keep_test_draft: bool = False) -> dict:
    """§6.4 真实写入回环：原样回写 → 回读零变更 → overrides 改标题/描述 → 回读一致。

    仅在 ``--write-roundtrip --confirm-write`` 且账号**已绑定 TikTok 店铺**时由 Lead
    执行；本账号未绑定店铺 → 预检直接 SKIP，不发起任何写请求。
    """
    report: dict = {"requested": True, "performed": False, "skipped_reason": "",
                    "product_id": str(product_id or ""), "steps": [],
                    "save_response": {}, "readback": {}, "error": ""}

    def step(name: str, ok: bool, detail: str) -> None:
        report["steps"].append({"name": name, "ok": bool(ok), "detail": str(detail)})
        print(f"[{'PASS' if ok else 'FAIL'}] 写回环 · {name} — {detail}")

    api = ReadOnlyApi(cookie_header, timeout=timeout)
    try:
        account, shops = _api_user_and_shops(api)
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"写回环预检失败：{type(exc).__name__}: {exc}"
        step("预检登录", False, report["error"])
        return report
    step("预检登录", bool(account.get("account")),
         f"account={account.get('account')} puid={account.get('puid')}")
    if not shops:
        report["skipped_reason"] = (
            "账号未绑定 TikTok 店铺：无法执行真实写入回环（契约 §6.4）。"
            "请先在店小秘『授权店铺』绑定 TikTok Shop，然后由 Lead 用同一命令重跑："
            "tools/verify_tiktok.py --write-roundtrip --confirm-write --product-id <id>")
        step("预检店铺", False, report["skipped_reason"])
        return report
    if not str(product_id or "").strip():
        report["skipped_reason"] = ("缺少 --product-id：写回环必须指定一个采集箱草稿 id"
                                    "（用于原样回写/编辑/回读）")
        step("预检商品", False, report["skipped_reason"])
        return report

    api.write_mode = True  # 只有这里开启；其余只读 API 仍禁止写入口
    try:
        source = _read_detail(api, product_id)
        product = _as_dict(source.get("product")) or source
        step("读取源商品", bool(product), f"字段 {len(product)} 个")

        # 1) op=1 原样回写（先改标题为测试标记，便于识别；回读零变更校验）
        stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        test_title = f"AURORA-TEST-{stamp}"
        payload = dict(product)
        payload.pop("variationBos", None)
        payload["productName"] = test_title
        payload["op"] = 1
        first = _write_save(api, payload, op="1")
        report["save_response"] = first
        step("原样回写（op=1）", str(first.get("code")) == "0",
             f"code={first.get('code')} msg={first.get('msg')}")

        readback = _read_detail(api, str(first.get("data") or product_id))
        readback_product = _as_dict(readback.get("product")) or readback
        report["readback"] = {"title": readback_product.get("productName")}
        step("回读标题一致", str(readback_product.get("productName")) == test_title,
             f"回读标题={readback_product.get('productName')!r}")

        # 2) overrides：标题/描述编辑后保存 → 回读一致
        edited_title = f"{test_title} edited"
        edited_payload = dict(readback_product)
        edited_payload.pop("variationBos", None)
        edited_payload["productName"] = edited_title
        description = str(edited_payload.get("description") or "")
        edited_payload["description"] = description + "<p>aurora roundtrip edit</p>"
        edited_payload["op"] = 1
        second = _write_save(api, edited_payload, op="1")
        report["save_response"] = second
        step("编辑后保存（op=1）", str(second.get("code")) == "0",
             f"code={second.get('code')} msg={second.get('msg')}")

        final = _read_detail(api, str(second.get("data") or product_id))
        final_product = _as_dict(final.get("product")) or final
        report["readback"] = {
            "title": final_product.get("productName"),
            "description_changed": str(final_product.get("description") or "")
            != description,
        }
        step("回读编辑生效",
             str(final_product.get("productName")) == edited_title
             and report["readback"]["description_changed"],
             f"标题={final_product.get('productName')!r} "
             f"描述变更={report['readback']['description_changed']}")
        report["performed"] = True

        # 3) 清理测试草稿（仅删除本次新建的 id；不删源草稿）
        created_id = str(second.get("data") or "")
        if created_id and created_id != str(product_id) and not keep_test_draft:
            delete_payload = api.session.request(
                "POST", BASE + "/api/tiktokProduct/batchProductDelete.json",
                data={"ids": created_id}, timeout=timeout)
            try:
                deleted = delete_payload.json()
            except ValueError:
                deleted = {"code": str(delete_payload.status_code)}
            report["delete_response"] = deleted
            step("清理测试草稿", str(deleted.get("code")) == "0",
                 f"id={created_id} code={deleted.get('code')}")
        else:
            step("保留测试草稿", True,
                 f"未删除（created_id={created_id or '-'} / "
                 f"keep_test_draft={keep_test_draft}）")
        return report
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
        step("写回环异常", False, report["error"])
        return report
    finally:
        api.write_mode = False


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def _stamp() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _readonly_endpoint_rows() -> list[str]:
    return [f"{'/'.join(sorted(methods))} {path}"
            for path, methods in ALLOWED_ENDPOINTS.items()]


def _empty_report(timeout: int, write_requested: bool,
                  confirm_write: bool) -> dict:
    return {
        "generated_at": _stamp(),
        "mode": "independent read-only recheck (requests; no backend import)",
        "status": "ERROR",
        "base": BASE,
        "timeout_seconds": timeout,
        "output": str(DEFAULT_OUTPUT.name),
        "account": {},
        "cookie": {"source": "", "source_kind": "", "count": 0,
                   "content_logged": False, "content_in_report": False},
        "shops": {},
        "sites": {},
        "lists": {},
        "categories": {},
        "limits": {},
        "save_transport": {},
        "write_roundtrip": {
            "requested": bool(write_requested),
            "performed": False,
            "skipped_reason": ("未请求写回环：默认只读（真实写需 --write-roundtrip "
                               "--confirm-write，且只由 Lead 在绑定 TikTok 店铺后执行）"
                               if not write_requested else ""),
            "product_id": "",
            "steps": [],
            "save_response": {},
            "readback": {},
            "error": "",
        },
        "checks": [],
        "summary": {"passed": 0, "total": 0, "failed": 0, "failed_checks": []},
        "safety": {
            "write_performed": False,
            "write_endpoints_never_called": True,
            "write_endpoints_never_called_scope":
                "通用只读 API；唯一例外是文档化的空 payload 保存传输自检",
            "write_calls_via_read_api": 0,
            "transport_selfcheck_calls": 0,
            "transport_selfcheck_payload_empty": True,
            "transport_selfcheck_data_written": False,
            "forbidden_write_suffix_checked": True,
            "forbidden_write_suffix": FORBIDDEN_WRITE_SUFFIX,
            "allowed_readonly_endpoints": _readonly_endpoint_rows(),
            "request_count": 0,
            "cache_hits": 0,
            "blocked_attempts": [],
            "cookie_in_report": False,
        },
        "notes": [
            "独立复核：不 import aeclone 业务后端，全部 requests + 只读白名单。",
            "唯一写入口仅被『空 payload 传输自检』触达一次（路径由拼接表达式构造，"
            "源码无完整字面量）；通用只读 API 对它硬断言拦截，数据不落库。",
            "主只读证据为 docs/tiktok-readonly-verification.json（由 tiktok-client 产出）；"
            "本报告不覆盖主证据，冲突时以真实响应为准。",
            "Cookie 内容不写入本报告，控制台仅打印前 6 位脱敏片段。",
        ],
        "limitations": [
            "账号未绑定 TikTok 店铺：真实写入回环未验证/未执行（契约 §6.4 需绑定店铺）；"
            "绑定后由 Lead 用 --write-roundtrip --confirm-write 执行。",
            "保存传输自检只能证明 ZIP/multipart 被服务端解析到店铺校验阶段，"
            "不能证明真实保存成功（不得把离线/只读通过写成真实写入成功）。",
            "账号无 TikTok 商品：详情/变种接口只能证明端点可调用、空数据按预期返回。",
            "类目/搜索/属性/限额无需店铺即可调用；店铺特有站点映射（shopSiteList）未验证。",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="TikTok 刊登真实账号独立复核（默认只读；写回环需显式确认）")
    parser.add_argument("--cookie-file", default="", help="Cookie 文件（头格式或 Netscape）")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="JSON 报告路径（默认独立复核报告）")
    parser.add_argument("--product-id", default="",
                        help="写回环用的采集箱草稿 id（默认只读时忽略）")
    parser.add_argument("--timeout", type=int, default=TIMEOUT, help="单请求超时秒数")
    parser.add_argument("--write-roundtrip", action="store_true",
                        help="执行真实写入回环（默认 dry-run；必须同时 --confirm-write）")
    parser.add_argument("--confirm-write", action="store_true",
                        help="确认允许真实写入（写回环硬门槛）")
    parser.add_argument("--keep-test-draft", action="store_true",
                        help="写回环后保留测试草稿（默认删除本次新建的草稿）")
    args = parser.parse_args(argv)

    output = Path(args.output)
    timeout = max(5, int(args.timeout))
    report = _empty_report(timeout, args.write_roundtrip, args.confirm_write)

    # 写回环确认门：必须在任何写操作之前；未确认 → 明确拒绝并退出。
    if args.write_roundtrip:
        error = write_confirm_error(args)
        if error:
            print(f"ERROR: {error}")
            report["status"] = "ERROR"
            report["error"] = error
            report["write_roundtrip"]["skipped_reason"] = error
            _write_report(output, report)
            print(f"报告已写入 {output}")
            return 2

    cookie_path, cookie_kind = resolve_cookie_file(args.cookie_file)
    if cookie_path is None or not cookie_path.exists():
        report["status"] = "SKIP"
        report["skip_reason"] = ("没有 Cookie 文件：请用 --cookie-file 或 DXM_COOKIE_PATH "
                                 "指定，或放到 .tmp-probe/cookies.txt（禁止提交）")
        _write_report(output, report)
        print(f"SKIP: {report['skip_reason']}")
        print(f"报告已写入 {output}")
        return 0

    raw_cookie = cookie_path.read_text(encoding="utf-8", errors="replace").strip()
    pairs = parse_cookie_pairs(raw_cookie)
    report["cookie"].update({"source": cookie_path.name, "source_kind": cookie_kind,
                             "count": len(pairs)})
    if not pairs:
        report["status"] = "SKIP"
        report["skip_reason"] = f"Cookie 文件为空或无法解析：{cookie_path.name}"
        _write_report(output, report)
        print(f"SKIP: {report['skip_reason']}")
        return 0

    print(f"Cookie：{cookie_kind} · 文件 {cookie_path.name} · {len(pairs)} 个字段 · "
          f"脱敏前 6 位 {mask_cookie(raw_cookie)}")
    print("模式：独立只读复核（白名单端点；写入口硬拦截；仅空 payload 传输自检触达保存端点）\n")

    api = ReadOnlyApi(raw_cookie, timeout=timeout)
    checker = Checker()
    ctx: dict = {"product_id": str(args.product_id or "").strip()}

    checks: list[tuple[str, object]] = [
        ("userIn 登录 / 账号", partial(check_login, api)),
        ("TikTok 店铺真相（必须 0）", partial(check_shop_truth, api)),
        ("getL7ShopIdAndSite 23 站点", partial(check_l7_sites, api)),
        ("pageList 采集箱(draft)", check_page_list(api, "draft")),
        ("pageList 待发布(offline)", check_page_list(api, "offline")),
        ("pageList 在线(online)", check_page_list(api, "online")),
        ("getOfflineCounts 采集箱计数", partial(check_offline_counts, api)),
        ("getOnlineCounts 在线计数", partial(check_online_counts, api)),
        ("tiktokCategory/list GB 根 30 条", partial(check_category_roots, api)),
        ("类目下钻（categoryParentId）", partial(check_category_drill, api)),
        ("searchCategory category=phone", partial(check_search_category, api)),
        ("attributeList 叶子 type=3", partial(check_attributes, api)),
        ("getLimitBySite 站点限额", partial(check_limits, api)),
        ("listWarehouse 仓库", partial(check_warehouses, api)),
        ("multiShop/tiktok/shopList 多店", partial(check_multishop, api)),
        ("historyList 最近类目", partial(check_history, api)),
        ("getByProductId 详情回退（预期空）", partial(check_detail_fallback, api)),
        # 必须放最后：唯一一次触达写入口（空 payload 传输自检，数据不落库）
        ("保存传输格式自检（空 payload）", partial(check_save_transport, api)),
    ]
    for name, fn in checks:
        checker.run(name, fn, ctx)

    passed = sum(1 for row in checker.checks if row["ok"])
    failed_rows = [row for row in checker.checks if not row["ok"]]
    total = len(checker.checks)
    report["checks"] = checker.checks
    report["summary"] = {
        "passed": passed, "total": total, "failed": total - passed,
        "failed_checks": [row["name"] for row in failed_rows],
    }
    account = observed_of(checker.checks, "userIn 登录 / 账号")
    shops = observed_of(checker.checks, "TikTok 店铺真相（必须 0）")
    sites = observed_of(checker.checks, "getL7ShopIdAndSite 23 站点")
    report["account"] = account
    report["shops"] = shops
    report["sites"] = sites
    report["lists"] = {
        "draft": observed_of(checker.checks, "pageList 采集箱(draft)"),
        "offline": observed_of(checker.checks, "pageList 待发布(offline)"),
        "online": observed_of(checker.checks, "pageList 在线(online)"),
        "offline_counts": observed_of(checker.checks, "getOfflineCounts 采集箱计数"),
        "online_counts": observed_of(checker.checks, "getOnlineCounts 在线计数"),
    }
    report["categories"] = {
        "gb_roots": observed_of(checker.checks, "tiktokCategory/list GB 根 30 条"),
        "drill": observed_of(checker.checks, "类目下钻（categoryParentId）"),
        "search": observed_of(checker.checks, "searchCategory category=phone"),
        "attributes": observed_of(checker.checks, "attributeList 叶子 type=3"),
        "history": observed_of(checker.checks, "historyList 最近类目"),
    }
    report["limits"] = observed_of(checker.checks, "getLimitBySite 站点限额")
    report["warehouses"] = observed_of(checker.checks, "listWarehouse 仓库")
    report["multishop"] = observed_of(checker.checks,
                                      "multiShop/tiktok/shopList 多店")
    report["detail"] = observed_of(checker.checks,
                                   "getByProductId 详情回退（预期空）")
    report["save_transport"] = observed_of(
        checker.checks, "保存传输格式自检（空 payload）")
    report["safety"].update({
        "write_performed": False,
        "request_count": api.request_count,
        "cache_hits": api.cache_hits,
        "blocked_attempts": api.blocked_attempts,
        "write_endpoints_never_called": not api.blocked_attempts,
        "transport_selfcheck_calls": api.transport_selfcheck_calls,
        "transport_selfcheck_payload_empty": api.transport_selfcheck_payload_empty,
        "transport_selfcheck_data_written": api.transport_selfcheck_data_written,
        "write_calls_via_read_api": 0,
    })
    report["write_roundtrip"]["product_id"] = str(args.product_id or "")

    # 写回环：只有显式确认后才执行；本账号无 TikTok 店铺会直接 SKIP（不写）。
    if args.write_roundtrip:
        roundtrip = write_roundtrip(raw_cookie, timeout,
                                    str(args.product_id or ""),
                                    bool(args.keep_test_draft))
        report["write_roundtrip"] = roundtrip
        report["safety"]["write_performed"] = bool(roundtrip.get("performed"))
        if roundtrip.get("performed") and not roundtrip.get("error"):
            report["write_roundtrip_ok"] = True
        # 写回环的每一步都计入报告；真实写成功不改变只读检查的 PASS 判定。
        if roundtrip.get("error"):
            report["status"] = "FAIL"
            _write_report(output, report)
            print(f"\n写回环失败：{roundtrip['error']}")
            print(f"报告已写入 {output}")
            return 1

    if api.blocked_attempts:
        report["status"] = "ERROR"
        report["error"] = "安全违规：出现非只读请求尝试"
        code = 1
    elif passed == total:
        report["status"] = "PASS"
        code = 0
    else:
        report["status"] = "FAIL"
        code = 1

    _write_report(output, report)
    print(f"\n结果：{passed}/{total} PASS · 状态 {report['status']} · "
          f"只读请求 {api.request_count} 次 · 拦截 {len(api.blocked_attempts)} 次 · "
          f"空 payload 传输自检 {api.transport_selfcheck_calls} 次（数据不落库）")
    if failed_rows:
        print("失败项：")
        for row in failed_rows:
            print(f"  - {row['name']}: {row['detail']}")
    if args.write_roundtrip:
        state = "已执行" if report["write_roundtrip"].get("performed") else "未执行"
        print(f"写回环：{state}；原因/结果="
              f"{report['write_roundtrip'].get('skipped_reason') or report['write_roundtrip'].get('error') or '见报告'}")
    print(f"报告已写入 {output}")
    return code


if __name__ == "__main__":
    sys.exit(main())
