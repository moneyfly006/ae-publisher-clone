"""Amazon 刊登 · 真实账号独立只读核验（requests 自实现，不 import 后端模块）。

目的
----
按 ``docs/AMAZON_DESIGN.md`` §1/§8 的契约，对真实店小秘账号做**只读**核验：
登录态 / 店铺 / 站点解析 / 三态列表计数 / 商品详情 / 类目属性 / 类目树逐级 /
按路径取类目 / 最近使用类目 / 产品类型 / 币种 / 运费模板 / 品牌 / 违禁词 /
产品类型预测。每项输出 PASS/FAIL 与关键字段，报告写入
``docs/amazon-readonly-verification.json``。

安全边界（硬约束，代码内断言）
------------------------------
- 只允许白名单内的只读端点；任何未知路径直接抛错并记录 ``blocked_attempts``。
- 唯一写入口（保存/移入待发布/发布）以符号常量 ``FORBIDDEN_WRITE_SUFFIX`` 拼出，
  源码中不出现该写入路径的完整字面量；请求守卫对它做显式断言拦截。
- 所有 POST 都只是「只读查询」端点；本脚本没有任何写开关。
- Cookie 只从 ``--cookie-file`` / ``DXM_COOKIE_PATH`` / ``.tmp-probe/cookies.txt``
  读取到内存；控制台只打印前 6 位脱敏片段，报告文件不写任何 Cookie 内容。
- 不读/不写 ``.tmp-probe`` 的其它抓包文件，不改任何业务代码，不做 git 操作。

跑法
----
    .venv/bin/python tools/verify_amazon.py
    .venv/bin/python tools/verify_amazon.py --cookie-file /path/to/cookies.txt
    DXM_COOKIE_PATH=/path/to/cookies.txt .venv/bin/python tools/verify_amazon.py

退出码：全 PASS → 0；有 FAIL → 1；Cookie 缺失/过期 → SKIP 且 0（仍写报告）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
import time
from functools import partial
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COOKIE_FILE = ROOT / ".tmp-probe" / "cookies.txt"
DEFAULT_OUTPUT = ROOT / "docs" / "amazon-readonly-verification.json"

BASE = "https://www.dianxiaomi.com"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TIMEOUT = 30

# ---- 契约期望值（docs/AMAZON_DESIGN.md §0/§1） ------------------------------
EXPECT = {
    "account": "QINGHEMAOYI",
    "puid": 2728813,
    "shop_id": "9408005",
    "shop_name": "QingHe",
    "merchant_id": "A26OV9RTCAYJMS",
    "default_marketplace": "ATVPDKIKX0DER",
    "sites": {
        # 站点代码 -> (marketPlaceId, 币种, markeyPlaceLang)
        "US": ("ATVPDKIKX0DER", "USD", "en_US"),
        "CA": ("A2EUQ1WTGCTBG2", "CAD", "en_CA"),
        "MX": ("A1AM78C64UM0Y8", "MXN", "es_MX"),
        "BR": ("A2Q3Y263D00KWC", "BRL", "pt_BR"),
    },
    "counts": {"draft": 16, "offline": 5, "online": 142},
    "wait_publish": 5,
    "publish_fail": 21,
    "sample_product_id": "220114595363182310",
    "sample_site": "CA",
    "sample_marketplace": "A2EUQ1WTGCTBG2",
    "sample_category_id": "13553005011",
    "sample_category_path": "6299024011/6256963011/6256964011/13553005011",
    "sample_product_fields": 164,
    "category_roots": 17,
    "predict_item_name": "Backhoe Loader",   # 长标题会返回 code=-1（预测失败），用短词演示可用路径
}

# ---- 只读端点白名单（路径 -> 允许的方法） -----------------------------------
ALLOWED_ENDPOINTS: dict[str, frozenset[str]] = {
    "/api/userIn.json": frozenset({"GET"}),
    "/api/amazonProduct/getCountryByShopId.json": frozenset({"POST"}),
    "/api/amazonProduct/pageList.json": frozenset({"POST"}),
    "/api/amazonProductSp/editSp.json": frozenset({"GET"}),
    "/api/amazonProductSp/getProductAttrById.json": frozenset({"POST"}),
    "/api/amazonProductSp/list.json": frozenset({"POST"}),
    "/api/amazonProductSp/getCategoryBySiteNodePath.json": frozenset({"POST"}),
    "/api/amazonProductSp/listHistory.json": frozenset({"POST"}),
    "/api/amazonProductSp/gerProductTypes.json": frozenset({"POST"}),
    "/api/amazon/currency/list.json": frozenset({"POST"}),
    "/api/amazon/logistics/getLogisticsTemplates.json": frozenset({"POST"}),
    "/api/amazon/brand/getBrands.json": frozenset({"POST"}),
    "/api/bannedWord/amazonCheckBannedWord.json": frozenset({"POST"}),
    "/api/amazonProductSp/productTypePredictCatesByItemName.json": frozenset({"POST"}),
}

# 唯一写入口以拼接方式表达：源码里不出现完整写路径字面量，契约测试会反向断言。
FORBIDDEN_WRITE_SUFFIX = "add" + ".json"


# ---------------------------------------------------------------------------
# Cookie 读取 / 脱敏
# ---------------------------------------------------------------------------
def resolve_cookie_file(explicit: str = "") -> tuple[Path | None, str]:
    """按 --cookie-file → DXM_COOKIE_PATH → .tmp-probe/cookies.txt 解析。"""
    if explicit:
        return Path(explicit).expanduser(), "--cookie-file"
    env_path = str(os.environ.get("DXM_COOKIE_PATH", "") or "").strip()
    if env_path:
        return Path(env_path).expanduser(), "DXM_COOKIE_PATH"
    if DEFAULT_COOKIE_FILE.exists():
        return DEFAULT_COOKIE_FILE, "default .tmp-probe/cookies.txt"
    return None, "missing"


def parse_cookie_pairs(raw: str) -> list[tuple[str, str]]:
    """同时兼容 `a=1; b=2` 头格式与 Netscape cookies.txt（制表符分隔）。"""
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
    """控制台脱敏：只显示前 6 位 + 省略号。"""
    text = str(raw or "").strip()
    return (text[:6] + "…") if text else "(空)"


# ---------------------------------------------------------------------------
# 只读请求守卫：白名单 + 写入口断言
# ---------------------------------------------------------------------------
class ReadOnlyApi:
    """所有真实请求的唯一出口；非白名单/写入口一律拒绝。"""

    def __init__(self, cookie_header: str, timeout: int = TIMEOUT):
        self.timeout = timeout
        self.request_count = 0
        self.cache_hits = 0
        self._cache: dict[str, dict] = {}
        self.blocked_attempts: list[dict] = []
        self.session = requests.Session()
        for name, value in parse_cookie_pairs(cookie_header):
            self.session.cookies.set(name, value, domain=".dianxiaomi.com")
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{BASE}/web/amazon/pageList/draft",
        })

    def _guard(self, method: str, path: str) -> None:
        method = str(method).upper()
        # 显式断言：唯一写入口绝不能被调用；未知端点也不放行。
        if FORBIDDEN_WRITE_SUFFIX in path:
            self.blocked_attempts.append({"method": method, "path": path,
                                          "reason": "forbidden write endpoint"})
            raise RuntimeError("只读核验禁止调用唯一写入口（保存/发布端点）")
        allowed = ALLOWED_ENDPOINTS.get(path)
        if allowed is None:
            self.blocked_attempts.append({"method": method, "path": path,
                                          "reason": "path not in read-only allowlist"})
            raise RuntimeError(f"只读核验拒绝未白名单端点：{path}")
        if method not in allowed:
            self.blocked_attempts.append({"method": method, "path": path,
                                          "reason": "method not allowed"})
            raise RuntimeError(f"只读核验拒绝方法 {method} {path}")

    def _send(self, method: str, path: str, **kwargs) -> dict:
        # 同一只读查询在本次核验内只发一次：既降负载，也避免触发平台频控。
        cache_key = json.dumps(
            {"method": method, "path": path,
             "params": kwargs.get("params"), "data": kwargs.get("data")},
            sort_keys=True, ensure_ascii=False, default=str)
        if cache_key in self._cache:
            self.cache_hits += 1
            return self._cache[cache_key]
        payload: dict = {}
        for attempt in range(2):  # 频控（code=2005）时最多重试一次
            self._guard(method, path)
            self.request_count += 1
            response = self.session.request(method, BASE + path, timeout=self.timeout, **kwargs)
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code} {method} {path}")
            try:
                payload = response.json()
            except ValueError as exc:  # 登录失效时可能返回 HTML
                snippet = response.text[:80].replace("\n", " ")
                raise RuntimeError(f"非 JSON 响应（可能是登录态失效）：{snippet}") from exc
            if not isinstance(payload, dict):
                raise RuntimeError(f"响应不是 JSON 对象：{type(payload).__name__}")
            if str(payload.get("code")) in ("2005", "429") and attempt == 0:
                print(f"[WAIT] {path} 返回频控 code={payload.get('code')}，2 秒后重试一次")
                time.sleep(2)
                continue
            break
        self._cache[cache_key] = payload
        return payload

    def get(self, path: str, params: dict | None = None) -> dict:
        return self._send("GET", path, params=params or {})

    def post(self, path: str, data: dict | None = None) -> dict:
        return self._send("POST", path, data=data or {})


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
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# 各项只读检查
# ---------------------------------------------------------------------------
def check_login(api: ReadOnlyApi, ctx: dict) -> tuple[bool, str, dict]:
    payload = api.get("/api/userIn.json")
    data = _data(payload) or {}
    shops = data.get("shopMap") or {}
    shop = shops.get(str(EXPECT["shop_id"])) or {}
    ok = (
        _code(payload) == "0"
        and str(data.get("account")) == EXPECT["account"]
        and _as_int(data.get("puid")) == int(EXPECT["puid"])
        and isinstance(shops, dict)
        and str(shop.get("name")) == EXPECT["shop_name"]
        and str(shop.get("merchantId")) == EXPECT["merchant_id"]
        and str(shop.get("marketPlaceId")) == EXPECT["default_marketplace"]
    )
    ctx["account"] = str(data.get("account") or "")
    ctx["puid"] = _as_int(data.get("puid"))
    detail = (f"code={_code(payload)} account={data.get('account')} puid={data.get('puid')} "
              f"店铺={shop.get('name')} merchantId={shop.get('merchantId')} "
              f"默认站点={shop.get('marketPlaceId')}")
    return ok, detail, {
        "account": data.get("account"),
        "puid": data.get("puid"),
        "shop_id": shop.get("id"),
        "shop_name": shop.get("name"),
        "merchant_id": shop.get("merchantId"),
        "default_marketplace": shop.get("marketPlaceId"),
        "shop_ids": sorted(str(k) for k in shops)[:10],
    }


def check_sites(api: ReadOnlyApi, ctx: dict) -> tuple[bool, str, dict]:
    payload = api.post("/api/amazonProduct/getCountryByShopId.json",
                       {"shopId": EXPECT["shop_id"]})
    data = _data(payload) or {}
    raw_sites = str(data.get("strCountry") or "")
    langs = data.get("markeyPlaceLang") or {}
    parsed = []
    for segment in raw_sites.split("#"):
        if not segment.strip():
            continue
        parts = [p.strip() for p in segment.split(",")]
        if len(parts) < 5:
            continue
        country, _region, currency, code, marketplace = parts[:5]
        parsed.append({"country": country, "currency": currency,
                       "code": code, "marketplace": marketplace,
                       "lang": langs.get(marketplace)})
    by_code = {row["code"]: row for row in parsed}
    ok = _code(payload) == "0" and bool(parsed)
    for code, (marketplace, currency, lang) in EXPECT["sites"].items():
        row = by_code.get(code)
        ok = ok and bool(row) and row["marketplace"] == marketplace \
            and row["currency"] == currency and row["lang"] == lang
    detail = ("站点 " + str(len(parsed)) + " 个：" + " · ".join(
        f"{row['country']} {row['code']} {row['currency']} {row['marketplace']} ({row['lang']})"
        for row in parsed))
    return ok, detail, {"sites": parsed, "markeyPlaceLang": langs,
                        "strCountry": raw_sites}


def _page_list(api: ReadOnlyApi, state: str):
    return api.post("/api/amazonProduct/pageList.json", {
        "pageNo": 1, "pageSize": 20, "shopId": "-1",
        "dxmState": state, "searchValue": "",
    })


def check_page_list(api: ReadOnlyApi, state: str):
    def _run(_ctx) -> tuple[bool, str, dict]:
        payload = _page_list(api, state)
        data = _data(payload) or {}
        page = data.get("page") or {}
        rows = page.get("list") or []
        total = _as_int(page.get("totalSize"))
        expected = int(EXPECT["counts"][state])
        ok = _code(payload) == "0" and total == expected and isinstance(rows, list)
        detail = (f"code={_code(payload)} totalSize={total}（期望 {expected}）"
                  f" pageNo={page.get('pageNo')} rows={len(rows)}")
        return ok, detail, {
            "state": state,
            "total_size": total,
            "expected": expected,
            "rows_on_page": len(rows),
            "page_no": page.get("pageNo"),
            "total_page": page.get("totalPage"),
        }
    return _run


def check_left_stat(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = _page_list(api, "draft")
    data = _data(payload) or {}
    left = data.get("leftStat") or {}
    wait = _as_int(left.get("waitPublish"))
    fail = _as_int(left.get("publishFail"))
    ok = (_code(payload) == "0" and wait == int(EXPECT["wait_publish"])
          and fail == int(EXPECT["publish_fail"]))
    detail = f"leftStat.waitPublish={wait}（期望 {EXPECT['wait_publish']}）· " \
             f"publishFail={fail}（期望 {EXPECT['publish_fail']}）"
    return ok, detail, {"leftStat": left}


def check_edit_sp(api: ReadOnlyApi, ctx: dict) -> tuple[bool, str, dict]:
    product_id = ctx.get("product_id") or EXPECT["sample_product_id"]
    payload = api.get("/api/amazonProductSp/editSp.json", {"id": product_id})
    data = _data(payload) or {}
    product = data.get("product") or {}
    required = [
        "id", "shopId", "marketPlaceId", "categoryId", "categoryBrowsePath",
        "sku", "title", "brand", "manufacturer", "description", "bulletPoint",
        "searchTerms", "standardPrice", "saleSalePrice", "mainImage",
        "extraImages", "productAttr", "productData", "isVariation", "variations",
    ]
    missing = [key for key in required if key not in product]
    bullet = str(product.get("bulletPoint") or "")
    description = str(product.get("description") or "")
    attr_raw = product.get("productAttr")
    attr_ok = False
    attr_keys = 0
    if isinstance(attr_raw, str) and attr_raw.strip():
        try:
            parsed_attr = json.loads(attr_raw)
            attr_ok = isinstance(parsed_attr, dict) and bool(parsed_attr)
            attr_keys = len(parsed_attr) if isinstance(parsed_attr, dict) else 0
        except ValueError:
            attr_ok = False
    ok = (
        _code(payload) == "0"
        and str(data.get("site")) == EXPECT["sample_site"]
        and str(product.get("id")) == product_id
        and len(product) == int(EXPECT["sample_product_fields"])
        and not missing
        and bool(bullet) and "±" in bullet
        and bool(description) and "<" in description
        and attr_ok
        and str(product.get("categoryId")) == EXPECT["sample_category_id"]
        and str(product.get("categoryBrowsePath")) == EXPECT["sample_category_path"]
    )
    detail = (f"code={_code(payload)} site={data.get('site')} 字段数={len(product)}"
              f"（期望 {EXPECT['sample_product_fields']}） bulletPoint={len(bullet)}字符"
              f"±={bullet.count('±')+1 if bullet else 0}段 description={len(description)}字符 "
              f"productAttr={attr_keys}键 缺字段={missing or '无'}")
    return ok, detail, {
        "site": data.get("site"),
        "field_count": len(product),
        "missing_fields": missing,
        "title": str(product.get("title") or "")[:120],
        "bullets": (bullet.split("±") if bullet else []),
        "description_html": description.startswith("<") and ">" in description,
        "productAttr_keys": attr_keys,
        "productType": data.get("productType"),
        "categoryId": product.get("categoryId"),
        "categoryBrowsePath": product.get("categoryBrowsePath"),
    }


def check_product_attr(api: ReadOnlyApi, ctx: dict) -> tuple[bool, str, dict]:
    product_id = ctx.get("product_id") or EXPECT["sample_product_id"]
    payload = api.post("/api/amazonProductSp/getProductAttrById.json", {
        "id": product_id, "site": EXPECT["sample_marketplace"],
    })
    raw = _data(payload)
    attrs = None
    if isinstance(raw, str):
        try:
            attrs = json.loads(raw)
        except ValueError as exc:
            raise RuntimeError(f"data 不是合法 JSON 字符串：{exc}") from exc
    elif isinstance(raw, dict):
        attrs = raw
    else:
        raise RuntimeError(f"data 类型异常：{type(raw).__name__}")
    sample_keys = ["is_assembly_required", "batteries_required",
                   "item_dimensions", "item_package_dimensions"]
    present = [key for key in sample_keys if key in attrs]
    ok = _code(payload) == "0" and isinstance(attrs, dict) and len(attrs) > 0 \
        and bool(present)
    detail = (f"code={_code(payload)} 属性={len(attrs) if isinstance(attrs, dict) else 0}键"
              f" 覆盖样例键={present}")
    return ok, detail, {
        "attribute_count": len(attrs) if isinstance(attrs, dict) else 0,
        "sample_keys_present": present,
        "data_is_json_string": isinstance(raw, str),
    }


def check_category_tree(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    levels = []
    parent: object = 0
    ok = True
    for depth in range(4):
        payload = api.post("/api/amazonProductSp/list.json", {
            "site": EXPECT["sample_site"], "parentId": str(parent),
        })
        rows = _data(payload)
        if _code(payload) != "0" or not isinstance(rows, list) or not rows:
            ok = False
            levels.append({"depth": depth, "parentId": str(parent), "error": "empty/invalid"})
            break
        node = rows[0]
        internal_id = node.get("id")
        if depth == 0:
            ok = ok and len(rows) == int(EXPECT["category_roots"])
        else:
            # 逐级下钻必须用内部 id：返回节点的 parentId 应等于请求的 parentId。
            ok = ok and str(node.get("parentId")) == str(parent)
        ok = ok and isinstance(internal_id, int) \
            and str(node.get("categoryId") or "").isdigit()
        levels.append({
            "depth": depth,
            "parentId": str(parent),
            "count": len(rows),
            "first_id": internal_id,
            "categoryId": node.get("categoryId"),
            "categoryName": node.get("categoryName"),
            "chineseName": node.get("chineseName"),
            "nodePath": node.get("nodePath"),
            "childIds": len(node.get("childIds") or []),
        })
        child_ids = node.get("childIds") or []
        if not child_ids:
            break
        parent = internal_id
    ok = ok and len(levels) >= 3
    detail = ("逐级 " + " → ".join(
        f"{row.get('categoryName')}(内部id={row.get('first_id')},子={row.get('count')})"
        for row in levels) + f" · 顶层={levels[0].get('count') if levels else 0}")
    return ok, detail, {"levels": levels}


def check_category_by_path(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    path = EXPECT["sample_category_path"]
    payload = api.post("/api/amazonProductSp/getCategoryBySiteNodePath.json", {
        "site": EXPECT["sample_site"], "nodePath": path,
    })
    node = _data(payload)
    ok = (_code(payload) == "0" and isinstance(node, dict)
          and str(node.get("categoryId")) == EXPECT["sample_category_id"]
          and str(node.get("nodePathId")) == path
          and str(node.get("site")) == EXPECT["sample_site"])
    # 反向：不属于该站点的路径应返回 data=null（不能误判为本站点类目）。
    bad = api.post("/api/amazonProductSp/getCategoryBySiteNodePath.json", {
        "site": EXPECT["sample_site"], "nodePath": "1/2/3",
    })
    bad_ok = _code(bad) == "0" and _data(bad) is None
    ok = ok and bad_ok
    detail = (f"code={_code(payload)} categoryId={node.get('categoryId') if isinstance(node, dict) else None}"
              f" nodePathId={(node or {}).get('nodePathId') if isinstance(node, dict) else None}"
              f" 中文名={(node or {}).get('chineseName') if isinstance(node, dict) else None}"
              f" · 非法路径 data=null={bool(bad_ok)}")
    return ok, detail, {
        "valid": {"categoryId": (node or {}).get("categoryId"),
                  "nodePathId": (node or {}).get("nodePathId"),
                  "categoryName": (node or {}).get("categoryName"),
                  "chineseName": (node or {}).get("chineseName"),
                  "leafCategory": (node or {}).get("leafCategory")},
        "invalid_path_data_null": bad_ok,
    }


def check_category_history(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.post("/api/amazonProductSp/listHistory.json", {
        "shopId": EXPECT["shop_id"], "site": EXPECT["sample_site"],
    })
    rows = _data(payload)
    ok = _code(payload) == "0" and isinstance(rows, list) and len(rows) > 0 \
        and all(isinstance(row, dict) and row.get("categoryId") for row in rows)
    detail = f"code={_code(payload)} 最近使用类目={len(rows) if isinstance(rows, list) else 0} 条"
    return ok, detail, {
        "count": len(rows) if isinstance(rows, list) else 0,
        "sample": [{"categoryId": row.get("categoryId"),
                    "categoryName": row.get("categoryName"),
                    "num": row.get("num")} for row in (rows or [])[:5]],
    }


def check_product_types(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.post("/api/amazonProductSp/gerProductTypes.json",
                       {"site": EXPECT["sample_site"]})
    rows = _data(payload)
    ok = (_code(payload) == "0" and isinstance(rows, list) and len(rows) > 100
          and all(isinstance(row, str) for row in rows)
          and "OUTDOOR_LIVING" in rows)
    detail = f"code={_code(payload)} 产品类型={len(rows) if isinstance(rows, list) else 0} 个" \
             f"（含 OUTDOOR_LIVING={isinstance(rows, list) and 'OUTDOOR_LIVING' in rows}）"
    return ok, detail, {
        "count": len(rows) if isinstance(rows, list) else 0,
        "has_outdoor_living": isinstance(rows, list) and "OUTDOOR_LIVING" in rows,
        "sample": (rows or [])[:8],
    }


def check_product_types_marketplace(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    """契约 §1.5 注意项：传 marketPlaceId 会返回空数组（必须用站点代码）。"""
    payload = api.post("/api/amazonProductSp/gerProductTypes.json",
                       {"site": EXPECT["sample_marketplace"]})
    rows = _data(payload)
    ok = _code(payload) == "0" and rows == []
    detail = f"code={_code(payload)} 传 marketPlaceId 返回空数组={rows == []}（契约注意项）"
    return ok, detail, {"data": rows}


def check_currency(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.post("/api/amazon/currency/list.json", {
        "shopId": EXPECT["shop_id"], "marketPlaceId": EXPECT["sample_marketplace"],
    })
    data = _data(payload) or {}
    currency = str(data.get("currency") or "")
    ok = _code(payload) == "0" and currency == EXPECT["sites"]["CA"][1]
    detail = f"code={_code(payload)} currency={currency}（期望 {EXPECT['sites']['CA'][1]}）"
    return ok, detail, {"currency": currency,
                        "marketPlaceId": data.get("marketPlaceId"),
                        "shopId": data.get("shopId")}


def check_logistics(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.post("/api/amazon/logistics/getLogisticsTemplates.json", {
        "shopId": EXPECT["shop_id"], "marketPlaceId": EXPECT["sample_marketplace"],
    })
    rows = _data(payload)
    ok = _code(payload) == "0" and isinstance(rows, list) and len(rows) > 0 \
        and all(isinstance(row, dict) and row.get("templateId") for row in rows)
    detail = f"code={_code(payload)} 运费模板={len(rows) if isinstance(rows, list) else 0} 个"
    return ok, detail, {
        "count": len(rows) if isinstance(rows, list) else 0,
        "sample": [{"templateId": row.get("templateId"),
                    "templateName": row.get("templateName")} for row in (rows or [])[:5]],
    }


def check_brands(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.post("/api/amazon/brand/getBrands.json", {
        "shopId": EXPECT["shop_id"], "marketPlaceId": EXPECT["sample_marketplace"],
    })
    rows = _data(payload)
    # 品牌列表允许为空（未做品牌备案的店铺）。
    ok = _code(payload) == "0" and isinstance(rows, list)
    detail = f"code={_code(payload)} 品牌={len(rows) if isinstance(rows, list) else '非数组'} 个（可为空）"
    return ok, detail, {"count": len(rows) if isinstance(rows, list) else None,
                        "data_is_list": isinstance(rows, list)}


def check_banned_word(api: ReadOnlyApi, ctx: dict) -> tuple[bool, str, dict]:
    title = "Best Backhoe Loader 100% Free Shipping"
    payload = api.post("/api/bannedWord/amazonCheckBannedWord.json", {
        "title": title,
        "description": "<p>Guarantee best quality</p>",
        "bulletPoint": "Heavy duty±Diesel engine",
        "searchTerms": "backhoe loader",
    })
    data = _data(payload)
    # 契约 §1.5：data 可为 null，仅提示不阻断。
    ok = _code(payload) == "0" and (data is None or isinstance(data, (list, dict)))
    detail = (f"code={_code(payload)} data={json.dumps(data, ensure_ascii=False)[:120] if data is not None else 'null'}"
              f"（data 可为 null；仅提示，不阻断）")
    return ok, detail, {"data": data, "data_is_null": data is None}


def check_predict(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.post("/api/amazonProductSp/productTypePredictCatesByItemName.json", {
        "shopId": EXPECT["shop_id"], "site": EXPECT["sample_site"],
        "itemName": EXPECT["predict_item_name"],
    })
    data = _data(payload)
    ok = (_code(payload) == "0" and isinstance(data, dict)
          and bool(str(data.get("productType") or ""))
          and "amaoznCategorySearchVos" in data)
    detail = (f"code={_code(payload)} itemName={EXPECT['predict_item_name']!r} "
              f"productType={(data or {}).get('productType') if isinstance(data, dict) else data}")
    return ok, detail, {"data": data}


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------
def _stamp() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _readonly_endpoint_rows() -> list[str]:
    return [f"{'/'.join(sorted(methods))} {path}" for path, methods in ALLOWED_ENDPOINTS.items()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Amazon 刊登真实账号只读核验（绝不写）")
    parser.add_argument("--cookie-file", default="", help="Cookie 文件（头格式或 Netscape）")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="JSON 报告路径")
    parser.add_argument("--product-id", default=EXPECT["sample_product_id"],
                        help="用于详情/属性的样例采集箱商品 id")
    parser.add_argument("--timeout", type=int, default=TIMEOUT, help="单请求超时秒数")
    args = parser.parse_args(argv)

    output = Path(args.output)
    report: dict = {
        "generated_at": _stamp(),
        "mode": "read-only",
        "status": "ERROR",
        "base": BASE,
        "account": {},
        "cookie": {"source": "", "source_kind": "", "count": 0, "content_logged": False},
        "checks": [],
        "summary": {"passed": 0, "total": 0, "failed": 0, "failed_checks": []},
        "safety": {
            "write_endpoints_never_called": True,
            "forbidden_write_suffix_checked": True,
            "allowed_readonly_endpoints": _readonly_endpoint_rows(),
            "request_count": 0,
            "blocked_attempts": [],
        },
        "notes": [
            "独立实现：不 import 后端 aeclone 模块，全部走 requests + 只读白名单。",
            "唯一写入口由 FORBIDDEN_WRITE_SUFFIX 拼接判定，代码内断言永不调用；没有任何写开关。",
            "Cookie 内容不写入本报告，控制台仅打印前 6 位脱敏片段。",
        ],
        "limitations": [
            "违禁词接口 data 允许为 null：本轮只能证明端点可调用，不能证明具体词库命中规则。",
            "产品类型预测为 best-effort：长标题会返回 code=-1『预测失败』，本轮用短词验证可用路径。",
            "品牌列表可为空；空列表视为端点正常（未做品牌备案）。",
            "核验只读，不验证保存/发布写链路（真实写证据见 docs/amazon-write-verification.json）。",
        ],
    }

    cookie_path, cookie_kind = resolve_cookie_file(args.cookie_file)
    if cookie_path is None or not cookie_path.exists():
        report["status"] = "SKIP"
        report["skip_reason"] = ("没有 Cookie 文件：请用 --cookie-file 或 DXM_COOKIE_PATH 指定，"
                                 "或放到 .tmp-probe/cookies.txt（.gitignore 内，禁止提交）")
        _write_report(output, report)
        print(f"SKIP: {report['skip_reason']}")
        print(f"报告已写入 {output}")
        return 0

    raw_cookie = cookie_path.read_text(encoding="utf-8", errors="replace").strip()
    pairs = parse_cookie_pairs(raw_cookie)
    report["cookie"].update({
        "source": cookie_path.name,
        "source_kind": cookie_kind,
        "count": len(pairs),
    })
    if not pairs:
        report["status"] = "SKIP"
        report["skip_reason"] = f"Cookie 文件为空或无法解析：{cookie_path.name}"
        _write_report(output, report)
        print(f"SKIP: {report['skip_reason']}")
        return 0

    print(f"Cookie：{cookie_kind} · 文件 {cookie_path.name} · {len(pairs)} 个字段 · "
          f"脱敏前 6 位 {mask_cookie(raw_cookie)}")
    print("模式：只读（白名单端点；写入口硬拦截）\n")

    api = ReadOnlyApi(raw_cookie, timeout=max(5, int(args.timeout)))
    checker = Checker()
    ctx: dict = {"product_id": str(args.product_id or EXPECT["sample_product_id"])}

    checks: list[tuple[str, object]] = [
        ("userIn 登录 / 店铺", partial(check_login, api)),
        ("getCountryByShopId 站点解析", partial(check_sites, api)),
        ("pageList 采集箱(draft) 计数", check_page_list(api, "draft")),
        ("pageList 待发布(offline) 计数", check_page_list(api, "offline")),
        ("pageList 在线(online) 计数", check_page_list(api, "online")),
        ("pageList leftStat 待发布/发布失败", partial(check_left_stat, api)),
        ("editSp 商品详情", partial(check_edit_sp, api)),
        ("getProductAttrById 类目属性", partial(check_product_attr, api)),
        ("类目树逐级（parentId=内部 id）", partial(check_category_tree, api)),
        ("getCategoryBySiteNodePath 路径校验", partial(check_category_by_path, api)),
        ("listHistory 最近使用类目", partial(check_category_history, api)),
        ("gerProductTypes 产品类型（站点代码）", partial(check_product_types, api)),
        ("gerProductTypes 传 marketPlaceId 返回空", partial(check_product_types_marketplace, api)),
        ("currency 币种", partial(check_currency, api)),
        ("logistics 运费模板", partial(check_logistics, api)),
        ("brand 品牌", partial(check_brands, api)),
        ("amazonCheckBannedWord 违禁词", partial(check_banned_word, api)),
        ("productTypePredictCatesByItemName 类型预测", partial(check_predict, api)),
    ]
    for name, fn in checks:
        checker.run(name, fn, ctx)

    passed = sum(1 for row in checker.checks if row["ok"])
    failed_rows = [row for row in checker.checks if not row["ok"]]
    total = len(checker.checks)
    report["checks"] = checker.checks
    report["summary"] = {
        "passed": passed,
        "total": total,
        "failed": total - passed,
        "failed_checks": [row["name"] for row in failed_rows],
    }
    report["account"] = {
        "account": ctx.get("account", ""),
        "puid": ctx.get("puid", 0),
        "expected_account": EXPECT["account"],
        "expected_puid": EXPECT["puid"],
        "shop_id": EXPECT["shop_id"],
        "shop_name": EXPECT["shop_name"],
    }
    report["safety"].update({
        "request_count": api.request_count,
        "cache_hits": api.cache_hits,
        "blocked_attempts": api.blocked_attempts,
        "write_endpoints_never_called": not api.blocked_attempts,
    })

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
          f"只读请求 {api.request_count} 次 · 拦截 {len(api.blocked_attempts)} 次")
    if failed_rows:
        print("失败项：")
        for row in failed_rows:
            print(f"  - {row['name']}: {row['detail']}")
    print(f"报告已写入 {output}")
    return code


if __name__ == "__main__":
    sys.exit(main())
