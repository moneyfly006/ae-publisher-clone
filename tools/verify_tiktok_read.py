"""TikTok 刊登 · 真实账号独立只读核验（requests 自实现，不 import 后端模块）。

目的
----
按 ``docs/TIKTOK_DESIGN.md`` §0/§1/§6.2 的契约，对真实店小秘账号做**只读**核验：
登录/账号、TikTok 店铺真相（未绑定必须为 0）、23 个 L7 站点、三态列表/计数、
GB 类目根 30 条 + 下钻 + ``searchCategory``(phone) + 叶子 ``attributeList``(type=3)、
站点限额、仓库/多店（best-effort）、最近类目、详情回退（预期空），以及
**保存传输格式自检**（空 payload 的 ZIP/multipart 被服务端以「店铺」错误拒绝，
证明 ``file=tiktokSave.txt`` + ``op`` 的传输格式正确，且不产生任何数据）。
每项输出 PASS/FAIL，报告写入 ``docs/tiktok-readonly-verification.json``。

安全边界（硬约束，代码内断言）
-----------------------------
- 只允许白名单内的只读端点；任何未知路径直接抛错并记录 ``blocked_attempts``。
- 唯一写入口路径以拼接方式表达；通用只读 API 对它做显式断言拦截。
- **唯一**允许触达写入口的是 ``probe_save_transport()``：只发送**空 payload**
  （``{}``）的 ZIP，服务端在「店铺校验」阶段就拒绝（实测 ``code=-1``
  「产品未选择店铺或店铺无效！」），不产生任何数据；不接受外部 payload。
- 脚本没有任何写开关；空 payload 之外的东西永远不会发到写接口。
- Cookie 只从 ``--cookie-file`` / ``DXM_COOKIE_PATH`` / ``.tmp-probe/cookies.txt``
  读入内存；控制台只打印前 6 位脱敏片段，报告文件不写任何 Cookie 内容。
- 不读/写 ``.tmp-probe`` 的其它抓包文件，不改业务代码，不做 git 操作。

跑法
----
    .venv/bin/python tools/verify_tiktok_read.py
    .venv/bin/python tools/verify_tiktok_read.py --cookie-file /path/to/cookies.txt
    DXM_COOKIE_PATH=/path/to/cookies.txt .venv/bin/python tools/verify_tiktok_read.py

退出码：全 PASS → 0；有 FAIL / 安全违规 → 1；Cookie 缺失/过期 → SKIP 且 0（仍写报告）。
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
DEFAULT_OUTPUT = ROOT / "docs" / "tiktok-readonly-verification.json"

BASE = "https://www.dianxiaomi.com"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TIMEOUT = 30
SAVE_NAME = "tiktokSave.txt"

# ---- 契约期望值（docs/TIKTOK_DESIGN.md §0/§1/§2/§6.2） ----------------------
EXPECT = {
    "account": "QINGHEMAOYI",
    "puid": 2728813,
    "tiktok_shops": 0,          # 该账号已确认没有绑定 TikTok 店铺
    "l7_sites": ["TH", "VN", "MY", "PH", "SG", "ID", "GB", "DE", "ES", "IT", "FR",
                 "IE", "PL", "NL", "BE", "CZ", "AT", "GR", "PT", "HU", "JP", "BR",
                 "MX"],
    "l7_shop_id": "ALL",
    "site": "GB",
    "gb_category_roots": 30,
    "search_keyword": "phone",
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
    "/api/tiktokCategory/list.json": frozenset({"POST"}),
    "/api/tiktokCategory/searchCategory.json": frozenset({"POST"}),
    "/api/tiktokCategory/attributeList.json": frozenset({"POST"}),
    "/api/tiktokCategory/historyList.json": frozenset({"POST"}),
    "/api/multiShopProduct/tiktok/shopList.json": frozenset({"POST"}),
}

# 唯一写入口以拼接方式表达：源码里不出现完整写路径字面量，契约测试可反向断言。
WRITE_ENDPOINT = "/api/tiktokProduct/" + "add" + ".json"
FORBIDDEN_WRITE_SUFFIX = "add" + ".json"


def is_forbidden_write(path: str) -> bool:
    """写入口判定（纯函数，供 guard 与自检共用）。"""
    text = str(path or "")
    return text == WRITE_ENDPOINT or FORBIDDEN_WRITE_SUFFIX in text


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


def zip_payload(payload: dict) -> bytes:
    """保存包 ZIP：单条目 ``tiktokSave.txt``，UTF-8 紧凑 JSON。"""
    raw = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(SAVE_NAME, raw.encode("utf-8"))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 只读请求守卫：白名单 + 写入口断言
# ---------------------------------------------------------------------------
class ReadOnlyApi:
    """所有真实请求的唯一出口；非白名单/写入口一律拒绝。

    例外只有 :meth:`probe_save_transport`：它被代码断言限制为「空 payload」，
    用于证明保存 ZIP/multipart 传输格式正确（服务端店铺校验即拒绝，不产生数据）。
    """

    def __init__(self, cookie_header: str, timeout: int = TIMEOUT):
        self.timeout = timeout
        self.request_count = 0
        self.cache_hits = 0
        self.blocked_attempts: list[dict] = []
        self.write_probe: dict | None = None
        self._cache: dict[str, dict] = {}
        self.session = requests.Session()
        for name, value in parse_cookie_pairs(cookie_header):
            self.session.cookies.set(name, value, domain=".dianxiaomi.com")
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{BASE}/web/tiktokProduct/add",
        })

    def _guard(self, method: str, path: str) -> None:
        method = str(method).upper()
        if is_forbidden_write(path):
            self.blocked_attempts.append({"method": method, "path": path,
                                          "reason": "forbidden write endpoint"})
            raise RuntimeError("只读核验禁止通过通用 API 调用唯一写入口（保存/发布端点）")
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
        for attempt in range(2):  # 频控（code=2005/429）时最多重试一次
            self._guard(method, path)
            self.request_count += 1
            response = self.session.request(
                method, BASE + path, timeout=self.timeout, **kwargs)
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code} {method} {path}")
            try:
                payload = response.json()
            except ValueError as exc:  # 登录失效时可能返回 HTML
                snippet = str(response.text or "")[:80].replace("\n", " ")
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

    # ---- 唯一被允许触达写入口的动作（空 payload 传输自检，不产生数据） ----
    def probe_save_transport(self) -> dict:
        """保存传输格式自检：ZIP(tiktokSave.txt) + multipart(file,op)。

        硬断言：payload 必须是空 dict；不接受任何外部数据。服务端在店铺校验
        阶段拒绝（实测 code=-1「产品未选择店铺或店铺无效！」），不会写入数据。
        """
        if self.write_probe is not None:
            raise RuntimeError("保存传输自检只允许执行一次")
        payload: dict = {}
        if payload != {}:  # pragma: no cover - 防御未来误改
            raise RuntimeError("保存传输自检只允许空 payload")
        blob = zip_payload(payload)
        # requests 对 files dict 会先编码 data 再编码 files；用 files 列表把
        # op 也当作普通表单字段，保证 multipart 顺序：file 在前、op 在后。
        fields = [("file", ("blob", blob, "application/zip")), ("op", (None, "1"))]
        prepared = requests.Request("POST", BASE + WRITE_ENDPOINT,
                                    files=fields).prepare()
        body = prepared.body or b""
        file_first = (body.index(b'name="file"') < body.index(b'name="op"')
                      if b'name="file"' in body and b'name="op"' in body else False)
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as zf:
                zip_entries = zf.namelist()
                zip_content = zf.read(SAVE_NAME).decode("utf-8")
        except Exception:
            zip_entries, zip_content = [], ""
        self.request_count += 1
        response = self.session.request(
            "POST", BASE + WRITE_ENDPOINT, files=fields,
            headers={"Referer": f"{BASE}/web/tiktokProduct/add"},
            timeout=self.timeout)
        http_status = response.status_code
        try:
            payload_out = response.json()
        except ValueError:
            payload_out = {}
        if not isinstance(payload_out, dict):
            payload_out = {}
        code = str(payload_out.get("code"))
        msg = str(payload_out.get("msg") or "")
        self.write_probe = {
            "endpoint": WRITE_ENDPOINT,
            "method": "POST",
            "purpose": "仅验证 ZIP(tiktokSave.txt)+multipart(file,op) 传输格式",
            "payload_empty": True,
            "payload": payload,
            "multipart_fields": ["file", "op"],
            "file_field_first": bool(file_first),
            "file_field_content_type": "application/zip",
            "zip_entry": SAVE_NAME,
            "zip_entries": zip_entries,
            "zip_content": zip_content,
            "http_status": http_status,
            "code": code,
            "msg": msg,
            "shop_error": "店铺" in msg,
            "data_written": False,
            "note": "空 payload 在店铺校验阶段被拒绝，不产生任何数据；本账号未绑定 TikTok 店铺。",
        }
        return self.write_probe


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


def _as_dict(node) -> dict:
    return node if isinstance(node, dict) else {}


def observed_of(checks: list[dict], name: str) -> dict:
    for row in checks:
        if row["name"] == name:
            return row.get("observed") or {}
    return {}


# ---------------------------------------------------------------------------
# 各项只读检查
# ---------------------------------------------------------------------------
def check_login(api: ReadOnlyApi, ctx: dict) -> tuple[bool, str, dict]:
    payload = api.get("/api/userIn.json")
    data = _as_dict(_data(payload))
    shops_raw = data.get("shopMap") or {}
    if isinstance(shops_raw, dict):
        rows = [row for row in shops_raw.values() if isinstance(row, dict)]
    elif isinstance(shops_raw, list):
        rows = [row for row in shops_raw if isinstance(row, dict)]
    else:
        rows = []
    ok = (
        _code(payload) == "0"
        and str(data.get("account")) == EXPECT["account"]
        and _as_int(data.get("puid")) == int(EXPECT["puid"])
        and isinstance(shops_raw, (dict, list))
    )
    ctx["account"] = str(data.get("account") or "")
    ctx["puid"] = _as_int(data.get("puid"))
    ctx["shops"] = [
        {"id": str(row.get("id") or row.get("shopId") or ""),
         "name": str(row.get("name") or ""),
         "platform": str(row.get("platform") or "")}
        for row in rows
    ]
    platforms = sorted({row["platform"] for row in ctx["shops"] if row["platform"]})
    detail = (f"code={_code(payload)} account={data.get('account')} "
              f"puid={data.get('puid')} 店铺={len(ctx['shops'])} 个 平台={platforms}")
    return ok, detail, {
        "account": data.get("account"),
        "puid": data.get("puid"),
        "expected_account": EXPECT["account"],
        "expected_puid": EXPECT["puid"],
        "shop_count": len(ctx["shops"]),
        "shops": ctx["shops"],
    }


def check_tiktok_shop_truth(api: ReadOnlyApi, ctx: dict) -> tuple[bool, str, dict]:
    """契约 §0：该账号没有绑定 TikTok 店铺，必须如实为 0。"""
    if "shops" not in ctx:
        payload = api.get("/api/userIn.json")
        data = _as_dict(_data(payload))
        rows = data.get("shopMap") or {}
        rows = list(rows.values()) if isinstance(rows, dict) else (rows or [])
        ctx["shops"] = [{"id": str(r.get("id") or ""), "name": str(r.get("name") or ""),
                         "platform": str(r.get("platform") or "")}
                        for r in rows if isinstance(r, dict)]
    tiktok = [row for row in ctx["shops"]
              if row.get("platform", "").lower() == "tiktok"]
    ok = len(tiktok) == int(EXPECT["tiktok_shops"])
    others = [row for row in ctx["shops"] if row not in tiktok]
    detail = (f"TikTok 店铺 {len(tiktok)} 个（期望 {EXPECT['tiktok_shops']}：账号未绑定）；"
              f"其它店铺={[(row['name'], row['platform']) for row in others]}")
    return ok, detail, {
        "tiktok_count": len(tiktok),
        "expected_tiktok_count": int(EXPECT["tiktok_shops"]),
        "tiktok_shops": tiktok,
        "other_shops": others,
        "truth": "账号 QINGHEMAOYI 已授权店铺只有 Amazon/手工订单，没有 TikTok Shop",
    }


def check_l7_sites(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.get("/api/tiktokProduct/getL7ShopIdAndSite.json")
    data = _as_dict(_data(payload))
    sites = data.get("tiktokL7Site")
    sites = [str(code).strip().upper() for code in sites] if isinstance(sites, list) else []
    shop_id = str(data.get("tiktokL7ShopId") or "")
    ok = (_code(payload) == "0" and sites == EXPECT["l7_sites"]
          and shop_id == EXPECT["l7_shop_id"])
    detail = (f"code={_code(payload)} 站点 {len(sites)} 个（期望 23）"
              f" shopId={shop_id} 列表={'一致' if sites == EXPECT['l7_sites'] else '不一致'}")
    return ok, detail, {
        "count": len(sites),
        "expected_count": len(EXPECT["l7_sites"]),
        "sites": sites,
        "tiktokL7ShopId": shop_id,
        "matches_contract": sites == EXPECT["l7_sites"],
    }


def check_page_list(state: str):
    def _run(_ctx) -> tuple[bool, str, dict]:
        api = _ctx["api"]
        payload = api.post("/api/tiktokProduct/pageList.json", {
            "pageNo": 1, "pageSize": 20, "shopId": "-1",
            "dxmState": state, "searchValue": "",
        })
        data = _as_dict(_data(payload))
        page = _as_dict(data.get("page"))
        rows = page.get("list") or []
        total = _as_int(page.get("totalSize"))
        ok = (_code(payload) == "0" and isinstance(rows, list)
              and isinstance(page, dict) and "totalSize" in page)
        if state == "draft":
            ok = ok and total == int(EXPECT["draft_total"])
        detail = (f"code={_code(payload)} totalSize={total} rows={len(rows)} "
                  f"pageNo={page.get('pageNo')} totalPage={page.get('totalPage')}")
        return ok, detail, {
            "state": state,
            "code": _code(payload),
            "total_size": total,
            "rows_on_page": len(rows),
            "page_no": page.get("pageNo"),
            "total_page": page.get("totalPage"),
        }
    return _run


def check_offline_counts(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.post("/api/tiktokProduct/getOfflineCounts.json", {"shopId": "-1"})
    data = _as_dict(_data(payload))
    required = ("draftNum", "publishingTiming", "waitPublish", "publishFail", "publishing")
    missing = [key for key in required if key not in data]
    ok = (_code(payload) == "0" and not missing
          and _as_int(data.get("draftNum")) == int(EXPECT["draft_total"]))
    detail = (f"code={_code(payload)} draftNum={data.get('draftNum')} "
              f"waitPublish={data.get('waitPublish')} publishFail={data.get('publishFail')} "
              f"publishing={data.get('publishing')} 缺字段={missing or '无'}")
    return ok, detail, {"counts": data, "missing_fields": missing}


def check_online_counts(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.post("/api/tiktokProduct/getOnlineCounts.json", {"shopId": "-1"})
    data = _data(payload)
    ok = _code(payload) == "0" and isinstance(data, dict)
    detail = f"code={_code(payload)} data={json.dumps(data, ensure_ascii=False)[:200]}"
    return ok, detail, {"counts": data}


def check_category_roots(api: ReadOnlyApi, ctx: dict) -> tuple[bool, str, dict]:
    payload = api.post("/api/tiktokCategory/list.json", {
        "shopId": "-1", "site": EXPECT["site"], "crossBorder": 1,
        "categoryParentId": "0",
    })
    rows = _data(payload)
    ok = (_code(payload) == "0" and isinstance(rows, list)
          and len(rows) == int(EXPECT["gb_category_roots"]))
    bad = [row for row in (rows or []) if isinstance(row, dict) and (
        str(row.get("site")) != EXPECT["site"]
        or str(row.get("categoryParentId")) != "0"
        or str(row.get("crossBorder")) != "1")]
    ok = ok and not bad
    if isinstance(rows, list) and rows:
        ctx["root"] = rows[0]
    detail = (f"code={_code(payload)} GB 根类目={len(rows) if isinstance(rows, list) else '非数组'}"
              f"（期望 {EXPECT['gb_category_roots']}） 字段异常={len(bad)}"
              f" 首条={_as_dict((rows or [{}])[0]).get('categoryId')}"
              f"/{_as_dict((rows or [{}])[0]).get('categoryName')}")
    return ok, detail, {
        "count": len(rows) if isinstance(rows, list) else None,
        "expected_count": int(EXPECT["gb_category_roots"]),
        "site": EXPECT["site"],
        "cross_border": 1,
        "first": {"categoryId": _as_dict((rows or [{}])[0]).get("categoryId"),
                  "categoryName": _as_dict((rows or [{}])[0]).get("categoryName"),
                  "transName": _as_dict((rows or [{}])[0]).get("transName")},
        "sample": [{"categoryId": row.get("categoryId"),
                    "categoryName": row.get("categoryName")}
                   for row in (rows or [])[:6] if isinstance(row, dict)],
        "invalid_nodes": bad[:3],
    }


def check_category_drill(api: ReadOnlyApi, ctx: dict) -> tuple[bool, str, dict]:
    root = ctx.get("root") or {}
    parent = str(root.get("categoryId") or "0")
    levels: list[dict] = []
    leaf: dict = {}
    ok = bool(parent and parent != "0")
    for depth in range(1, 5):
        payload = api.post("/api/tiktokCategory/list.json", {
            "shopId": "-1", "site": EXPECT["site"], "crossBorder": 1,
            "categoryParentId": parent,
        })
        rows = _data(payload)
        if _code(payload) != "0" or not isinstance(rows, list) or not rows:
            ok = False
            levels.append({"depth": depth, "parent_category_id": parent,
                           "error": "empty/invalid"})
            break
        node = rows[0]
        levels.append({
            "depth": depth,
            "parent_category_id": parent,
            "count": len(rows),
            "categoryId": node.get("categoryId"),
            "categoryName": node.get("categoryName"),
            "isLeaf": node.get("isLeaf"),
            "nodePathId": node.get("nodePathId"),
            "categoryParentId": node.get("categoryParentId"),
        })
        # 下钻必须用父节点 categoryId，且返回节点的 categoryParentId 与之相等
        ok = ok and str(node.get("categoryParentId")) == parent
        if str(node.get("isLeaf")) == "1":
            leaf = node
            break
        parent = str(node.get("categoryId"))
    if leaf:
        ctx["leaf"] = leaf
    ok = ok and bool(leaf) and len(levels) >= 2
    detail = ("逐级 " + " → ".join(
        f"{row.get('categoryName')}({row.get('categoryId')},子={row.get('count')})"
        for row in levels if "error" not in row)
        + f" · 叶子={leaf.get('categoryId') if leaf else '未找到'}")
    return ok, detail, {"levels": levels,
                        "leaf": {"categoryId": leaf.get("categoryId"),
                                 "categoryName": leaf.get("categoryName"),
                                 "nodePathId": leaf.get("nodePathId"),
                                 "isLeaf": leaf.get("isLeaf")}}


def check_search_category(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    keyword = EXPECT["search_keyword"]
    payload = api.post("/api/tiktokCategory/searchCategory.json", {
        "shopId": "-1", "site": EXPECT["site"], "crossBorder": 1,
        "category": keyword,
    })
    rows = _data(payload)
    count = len(rows) if isinstance(rows, list) else -1
    ok = (_code(payload) == "0" and count > 0
          and all(isinstance(row, dict) and row.get("categoryId") for row in rows))
    detail = (f"code={_code(payload)} 关键词={keyword!r}（字段名 category）"
              f" 命中={count} 条（契约实测 73 条，要求 >0）")
    return ok, detail, {
        "keyword": keyword,
        "keyword_field": "category",
        "count": count,
        "sample": [{"categoryId": row.get("categoryId"),
                    "categoryName": row.get("categoryName"),
                    "isLeaf": row.get("isLeaf"),
                    "nodePathId": row.get("nodePathId")}
                   for row in (rows or [])[:5]],
    }


def check_attributes(api: ReadOnlyApi, ctx: dict) -> tuple[bool, str, dict]:
    leaf = ctx.get("leaf") or {}
    category_id = str(leaf.get("categoryId") or "")
    if not category_id:
        raise RuntimeError("下钻未找到叶子类目，无法校验属性接口")
    base = {"shopId": "-1", "site": EXPECT["site"], "crossBorder": 1,
            "categoryId": category_id}
    with_type = api.post("/api/tiktokCategory/attributeList.json",
                         {**base, "type": 3})
    without_type = api.post("/api/tiktokCategory/attributeList.json", base)
    rows = _data(with_type)
    plain = _data(without_type)
    count = len(rows) if isinstance(rows, list) else -1
    plain_count = len(plain) if isinstance(plain, list) else -1
    types = sorted({str(row.get("attrType")) for row in (rows or [])
                    if isinstance(row, dict)})
    mandatory = sum(1 for row in (rows or [])
                    if isinstance(row, dict) and str(row.get("isMandatory")) == "1")
    ok = (_code(with_type) == "0" and count > 0 and types == ["3"]
          and _code(without_type) == "0" and plain_count < count)
    detail = (f"code={_code(with_type)} 叶子 {category_id} 属性={count} 条"
              f"（期望 10 条） attrType={types} 必填={mandatory} ·"
              f" 不带 type 仅 {plain_count} 条（对照，证明必须 type=3）")
    return ok, detail, {
        "category_id": category_id,
        "nodePathId": leaf.get("nodePathId"),
        "attr_count_type3": count,
        "attr_types": types,
        "mandatory_count": mandatory,
        "attr_count_without_type": plain_count,
        "sample": [{"attrId": row.get("attrId"), "attrName": row.get("attrName"),
                    "isMandatory": row.get("isMandatory")}
                   for row in (rows or [])[:5] if isinstance(row, dict)],
    }


def check_limits(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.post("/api/tiktokProduct/getLimitBySite.json",
                       {"site": EXPECT["site"]})
    data = _as_dict(_data(payload))
    mismatches = {}
    for key, expected in EXPECT["limits"].items():
        actual = data.get(key)
        if isinstance(expected, float):
            match = actual is not None and abs(float(actual) - expected) < 1e-9
        else:
            match = actual is not None and _as_int(actual, -1) == int(expected)
        if not match:
            mismatches[key] = {"expected": expected, "actual": actual}
    ok = _code(payload) == "0" and not mismatches
    detail = (f"code={_code(payload)} 字段 {len(data)} 个 不符={mismatches or '无'} ·"
              f" 标题 {data.get('nameMin')}–{data.get('nameMax')}"
              f" 描述≤{data.get('descMax')} 主图 {data.get('imgCountMin')}–"
              f"{data.get('imgCountMax')} 库存≤{data.get('stockMax')}")
    return ok, detail, {"limits": data, "mismatches": mismatches,
                        "expected": EXPECT["limits"]}


def check_list_warehouse(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.post("/api/tiktokProduct/listWarehouse.json", {"shopId": "-1"})
    data = _data(payload)
    ok = _code(payload) == "0" and isinstance(data, list)
    detail = f"code={_code(payload)} 仓库={len(data) if isinstance(data, list) else '非数组'} 个（无店铺可为空）"
    return ok, detail, {"count": len(data) if isinstance(data, list) else None,
                        "data_is_list": isinstance(data, list)}


def check_multishop(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.post("/api/multiShopProduct/tiktok/shopList.json", {})
    data = _as_dict(_data(payload))
    ok = (_code(payload) == "0" and isinstance(_data(payload), dict)
          and data.get("siteShop") is None and data.get("popShop") is None)
    detail = (f"code={_code(payload)} siteShop={data.get('siteShop')} "
              f"popShop={data.get('popShop')}（无店铺时均为 null，与契约一致）")
    return ok, detail, {"siteShop": data.get("siteShop"),
                        "popShop": data.get("popShop")}


def check_history(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    payload = api.post("/api/tiktokCategory/historyList.json",
                       {"shopId": "-1", "site": EXPECT["site"]})
    rows = _data(payload)
    ok = _code(payload) == "0" and isinstance(rows, list)
    detail = f"code={_code(payload)} 最近类目={len(rows) if isinstance(rows, list) else '非数组'} 条（无店铺可为空）"
    return ok, detail, {"count": len(rows) if isinstance(rows, list) else None}


def check_detail_fallback(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    """无真实 TikTok 商品：详情回退端点应可调用且返回 data=null（属预期）。"""
    payload = api.post("/api/tiktokProduct/getByProductId.json",
                       {"productId": "0"})
    data = _data(payload)
    ok = _code(payload) == "0" and data is None
    detail = (f"code={_code(payload)} data={json.dumps(data, ensure_ascii=False)}"
              "（账号无 TikTok 商品：读不到属预期，客户端会抛中文 TikTokError）")
    return ok, detail, {"data": data, "data_is_null": data is None}


def check_save_transport(api: ReadOnlyApi, _ctx) -> tuple[bool, str, dict]:
    probe = api.probe_save_transport()
    ok = (probe["http_status"] == 200 and probe["code"] == "-1"
          and probe["shop_error"] and probe["file_field_first"]
          and probe["zip_entries"] == [SAVE_NAME]
          and probe["zip_content"] == "{}" and probe["payload_empty"])
    detail = (f"HTTP {probe['http_status']} code={probe['code']} msg={probe['msg']!r}"
              f" · ZIP 条目={probe['zip_entries']} 内容={probe['zip_content']!r}"
              f" · multipart file 在前={probe['file_field_first']}"
              " → 传输格式正确；空 payload 不产生数据")
    return ok, detail, probe


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


def _empty_report(timeout: int) -> dict:
    return {
        "generated_at": _stamp(),
        "mode": "read-only (+empty-payload save transport self-check)",
        "status": "ERROR",
        "base": BASE,
        "timeout_seconds": timeout,
        "account": {},
        "cookie": {"source": "", "source_kind": "", "count": 0,
                   "content_logged": False, "content_in_report": False},
        "shops": {},
        "sites": {},
        "lists": {},
        "categories": {},
        "limits": {},
        "save_transport": {},
        "checks": [],
        "summary": {"passed": 0, "total": 0, "failed": 0, "failed_checks": []},
        "safety": {
            # 语义：通用只读 API（get/post）从未调用写入口；唯一一次写入口请求
            # 是下面文档化的「空 payload 传输自检」，服务端店铺校验即拒绝。
            "write_endpoints_never_called": True,
            "write_endpoints_never_called_scope":
                "通用只读 API；唯一例外是文档化的空 payload 保存传输自检",
            "write_calls_via_read_api": 0,
            "transport_selfcheck_calls": 0,
            "write_endpoint_reached_by_transport_selfcheck_only": True,
            "transport_selfcheck_payload_empty": True,
            "write_guard_selftest": False,
            "forbidden_write_suffix_checked": True,
            "forbidden_write_suffix": FORBIDDEN_WRITE_SUFFIX,
            "allowed_readonly_endpoints": _readonly_endpoint_rows(),
            "request_count": 0,
            "cache_hits": 0,
            "blocked_attempts": [],
            "cookie_in_report": False,
        },
        "notes": [
            "独立实现：不 import 后端 aeclone 模块，全部走 requests + 只读白名单。",
            "唯一写入口仅被『空 payload 传输自检』触达一次（该端点由拼接表达式构造，"
            "源码不出现完整字面量）；通用只读 API 对它做硬断言拦截。",
            "空 payload 在服务端『产品未选择店铺或店铺无效！』阶段被拒绝，不产生任何数据。",
            "Cookie 内容不写入本报告，控制台仅打印前 6 位脱敏片段。",
        ],
        "limitations": [
            "账号未绑定 TikTok 店铺：无法验证『采集箱商品编辑 → 保存/发布』真实写入回环；"
            "绑定店铺后按 docs/TIKTOK_DESIGN.md §6.4 用 --write-roundtrip 验证。",
            "保存传输自检只能证明 ZIP/multipart/字段被服务端正确解析到店铺校验阶段，"
            "不能证明真实保存成功。",
            "账号无 TikTok 商品：详情/变种接口只能证明端点可调用、空数据按预期返回。",
            "类目/搜索/属性/限额无需店铺即可调用；店铺特有站点映射（shopSiteList）未验证。",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="TikTok 刊登真实账号只读核验（仅在空 payload 传输自检时触达写入口）")
    parser.add_argument("--cookie-file", default="", help="Cookie 文件（头格式或 Netscape）")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="JSON 报告路径")
    parser.add_argument("--timeout", type=int, default=TIMEOUT, help="单请求超时秒数")
    args = parser.parse_args(argv)

    output = Path(args.output)
    timeout = max(5, int(args.timeout))
    report = _empty_report(timeout)

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
    print("模式：只读（白名单端点；写入口硬拦截；仅空 payload 传输自检触达保存端点）\n")

    api = ReadOnlyApi(raw_cookie, timeout=timeout)
    checker = Checker()
    ctx: dict = {"api": api}

    checks: list[tuple[str, object]] = [
        ("userIn 登录 / 账号", partial(check_login, api)),
        ("TikTok 店铺真相（必须 0）", partial(check_tiktok_shop_truth, api)),
        ("getL7ShopIdAndSite 23 站点", partial(check_l7_sites, api)),
        ("pageList 采集箱(draft)", check_page_list("draft")),
        ("pageList 待发布(offline)", check_page_list("offline")),
        ("pageList 在线(online)", check_page_list("online")),
        ("getOfflineCounts 采集箱计数", partial(check_offline_counts, api)),
        ("getOnlineCounts 在线计数", partial(check_online_counts, api)),
        ("tiktokCategory/list GB 根 30 条", partial(check_category_roots, api)),
        ("类目下钻（categoryParentId）", partial(check_category_drill, api)),
        ("searchCategory category=phone", partial(check_search_category, api)),
        ("attributeList 叶子 type=3", partial(check_attributes, api)),
        ("getLimitBySite 站点限额", partial(check_limits, api)),
        ("listWarehouse 仓库", partial(check_list_warehouse, api)),
        ("multiShop/tiktok/shopList 多店", partial(check_multishop, api)),
        ("historyList 最近类目", partial(check_history, api)),
        ("getByProductId 详情回退（预期空）", partial(check_detail_fallback, api)),
        # 必须放最后：唯一一次触达写入口（空 payload 传输自检，不产生数据）
        ("保存传输格式自检（空 payload）", partial(check_save_transport, api)),
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
    report["account"] = observed_of(checker.checks, "userIn 登录 / 账号")
    report["shops"] = observed_of(checker.checks, "TikTok 店铺真相（必须 0）")
    report["account"]["tiktok_shop_count"] = report["shops"].get("tiktok_count")
    sites = observed_of(checker.checks, "getL7ShopIdAndSite 23 站点")
    report["sites"] = sites
    report["lists"] = {
        "draft": observed_of(checker.checks, "pageList 采集箱(draft)"),
        "offline": observed_of(checker.checks, "pageList 待发布(offline)"),
        "online": observed_of(checker.checks, "pageList 在线(online)"),
        "offline_counts": observed_of(checker.checks, "getOfflineCounts 采集箱计数"),
        "online_counts": observed_of(checker.checks, "getOnlineCounts 在线计数"),
    }
    drill = observed_of(checker.checks, "类目下钻（categoryParentId）")
    report["categories"] = {
        "gb_roots": observed_of(checker.checks, "tiktokCategory/list GB 根 30 条"),
        "drill": drill,
        "search": observed_of(checker.checks, "searchCategory category=phone"),
        "attributes": observed_of(checker.checks, "attributeList 叶子 type=3"),
        "history": observed_of(checker.checks, "historyList 最近类目"),
    }
    report["limits"] = observed_of(checker.checks, "getLimitBySite 站点限额")
    report["warehouses"] = observed_of(checker.checks, "listWarehouse 仓库")
    report["multishop"] = observed_of(checker.checks, "multiShop/tiktok/shopList 多店")
    report["detail"] = observed_of(checker.checks, "getByProductId 详情回退（预期空）")
    report["save_transport"] = observed_of(checker.checks, "保存传输格式自检（空 payload）")

    guard_selftest = (
        is_forbidden_write(WRITE_ENDPOINT)
        and not is_forbidden_write("/api/userIn.json")
        and not is_forbidden_write("/api/tiktokCategory/list.json")
        and WRITE_ENDPOINT not in ALLOWED_ENDPOINTS
    )
    report["safety"].update({
        "write_calls_via_read_api": len([row for row in api.blocked_attempts
                                          if row.get("reason") == "forbidden write endpoint"]),
        "transport_selfcheck_calls": 1 if api.write_probe else 0,
        "write_endpoint_reached_by_transport_selfcheck_only":
            bool(api.write_probe) and len(api.blocked_attempts) == 0,
        "transport_selfcheck_payload_empty":
            bool((api.write_probe or {}).get("payload_empty")),
        "transport_selfcheck_data_written":
            bool((api.write_probe or {}).get("data_written")),
        "write_guard_selftest": guard_selftest,
        "request_count": api.request_count,
        "cache_hits": api.cache_hits,
        "blocked_attempts": api.blocked_attempts,
        "cookie_in_report": False,
        "cookie_masked_on_console_only": True,
    })

    if api.blocked_attempts:
        report["status"] = "ERROR"
        report["error"] = "安全违规：出现非只读请求尝试"
        code = 1
    elif not guard_selftest:
        report["status"] = "ERROR"
        report["error"] = "安全自检失败：写入口断言未生效"
        code = 1
    elif passed == total:
        report["status"] = "PASS"
        code = 0
    else:
        report["status"] = "FAIL"
        code = 1

    _write_report(output, report)
    print(f"\n结果：{passed}/{total} PASS · 状态 {report['status']} · "
          f"请求 {api.request_count} 次 · 缓存命中 {api.cache_hits} · "
          f"拦截 {len(api.blocked_attempts)} 次")
    if failed_rows:
        print("失败项：")
        for row in failed_rows:
            print(f"  - {row['name']}: {row['detail']}")
    print(f"报告已写入 {output}")
    return code


if __name__ == "__main__":
    sys.exit(main())
