"""Amazon 发布状态 · 独立只读核验与失败原因分类（task-10）。

做什么
----
用 requests 自行实现（**不 import 后端实现**、**严禁写操作**）读取真实账号：

1. ``pageList`` 采集箱/在线计数（只读）；
2. ``POST /api/amazonProduct/offline.json``，``dxmState=offline`` +
   ``dxmOfflineState=publishFail|publishing|waitPublish``，``pageSize=200`` 一次取全；
3. 解析每行 ``errMsg``：错误码（100521 / 90220 / 8560…）、``属性:xxx`` 必填属性列表，
   归类 A（平台审核中）/ B（类目必填属性缺失）/ C（软件校验拦截）/ D（其它）；
4. 输出各状态数量、商品摘要、必填属性聚合统计、分类占比；
5. 对 ``--product-id`` 指定商品（默认两个发布失败样例）生成「发布结果核验清单」：
   发布中/发布失败/在线/待发布/采集箱 各对应什么结论、要看 errMsg 哪些字段。

安全
----
- 只读白名单：2 个只读查询端点 + userIn；唯一写入口以拼接常量 + 请求守卫显式拦截；
- 不打印/不写 Cookie 内容（控制台仅前 6 位脱敏，报告 ``content_logged=false``）；
- 输出 ``docs/amazon-publish-state.json``；代码内无任何写开关。

跑法
----
    .venv/bin/python tools/verify_amazon_publish_state.py
    .venv/bin/python tools/verify_amazon_publish_state.py --product-id 220114595363182310,220114595363182214
    DXM_COOKIE_PATH=/path/to/cookies.txt .venv/bin/python tools/verify_amazon_publish_state.py --json-out /tmp/x.json

退出码：成功读取并完成分类 → 0；Cookie 缺失 → SKIP 且 0；安全违规/接口失败 → 1。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COOKIE_FILE = ROOT / ".tmp-probe" / "cookies.txt"
DEFAULT_OUTPUT = ROOT / "docs" / "amazon-publish-state.json"

BASE = "https://www.dianxiaomi.com"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TIMEOUT = 40
PAGE_SIZE = 200
SAMPLE_PRODUCT_IDS = ("220114595363182310", "220114595363182214")

# 只读端点白名单：路径 -> 允许方法
ALLOWED_ENDPOINTS: dict[str, frozenset[str]] = {
    "/api/userIn.json": frozenset({"GET"}),
    "/api/amazonProduct/pageList.json": frozenset({"POST"}),
    "/api/amazonProduct/offline.json": frozenset({"POST"}),
}
# 唯一写入口用拼接表达：源码不出现完整写路径字面量（契约测试可反向断言）。
FORBIDDEN_WRITE_SUFFIX = "add" + ".json"

STATES = ("publishFail", "publishing", "waitPublish")
STATE_LABELS = {
    "publishFail": "发布失败",
    "publishing": "发布中",
    "waitPublish": "待发布",
    "draft": "采集箱",
    "online": "在线",
}

# 失败原因分类（A/B/C/D）
CLASS_LABELS = {
    "A": "A 类 · 平台审核中（100521，非软件失败，48h 内可能转在线）",
    "B": "B 类 · 类目必填属性缺失（90220，需补属性）",
    "C": "C 类 · 软件校验拦截（促销词/联系方式等，不属于平台失败）",
    "D": "D 类 · 其它（HTTP/权限/数据/未知）",
}
CLASS_ORDER = ("A", "B", "C", "D")

# C 类关键词（软件保存前本地校验会命中的文案特征；平台失败列表一般不会出现）
PROMO_MARKERS = (
    "促销", "广告", "绝对化", "100%", "guaranteed", "guarantee", "free shipping",
    "best seller", "sale", "促销词", "联系方式", "电话", "邮箱", "email",
)


# ---------------------------------------------------------------------------
# Cookie / 只读请求守卫
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


class ReadOnlyApi:
    """所有真实请求的唯一出口；非白名单/写入口一律拒绝并记录。"""

    def __init__(self, cookie_header: str, timeout: int = TIMEOUT):
        self.timeout = timeout
        self.request_count = 0
        self.cache_hits = 0
        self.blocked_attempts: list[dict] = []
        self._cache: dict[str, dict] = {}
        self.session = requests.Session()
        for name, value in parse_cookie_pairs(cookie_header):
            self.session.cookies.set(name, value, domain=".dianxiaomi.com")
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{BASE}/web/amazon/pageList/offline",
        })

    def _guard(self, method: str, path: str) -> None:
        method = str(method).upper()
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
        cache_key = json.dumps({"method": method, "path": path,
                                "params": kwargs.get("params"),
                                "data": kwargs.get("data")},
                               sort_keys=True, ensure_ascii=False, default=str)
        if cache_key in self._cache:
            self.cache_hits += 1
            return self._cache[cache_key]
        payload: dict = {}
        for attempt in range(2):  # 频控（code=2005）时最多重试一次
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
# errMsg 解析 / 分类
# ---------------------------------------------------------------------------
def parse_error_codes(err_msg: str) -> list[str]:
    """按出现顺序去重提取 code:xxx（兼容 'code:90220' 与 'code:发布失败code:8560'）。"""
    codes = re.findall(r"code\s*:\s*(\d+)", str(err_msg or ""))
    seen: list[str] = []
    for code in codes:
        if code not in seen:
            seen.append(code)
    return seen


def parse_required_attrs(err_msg: str) -> list[str]:
    """提取 errMsg 里的 ``属性:xxx``；保序去重。"""
    attrs = re.findall(r"属性\s*:\s*([A-Za-z0-9_]+)", str(err_msg or ""))
    seen: list[str] = []
    for attr in attrs:
        if attr not in seen:
            seen.append(attr)
    return seen


def extract_missing_attr_labels(err_msg: str) -> list[str]:
    """提取 ``“型号” 为必填项，但缺失`` 里的中文属性名（便于给运营看）。"""
    return re.findall(r"[“\"]([^”\"]{1,40})[”\"]\s*为必填项", str(err_msg or ""))


def classify_failure(err_msg: str) -> tuple[str, dict]:
    """返回 (A/B/C/D, 明细)。A 优先（含 100521/审查），其次 B（90220/属性），C 促销词，其余 D。"""
    msg = str(err_msg or "")
    codes = parse_error_codes(msg)
    attrs = parse_required_attrs(msg)
    lower = msg.lower()
    detail: dict = {"codes": codes, "required_attrs": attrs}
    detail["secondary_codes"] = codes[1:] if len(codes) > 1 else []
    if "100521" in codes or "审查" in msg or "审核" in msg or "48 小时" in msg:
        detail["reason"] = "平台审核中（等待最多 48 小时，可能自动转在线）"
        return "A", detail
    if "90220" in codes or attrs or "为必填项" in msg:
        detail["reason"] = "类目必填属性缺失（需补属性后重新保存/发布）"
        return "B", detail
    if any(marker.lower() in lower for marker in PROMO_MARKERS):
        detail["reason"] = "软件本地校验拦截（促销词/联系方式等），不属于平台失败"
        return "C", detail
    detail["reason"] = "其它失败（需人工看完整 errMsg / Trace_id）"
    return "D", detail


CLASSIFIER_SAMPLES = (
    ("A", "发布失败:code:100521,我们正在审查这个商品信息，以确定是否需要任何其他信息。"
          "请等待最多 48 小时才能完成此流程。"),
    ("B", "发布失败:code:90220,“型号” 为必填项，但缺失。,属性:model_number;"
          "code:90220,“零件编号” 为必填项，但缺失。,属性:part_number;"),
    ("C", "保存校验未通过：描述含促销词 sale / guaranteed / 100% / 电话"),
    ("D", "HTTP 502 Bad Gateway（网关错误）"),
    ("A", "code:100521,我们正在审查这个商品信息。;code:发布失败code:8560,您的商品详情不够完整"),
)


def classifier_selftest() -> dict:
    """分类器自检：用 A/B/C/D 代表样本验证规则（保证 C 类规则可验证，即使当前为 0）。"""
    rows = []
    for expected, sample in CLASSIFIER_SAMPLES:
        actual, detail = classify_failure(sample)
        rows.append({"expected": expected, "actual": actual,
                     "ok": actual == expected, "sample": _short(sample, 90),
                     "codes": detail.get("codes", []),
                     "required_attrs": detail.get("required_attrs", [])})
    return {"ok": all(row["ok"] for row in rows), "rows": rows}


def _short(text, limit: int = 200) -> str:
    text = str(text or "").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _attr_summary(err_msg: str) -> dict:
    attrs = parse_required_attrs(err_msg)
    labels = extract_missing_attr_labels(err_msg)
    return {
        "required_attrs": attrs,
        "required_attr_count": len(attrs),
        "required_attr_labels": labels,
        "errMsg_chars": len(str(err_msg or "")),
    }


def _row_summary(row: dict, include_msg: bool = True, classify: bool = True) -> dict:
    err_msg = str(row.get("errMsg") or "")
    if not classify:
        detail = {"codes": [], "required_attrs": [], "reason": "非发布失败状态，不分类"}
        cls = ""
    elif err_msg.strip():
        cls, detail = classify_failure(err_msg)
    else:
        cls, detail = ("D", {"codes": [], "required_attrs": [],
                             "reason": "errMsg 为空（需复查/可能刚失败）"})
    summary = {
        "id": str(row.get("id") or ""),
        "title": _short(row.get("title"), 120),
        "sku": str(row.get("sku") or ""),
        "marketPlaceId": str(row.get("marketPlaceId") or ""),
        "categoryId": str(row.get("categoryId") or ""),
        "dxmState": str(row.get("dxmState") or ""),
        "dxmOfflineState": str(row.get("dxmOfflineState") or ""),
        "errMsg_present": bool(err_msg.strip()),
        "errMsg_len": len(err_msg),
        "codes": detail.get("codes", []),
        "required_attrs": detail.get("required_attrs", []),
        "class": cls,
        "class_label": CLASS_LABELS.get(cls, "—") if cls else "—",
        "reason": detail.get("reason", ""),
    }
    if include_msg:
        summary["errMsg"] = _short(err_msg, 200)
    return summary


# ---------------------------------------------------------------------------
# 只读查询
# ---------------------------------------------------------------------------
def fetch_account(api: ReadOnlyApi) -> dict:
    payload = api.get("/api/userIn.json")
    data = payload.get("data") or {}
    shops = data.get("shopMap") or {}
    shop = shops.get("9408005") or {}
    return {
        "code": str(payload.get("code")),
        "account": str(data.get("account") or ""),
        "puid": data.get("puid"),
        "shop_id": str(shop.get("id") or "9408005"),
        "shop_name": str(shop.get("name") or ""),
        "merchant_id": str(shop.get("merchantId") or ""),
        "default_marketplace": str(shop.get("marketPlaceId") or ""),
    }


def fetch_page_list(api: ReadOnlyApi, state: str, page_size: int = PAGE_SIZE) -> dict:
    payload = api.post("/api/amazonProduct/pageList.json", {
        "pageNo": 1, "pageSize": page_size, "shopId": "-1",
        "dxmState": state, "searchValue": "",
    })
    data = payload.get("data") or {}
    page = data.get("page") or {}
    rows = page.get("list") or []
    return {
        "code": str(payload.get("code")),
        "rows": rows,
        "total": page.get("totalSize"),
        "returned": len(rows),
        "draftNum": data.get("draftNum"),
        "onlineNum": data.get("onlineNum"),
        "leftStat": data.get("leftStat"),
        "markets": data.get("marketPids") or {},
    }


def fetch_offline_state(api: ReadOnlyApi, state: str, page_size: int = PAGE_SIZE) -> dict:
    payload = api.post("/api/amazonProduct/offline.json", {
        "pageNo": 1, "pageSize": page_size, "shopId": "-1",
        "dxmState": "offline", "dxmOfflineState": state,
    })
    data = payload.get("data") or {}
    page = data.get("page") or {}
    rows = page.get("list") or []
    return {
        "code": str(payload.get("code")),
        "msg": str(payload.get("msg") or ""),
        "rows": rows,
        "total": page.get("totalSize"),
        "returned": len(rows),
    }


def locate_product(product_id: str, page_draft: dict, page_online: dict,
                   offline_rows: dict[str, list[dict]]) -> dict:
    """按 id 在 draft/online/offline 三态中定位；返回状态与原始行。"""
    for row in page_draft.get("rows") or []:
        if str(row.get("id")) == product_id:
            return {"state": "draft", "row": row, "source": "pageList/draft"}
    for row in page_online.get("rows") or []:
        if str(row.get("id")) == product_id:
            return {"state": "online", "row": row, "source": "pageList/online"}
    for state in STATES:
        for row in offline_rows.get(state) or []:
            if str(row.get("id")) == product_id:
                return {"state": state, "row": row, "source": f"offline.json/{state}"}
    return {"state": "not_found", "row": {}, "source": ""}


def conclusion_for(found: dict) -> dict:
    """发布结果核验清单：状态 → 结论 / 下一步 / 需要检查字段。"""
    state = found.get("state")
    row = found.get("row") or {}
    err_msg = str(row.get("errMsg") or "")
    base = {
        "product_id": str(row.get("id") or found.get("product_id") or ""),
        "state": state,
        "state_label": STATE_LABELS.get(state, "未找到"),
        "source": found.get("source", ""),
        "title": _short(row.get("title"), 100),
        "sku": str(row.get("sku") or ""),
        "errMsg": _short(err_msg, 200),
    }
    if state == "online":
        base.update({"conclusion": "发布成功（在线）",
                     "action": "无需处理；可核对 ASIN/listingId 与价格库存",
                     "fields_to_check": ["asin", "listingId", "onlineTime", "standardPrice", "quantity"]})
    elif state == "offline" or state == "publishing":
        base.update({"conclusion": "发布中（op=2 已受理，平台处理中，尚无结论）",
                     "action": "约 40s 后重新运行本工具复查；若变为 publishFail 按 errMsg 分类处理",
                     "fields_to_check": ["dxmOfflineState", "errMsg（暂空正常）", "updateTime"]})
    elif state == "publishFail":
        cls, detail = classify_failure(err_msg)
        action = {
            "A": "平台审核中：无需改数据，等待最多 48h 后复查（可能自动转在线）",
            "B": "补类目必填属性后重新保存/发布：" + ", ".join(detail.get("required_attrs") or []),
            "C": "软件本地校验拦截：修正促销词/联系方式后重新预演（非平台失败）",
            "D": "人工核对完整 errMsg/Trace_id，必要时重试或联系平台",
        }[cls]
        base.update({"conclusion": f"发布失败 · {CLASS_LABELS[cls]}",
                     "failure_class": cls,
                     "required_attrs": detail.get("required_attrs") or [],
                     "codes": detail.get("codes") or [],
                     "action": action,
                     "fields_to_check": ["errMsg 中的 code（100521/90220/8560…）",
                                         "errMsg 中的 属性:xxx（缺失属性名）",
                                         "errMsg 末尾 Trace_id"]})
    elif state == "waitPublish":
        base.update({"conclusion": "待发布（已保存但未提交平台）",
                     "action": "若期望发布：用 op=2 发布；op=1/3 不触发平台发布",
                     "fields_to_check": ["dxmOfflineState=waitPublish", "op", "errMsg（通常为空）"]})
    elif state == "draft":
        base.update({"conclusion": "仍在采集箱（op=2 未生效/被拦截/尚未保存成功）",
                     "action": "检查保存结果 code/msg 与校验 issues；确认 op=2 是否执行",
                     "fields_to_check": ["save_result.code/msg", "issues/warnings", "op"]})
    else:
        base.update({"conclusion": "未在当前账号 draft/online/offline 三态中找到该商品",
                     "action": "确认 product_id 是否正确、是否属于当前店铺/站点",
                     "fields_to_check": ["product_id", "shopId", "marketPlaceId"]})
    return base


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def _stamp() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _write(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def build_report(args) -> tuple[dict, int]:
    report: dict = {
        "generated_at": _stamp(),
        "mode": "read-only",
        "status": "ERROR",
        "base": BASE,
        "read_only": True,
        "write_endpoints_never_called": True,
        "account": {},
        "cookie": {"source": "", "source_kind": "", "count": 0, "content_logged": False},
        "counts": {},
        "classification": {},
        "required_attrs": {},
        "items": {},
        "checklist": [],
        "safety": {
            "allowed_readonly_endpoints": [f"{'/'.join(sorted(m))} {p}"
                                           for p, m in ALLOWED_ENDPOINTS.items()],
            "request_count": 0, "cache_hits": 0,
            "blocked_attempts": [], "write_endpoints_never_called": True,
        },
        "notes": [
            "独立实现：不 import 后端 aeclone 模块，全走 requests + 只读白名单。",
            "唯一写入口以拼接常量 + 请求守卫断言拦截；脚本无任何写开关。",
            "Cookie 内容不写入报告，控制台仅打印前 6 位脱敏片段。",
            "publishFail/publishing 数量与 errMsg 以运行时实时数据为准（Lead 测试期间会变化）。",
        ],
        "limitations": [
            "publishing 是瞬态（约 40s），抓取时刻可能为 0；需按清单复查。",
            "C 类（软件校验拦截）不会出现在平台 publishFail 列表，通常停在草稿/待发布；"
            "本报告对 publishFail 的 C 计数可能为 0，不代表软件无该校验。",
            "A 类 100521 需等待平台审核，只读核验无法提前判定最终在线/失败。",
        ],
    }

    cookie_path, cookie_kind = resolve_cookie_file(args.cookie_file)
    if cookie_path is None or not cookie_path.exists():
        report["status"] = "SKIP"
        report["skip_reason"] = ("没有 Cookie 文件：请用 --cookie-file 或 DXM_COOKIE_PATH 指定，"
                                 "或放到 .tmp-probe/cookies.txt（.gitignore 内，禁止提交）")
        return report, 0
    raw_cookie = cookie_path.read_text(encoding="utf-8", errors="replace").strip()
    pairs = parse_cookie_pairs(raw_cookie)
    report["cookie"].update({"source": cookie_path.name, "source_kind": cookie_kind,
                             "count": len(pairs)})
    if not pairs:
        report["status"] = "SKIP"
        report["skip_reason"] = f"Cookie 文件为空或无法解析：{cookie_path.name}"
        return report, 0

    print(f"Cookie：{cookie_kind} · {cookie_path.name} · {len(pairs)} 个字段 · "
          f"脱敏前 6 位 {mask_cookie(raw_cookie)}")
    print("模式：只读（白名单端点；写入口硬拦截）\n")
    api = ReadOnlyApi(raw_cookie, timeout=max(5, int(args.timeout)))

    account = fetch_account(api)
    draft = fetch_page_list(api, "draft")
    online = fetch_page_list(api, "online")
    offline = {state: fetch_offline_state(api, state) for state in STATES}

    errors: list[str] = []
    for name, data in [("userIn", account), ("pageList/draft", draft),
                       ("pageList/online", online)]:
        if data.get("code") != "0":
            errors.append(f"{name} code={data.get('code')}")
    for state, data in offline.items():
        if data.get("code") != "0":
            errors.append(f"offline.json/{state} code={data.get('code')}")

    publish_rows = offline["publishFail"]["rows"]
    publishing_rows = offline["publishing"]["rows"]
    wait_rows = offline["waitPublish"]["rows"]
    left_stat = draft.get("leftStat") or {}

    # 分类统计
    class_rows: dict[str, list[dict]] = {key: [] for key in CLASS_ORDER}
    fail_items = []
    for row in publish_rows:
        item = _row_summary(row)
        fail_items.append(item)
        class_rows[item["class"]].append(item)
    total_fail = len(publish_rows)
    classification = {}
    for cls in CLASS_ORDER:
        rows = class_rows[cls]
        classification[cls] = {
            "label": CLASS_LABELS[cls],
            "count": len(rows),
            "percent": round(len(rows) * 100.0 / total_fail, 1) if total_fail else 0.0,
            "ids": [row["id"] for row in rows],
            "sample_errMsg": rows[0]["errMsg"] if rows else "",
        }
    # 属性聚合统计（B 类 + 其它带属性的失败都统计）
    attr_counter: dict[str, int] = {}
    product_attrs: dict[str, list[str]] = {}
    for item in fail_items:
        for attr in item.get("required_attrs") or []:
            attr_counter[attr] = attr_counter.get(attr, 0) + 1
            product_attrs.setdefault(item["id"], [])
            if attr not in product_attrs[item["id"]]:
                product_attrs[item["id"]].append(attr)
    required_attrs = {
        "by_attr": dict(sorted(attr_counter.items(), key=lambda kv: (-kv[1], kv[0]))),
        "by_product": product_attrs,
        "products_with_missing_attrs": len(product_attrs),
        "total_attr_mentions": sum(attr_counter.values()),
    }

    report["account"] = account
    report["counts"] = {
        "draft": draft.get("total"),
        "online": online.get("total"),
        "waitPublish": offline["waitPublish"].get("total"),
        "publishing": offline["publishing"].get("total"),
        "publishFail": offline["publishFail"].get("total"),
        "publishFail_rows_returned": len(publish_rows),
        "publishing_rows_returned": len(publishing_rows),
        "leftStat": left_stat,
        "leftStat_consistency": {
            "waitPublish": str(left_stat.get("waitPublish")) == str(offline["waitPublish"].get("total")),
            "publishing": str(left_stat.get("publishing")) == str(offline["publishing"].get("total")),
            "publishFail": str(left_stat.get("publishFail")) == str(offline["publishFail"].get("total")),
        },
    }
    report["classification"] = {
        "total_publish_fail": total_fail,
        "classes": classification,
        "a_share": classification["A"]["percent"],
        "real_actionable_failures": classification["B"]["count"] + classification["D"]["count"],
        "note": ("A 类为平台审核中（非软件失败）；B/D 类需修数据或人工排查；"
                 "C 类通常被软件预检拦截、不会出现在 publishFail 列表。"),
    }
    report["required_attrs"] = required_attrs
    report["items"] = {
        "publishFail": fail_items,
        "publishing": [_row_summary(row, include_msg=False, classify=False)
                       for row in publishing_rows],
        "waitPublish": [_row_summary(row, include_msg=False, classify=False)
                        for row in wait_rows],
        "draft": [{"id": str(r.get("id") or ""), "title": _short(r.get("title"), 100),
                   "sku": str(r.get("sku") or ""), "categoryId": str(r.get("categoryId") or "")}
                  for r in (draft.get("rows") or [])[:50]],
        "online_sample": [{"id": str(r.get("id") or ""), "title": _short(r.get("title"), 80),
                           "sku": str(r.get("sku") or "")}
                          for r in (online.get("rows") or [])[:10]],
    }

    # 发布结果核验清单：指定 ID + 两个样例 + 当前 publishing/publishFail 关键项
    targets: list[str] = []
    for pid in [*args.product_ids, *SAMPLE_PRODUCT_IDS,
                *[str(row.get("id")) for row in publishing_rows[:3]],
                *[str(row.get("id")) for row in publish_rows[:3]]]:
        pid = str(pid or "").strip()
        if pid and pid not in targets:
            targets.append(pid)
    offline_rows = {state: data["rows"] for state, data in offline.items()}
    checklist = []
    for pid in targets:
        found = locate_product(pid, draft, online, offline_rows)
        found["product_id"] = pid
        checklist.append(conclusion_for(found))
    report["checklist"] = checklist

    # 基本契约检查 + 分类器自检（全部 true → status=PASS）
    counts = report["counts"]
    classifier_selftest_result = classifier_selftest()
    checks = [
        {"name": "userIn code=0", "ok": str(account.get("code")) == "0"},
        {"name": "pageList/draft code=0", "ok": str(draft.get("code")) == "0"},
        {"name": "pageList/online code=0", "ok": str(online.get("code")) == "0"},
    ]
    for state in STATES:
        checks.append({"name": f"offline.json/{state} code=0",
                       "ok": str(offline[state].get("code")) == "0"})
    checks += [
        {"name": "publishFail 全部分类（A+B+C+D == total）",
         "ok": sum(row["count"] for row in classification.values()) == total_fail},
        {"name": "leftStat 与各状态总数一致",
         "ok": all(counts["leftStat_consistency"].values())},
        {"name": "分类器自检 A/B/C/D", "ok": classifier_selftest_result["ok"]},
        {"name": "只读：无写入口调用", "ok": not api.blocked_attempts},
    ]
    report["checks"] = checks
    report["classifier_selftest"] = classifier_selftest_result
    report["safety"].update({
        "request_count": api.request_count,
        "cache_hits": api.cache_hits,
        "blocked_attempts": api.blocked_attempts,
        "write_endpoints_never_called": not api.blocked_attempts,
    })
    report["write_endpoints_never_called"] = not api.blocked_attempts

    failed_checks = [row["name"] for row in checks if not row["ok"]]
    if errors or api.blocked_attempts or failed_checks:
        report["status"] = "ERROR"
        report["error"] = ("；".join(errors) or "安全违规：出现非只读请求尝试"
                           or ("检查失败：" + "；".join(failed_checks)))
        code = 1
    else:
        report["status"] = "PASS"
        code = 0
    return report, code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Amazon 发布状态只读核验与失败原因分类")
    parser.add_argument("--cookie-file", default="", help="Cookie 文件（头格式或 Netscape）")
    parser.add_argument("--json-out", default=str(DEFAULT_OUTPUT), help="JSON 证据路径")
    parser.add_argument("--product-id", default="",
                        help="要生成发布结果核验清单的商品 id，逗号分隔")
    parser.add_argument("--timeout", type=int, default=TIMEOUT, help="单请求超时秒数")
    args = parser.parse_args(argv)
    args.product_ids = [x.strip() for x in str(args.product_id or "").split(",") if x.strip()]

    report, code = build_report(args)
    output = Path(args.json_out)
    _write(output, report)

    print("=" * 72)
    if report["status"] == "SKIP":
        print(f"SKIP: {report['skip_reason']}")
        print(f"报告已写入 {output}")
        return 0
    counts = report["counts"]
    print(f"账号：{report['account'].get('account')} / puid {report['account'].get('puid')} · "
          f"店铺 {report['account'].get('shop_name')}")
    print(f"状态数量：采集箱 {counts.get('draft')} · 待发布 {counts.get('waitPublish')} · "
          f"发布中 {counts.get('publishing')} · 发布失败 {counts.get('publishFail')} · "
          f"在线 {counts.get('online')}")
    print(f"leftStat 一致性：{counts.get('leftStat_consistency')}")
    cls = report["classification"]
    print(f"\n发布失败分类（共 {cls['total_publish_fail']} 个）：")
    for key in CLASS_ORDER:
        row = cls["classes"][key]
        print(f"  {key} {row['count']:>3} 个 ({row['percent']:>5.1f}%) · {row['label']}")
        if row["ids"]:
            print(f"       ids={row['ids'][:6]}{' …' if len(row['ids']) > 6 else ''}")
    if report["required_attrs"]["by_attr"]:
        print("\n必填属性缺失统计（商品数）：")
        for attr, num in list(report["required_attrs"]["by_attr"].items())[:20]:
            print(f"  - {attr}: {num}")
        print("\n哪些商品缺哪些属性：")
        for pid, attrs in report["required_attrs"]["by_product"].items():
            print(f"  - {pid}: {', '.join(attrs)}")
    print("\n发布结果核验清单：")
    for row in report["checklist"]:
        print(f"  - {row['product_id']} [{row['state_label']}] {row.get('conclusion', '')}")
        if row.get("failure_class"):
            print(f"      分类={row['failure_class']}；需检查={row['fields_to_check']}")
            print(f"      下一步：{row.get('action', '')}")
        else:
            print(f"      下一步：{row.get('action', '')}")
    print("\n检查项：")
    for row in report.get("checks") or []:
        print(f"  [{'PASS' if row['ok'] else 'FAIL'}] {row['name']}")
    print(f"\n状态：{report['status']} · 只读请求 {report['safety']['request_count']} 次 · "
          f"拦截 {len(report['safety']['blocked_attempts'])} 次")
    print(f"证据已写入 {output}")
    return code


if __name__ == "__main__":
    sys.exit(main())
